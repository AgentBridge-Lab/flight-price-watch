"""네이버 항공권 국내선 운임 수집기.

flight.naver.com 은 Next.js SPA 이고, 운임은 검색 버튼 클릭 후
POST https://flight-api.naver.com/flight/domestic/searchFlights 가
SSE(text/event-stream) 로 파트너사별 결과를 순차 전송한다.

- `requests` 로는 접근 불가: flight-api.naver.com 은 비브라우저 클라이언트에 503 을 반환한다 (2026-08-04 실측).
- URL 로 진입만 하면 검색이 자동 실행되지 않는다. 검색 버튼을 실제로 눌러야 한다 (2026-08-04 실측).

수집(fetch_search_events)과 파싱(parse_flights)을 분리해 파싱은 순수 함수로 테스트한다.
"""
from __future__ import annotations

import codecs
import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

SEARCH_API_PATH = "/flight/domestic/searchFlights"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")
PRICE_RE = re.compile(r"\d{1,3},\d{3}\s*원")


@dataclass(frozen=True)
class Flight:
    flight_no: str        # "RF613"
    airline_code: str     # "RF"
    airline_name: str     # "에어로케이"
    dep_airport: str      # "CJJ"
    arr_airport: str      # "CJU"
    dep_date: str         # "20260920"
    dep_time: str         # "1835"
    arr_time: str         # "1945"
    total_fare: int       # 총액(운임+유류할증료+공항시설사용료+발권수수료), 성인 1인 편도
    seat_class: str       # L(특가)/D(할인)/Y(일반)/C(비즈니스)

    def key(self) -> str:
        return f"{self.dep_date}-{self.flight_no}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CollectError(RuntimeError):
    pass


def build_url(dep: str, arr: str, date: str, adults: int = 1) -> str:
    return f"https://flight.naver.com/flights/domestic/{dep}-{arr}-{date}?adult={adults}&fareType=Y"


# ---------------------------------------------------------------- 파싱 (순수)

def _c1_passthrough(exc: UnicodeError):
    """cp1252 에 정의되지 않은 바이트가 C1 제어문자로 남은 것을 원래 바이트로 되돌린다."""
    chunk = exc.object[exc.start:exc.end]  # type: ignore[attr-defined]
    if all(ord(c) < 0x100 for c in chunk):
        return bytes(ord(c) for c in chunk), exc.end  # type: ignore[attr-defined]
    raise exc


codecs.register_error("cp1252_c1", _c1_passthrough)


def repair_mojibake(text: str) -> str:
    """UTF-8 본문이 cp1252 를 거쳐 이중 인코딩된 경우를 복원한다.

    Playwright 의 Response.body() 가 이 형태로 본문을 넘겨준다 (2026-08-04 실측).
    '제'(EC A0 9C) 가 'ì œ'(C3AC C2A0 C593) 로 온다.
    cp1252 에 없는 바이트(0x90 등)는 같은 값의 C1 제어문자로 남아 있어 별도 처리한다.
    정상 UTF-8 한글은 cp1252 로 인코딩되지 않으므로 원본을 그대로 돌려준다.
    """
    try:
        return text.encode("cp1252", errors="cp1252_c1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def parse_sse_events(raw: bytes | str) -> list[dict[str, Any]]:
    """SSE 본문에서 `data: {...}` 이벤트만 추출한다."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    text = repair_mojibake(text)
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return events


def pick_final_event(events: list[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], bool]:
    """완료(isComplete) 이벤트를 고른다. 없으면 마지막 이벤트 + partial 플래그."""
    complete = [e for e in events if e.get("status", {}).get("isComplete")]
    if complete:
        return complete[-1], False
    if events:
        return events[-1], True
    return None, True


def _fare_total(fare: dict[str, Any]) -> Optional[int]:
    """총액. adultTotalFare 가 정본이고, 없으면 구성요소를 합산한다."""
    total = fare.get("adultTotalFare")
    if isinstance(total, int) and total > 0:
        return total
    base = fare.get("adultFare")
    if not isinstance(base, int) or base <= 0:
        return None
    return base + fare.get("aFuel", 0) + fare.get("aTax", 0) + fare.get("publishFee", 0)


def parse_flights(events: list[dict[str, Any]]) -> tuple[list[Flight], bool]:
    """SSE 이벤트 -> 편명 단위 최저 총액 목록. (flights, partial) 반환.

    같은 편명이 좌석등급(L/D/Y/C)별로 여러 레코드로 오므로 편명 기준 최저가로 집계한다.
    """
    event, partial = pick_final_event(events)
    if event is None:
        return [], True

    airline_names: dict[str, str] = event.get("status", {}).get("airlinesCodeMap", {}) or {}
    best: dict[str, Flight] = {}

    for item in event.get("flights", []) or []:
        seg = item.get("segment") or {}
        dep, arr = seg.get("departure") or {}, seg.get("arrival") or {}
        airline_code = seg.get("airlineCode") or ""
        number = seg.get("flightNumber") or ""
        if not airline_code or not number or not dep.get("time"):
            continue

        fares = item.get("fares") or []
        totals = [t for t in (_fare_total(f) for f in fares) if t]
        min_fare = item.get("minFare")
        if isinstance(min_fare, int) and min_fare > 0:
            totals.append(min_fare)
        if not totals:
            continue

        flight = Flight(
            flight_no=f"{airline_code}{number}",
            airline_code=airline_code,
            airline_name=airline_names.get(airline_code, airline_code),
            dep_airport=dep.get("airportCode", ""),
            arr_airport=arr.get("airportCode", ""),
            dep_date=dep.get("date", ""),
            dep_time=dep.get("time", ""),
            arr_time=arr.get("time", ""),
            total_fare=min(totals),
            seat_class=item.get("seatClass", ""),
        )
        prev = best.get(flight.key())
        if prev is None or flight.total_fare < prev.total_fare:
            best[flight.key()] = flight

    return sorted(best.values(), key=lambda f: f.dep_time), partial


# ---------------------------------------------------------------- 수집 (브라우저)

def fetch_search_events(
    dep: str,
    arr: str,
    date: str,
    *,
    adults: int = 1,
    headless: bool = False,
    timeout_seconds: int = 90,
    dump_dir: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """실브라우저로 검색을 실행하고 searchFlights SSE 이벤트를 모은다."""
    from playwright.sync_api import sync_playwright

    # SSE 응답 객체는 모아만 두고 body() 는 스트림이 끝난 뒤 읽는다.
    # 스트리밍 도중 body() 를 호출하면 블로킹/부분수신으로 빈 본문을 받는다.
    responses: list[Any] = []
    bodies: list[bytes] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--lang=ko-KR"],
        )
        try:
            ctx = browser.new_context(
                locale="ko-KR", timezone_id="Asia/Seoul", user_agent=UA,
                viewport={"width": 1512, "height": 1000},
            )
            ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
            page = ctx.new_page()

            def on_response(resp):
                if SEARCH_API_PATH in resp.url:
                    responses.append(resp)

            page.on("response", on_response)
            page.goto(build_url(dep, arr, date, adults), wait_until="domcontentloaded", timeout=60_000)
            time.sleep(5)

            # URL 진입만으로는 검색이 실행되지 않는다. 검색 버튼을 눌러야 한다.
            clicked = False
            for selector in ("button:has-text('검색')", "a:has-text('검색')"):
                element = page.query_selector(selector)
                if element and element.is_visible():
                    element.click()
                    clicked = True
                    break
            if not clicked:
                raise CollectError("검색 버튼을 찾지 못했습니다 (페이지 구조 변경 가능성)")

            # 운임이 화면에 렌더되면 SSE 스트림도 끝난 상태다.
            deadline = time.time() + timeout_seconds
            while time.time() < deadline:
                time.sleep(2)
                if len(PRICE_RE.findall(page.inner_text("body"))) >= 3:
                    time.sleep(3)
                    break

            for resp in responses:
                try:
                    bodies.append(resp.body())
                except Exception:  # noqa: BLE001  본문을 못 읽는 응답은 건너뛴다
                    continue

            if dump_dir is not None:
                dump_dir.mkdir(parents=True, exist_ok=True)
                (dump_dir / "sse_raw.txt").write_bytes(b"\n".join(bodies))
                page.screenshot(path=str(dump_dir / "page.png"), full_page=True)
        finally:
            browser.close()

    if not bodies:
        raise CollectError("searchFlights 응답 본문을 받지 못했습니다")
    return parse_sse_events(b"\n".join(bodies))


def collect(
    dep: str,
    arr: str,
    date: str,
    *,
    adults: int = 1,
    headless: bool = False,
    timeout_seconds: int = 90,
    dump_dir: Optional[Path] = None,
) -> tuple[list[Flight], bool]:
    events = fetch_search_events(
        dep, arr, date, adults=adults, headless=headless,
        timeout_seconds=timeout_seconds, dump_dir=dump_dir,
    )
    flights, partial = parse_flights(events)
    if not flights:
        raise CollectError(f"운임을 한 건도 파싱하지 못했습니다 (events={len(events)})")
    return flights, partial


if __name__ == "__main__":  # 수동 점검용
    import argparse

    ap = argparse.ArgumentParser(description="네이버 국내선 운임 1회 조회")
    ap.add_argument("dep")
    ap.add_argument("arr")
    ap.add_argument("date")
    ap.add_argument("--adults", type=int, default=1)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--dump-dir")
    a = ap.parse_args()

    got, is_partial = collect(
        a.dep, a.arr, a.date, adults=a.adults, headless=a.headless,
        dump_dir=Path(a.dump_dir) if a.dump_dir else None,
    )
    print(f"{a.dep}->{a.arr} {a.date}  {len(got)}편  partial={is_partial}")
    for f in got:
        print(f"  {f.dep_time[:2]}:{f.dep_time[2:]}  {f.flight_no:8} {f.airline_name:8} {f.total_fare:>9,}원  [{f.seat_class}]")
