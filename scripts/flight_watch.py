#!/usr/bin/env python3
"""항공권 운임 감시기: 폴링 -> 판정 -> 알림.

수집·판정·알림 전 구간이 순수 코드다. LLM 은 쓰지 않는다.

예) 제주->청주 2026-09-20 17~21시 출발, 10만원 미만이면 텔레그램 알림
    .venv/bin/python scripts/flight_watch.py \
      --dep CJU --arr CJJ --date 20260920 \
      --start-time 1700 --end-time 2100 --max-price 100000 \
      --state-dir state/cju-cjj-20260920 --notify telegram --once
"""
from __future__ import annotations

import argparse
import json
import os
import random
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from env_utils import default_env_candidates, load_env_candidates  # noqa: E402
from naver_collector import Flight, collect  # noqa: E402

HISTORY_LIMIT = 800


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def hhmm(value: str) -> str:
    return f"{value[:2]}:{value[2:]}" if len(value) == 4 else value


def yyyymmdd_kr(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:]}" if len(value) == 8 else value


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------- 판정 (순수)

def parse_until(value: str) -> datetime:
    """감시 종료 시점 YYYYMMDDHHMM 을 로컬 시간대 datetime 으로 바꾼다."""
    return datetime.strptime(value, "%Y%m%d%H%M").astimezone()


def is_expired(until: Optional[datetime], now: datetime) -> bool:
    return until is not None and now >= until


def in_window(flight: Flight, start: str, end: str, *, end_inclusive: bool) -> bool:
    if not flight.dep_time:
        return False
    if flight.dep_time < start:
        return False
    return flight.dep_time <= end if end_inclusive else flight.dep_time < end


def select_window(flights: list[Flight], start: str, end: str, *, end_inclusive: bool) -> list[Flight]:
    return [f for f in flights if in_window(f, start, end, end_inclusive=end_inclusive)]


def select_alerts(
    window_flights: list[Flight],
    max_price: int,
    already_alerted: dict[str, int],
) -> list[Flight]:
    """임계가 미만이면서, 직전 알림가보다 더 싸진 편만 고른다."""
    picked = []
    for f in window_flights:
        if f.total_fare >= max_price:
            continue
        previous = already_alerted.get(f.key())
        if previous is not None and f.total_fare >= previous:
            continue
        picked.append(f)
    return sorted(picked, key=lambda f: f.total_fare)


# ---------------------------------------------------------------- 알림 문구

def format_flight_line(f: Flight) -> str:
    return f"  {hhmm(f.dep_time)} 출발  {f.airline_name} {f.flight_no}  {f.total_fare:,}원"


def build_alert_message(route: str, date: str, max_price: int, flights: list[Flight],
                        previous: dict[str, int]) -> str:
    lines = [f"항공권 가격 알림 - {route} {yyyymmdd_kr(date)}",
             f"{max_price:,}원 미만 {len(flights)}편",
             ""]
    for f in flights:
        line = format_flight_line(f)
        before = previous.get(f.key())
        if before:
            line += f"  (직전 알림 {before:,}원에서 {before - f.total_fare:,}원 하락)"
        lines.append(line)
    return "\n".join(lines)


def build_smoke_message(route: str, date: str, max_price: int, window_flights: list[Flight],
                        start: str, end: str) -> str:
    lines = [f"항공권 감시 시작 - {route} {yyyymmdd_kr(date)}",
             f"감시 조건: {hhmm(start)}~{hhmm(end)} 출발, {max_price:,}원 미만",
             f"현재 해당 시간대 {len(window_flights)}편",
             ""]
    for f in sorted(window_flights, key=lambda x: x.dep_time):
        lines.append(format_flight_line(f))
    if window_flights:
        cheapest = min(window_flights, key=lambda x: x.total_fare)
        gap = cheapest.total_fare - max_price
        lines.append("")
        lines.append(f"현재 최저가 {cheapest.total_fare:,}원 "
                     + (f"(목표까지 {gap:,}원 더 내려가야 함)" if gap > 0 else "- 이미 조건 충족"))
    return "\n".join(lines)


# ---------------------------------------------------------------- 발송

def send_telegram(text: str, chat_id: Optional[str]) -> tuple[bool, str]:
    import requests

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False, "TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 없음"
    try:
        resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                             json={"chat_id": chat_id, "text": text}, timeout=20)
        if resp.status_code == 200:
            return True, "ok"
        return False, f"telegram {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"telegram 예외: {exc}"


def send_openclaw(text: str, target: Optional[str], channel: Optional[str],
                  account: Optional[str]) -> tuple[bool, str]:
    target = target or os.environ.get("OPENCLAW_NOTIFY_TARGET")
    if not target:
        return False, "OPENCLAW_NOTIFY_TARGET 없음"
    cmd = [os.environ.get("OPENCLAW_BIN") or "openclaw", "message", "send",
           "--channel", channel or os.environ.get("OPENCLAW_NOTIFY_CHANNEL") or "telegram",
           "--target", target, "--message", text, "--json"]
    account = account or os.environ.get("OPENCLAW_NOTIFY_ACCOUNT")
    if account:
        cmd += ["--account", account]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as exc:  # noqa: BLE001
        return False, f"openclaw 예외: {exc}"
    detail = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode == 0:
        return True, detail[:300] or "sent"
    return False, detail[:300] or f"openclaw 종료코드 {proc.returncode}"


class Watcher:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.state_dir = Path(args.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_dir / "state.json"
        self.stop_flag = self.state_dir / "stop.flag"
        self.log_path = self.state_dir / "watch.log"
        self.state = read_json(self.state_path)
        self.state.setdefault("alerted", {})
        self.state.setdefault("history", [])
        self.state.setdefault("consecutive_failures", 0)
        self.state.setdefault("smoke_sent", False)
        self._stopping = False
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    # -------------------------------------------------- 기반
    def _handle_signal(self, _signum: int, _frame: Any) -> None:
        self._stopping = True

    def should_stop(self) -> bool:
        return self._stopping or self.stop_flag.exists()

    def log(self, message: str) -> None:
        line = f"[{now_iso()}] {message}"
        print(line, flush=True)
        with self.log_path.open("a", encoding="utf-8") as fp:
            fp.write(line + "\n")

    def save_state(self, **updates: Any) -> None:
        self.state.update(updates)
        self.state["updated_at"] = now_iso()
        atomic_write_json(self.state_path, self.state)

    @property
    def route(self) -> str:
        return f"{self.args.dep}->{self.args.arr}"

    # -------------------------------------------------- 알림
    def notify(self, text: str) -> tuple[bool, str]:
        if self.args.notify == "stdout":
            print(text, flush=True)
            return True, "stdout"
        if self.args.notify == "telegram":
            return send_telegram(text, self.args.telegram_chat_id)
        return send_openclaw(text, self.args.openclaw_target, self.args.openclaw_channel,
                             self.args.openclaw_account)

    # -------------------------------------------------- 1회 수집/판정
    def poll_once(self) -> None:
        a = self.args
        flights, partial = collect(a.dep, a.arr, a.date, adults=a.adults,
                                   headless=a.headless, timeout_seconds=a.collect_timeout,
                                   dump_dir=Path(a.dump_dir) if a.dump_dir else None)
        window = select_window(flights, a.start_time, a.end_time, end_inclusive=not a.end_exclusive)
        cheapest = min((f.total_fare for f in window), default=None)
        cheapest_text = f"{cheapest:,}원" if cheapest is not None else "없음"
        self.log(f"{self.route} {a.date} 전체 {len(flights)}편 / "
                 f"{hhmm(a.start_time)}~{hhmm(a.end_time)} {len(window)}편 / 최저가 {cheapest_text}")
        if partial:
            # 일부 파트너사 응답만 받은 상태. 실제보다 비싸게 보일 수 있어 알림을 놓칠 수는 있으나,
            # 없는 특가를 알리는 오탐은 생기지 않는다. 반복되면 수집 조건을 점검할 것.
            self.log("경고: 부분 수신(isComplete 미도달) - 최저가가 실제보다 높게 잡혔을 수 있음")

        history = self.state["history"]
        history.append({"at": now_iso(), "window_min_fare": cheapest, "partial": partial,
                        "flights": [f.to_dict() for f in window]})
        del history[:-HISTORY_LIMIT]

        alerted: dict[str, int] = self.state["alerted"]

        # 최초 확인 알림은 기본으로 보내지 않는다. 임계가 미만인 편만 알린다.
        if a.smoke_notify and not self.state["smoke_sent"]:
            text = build_smoke_message(self.route, a.date, a.max_price, window, a.start_time, a.end_time)
            ok, detail = self.notify(text)
            self.log(f"최초 확인 알림 발송: ok={ok} {detail}")
            self.state["smoke_sent"] = bool(ok)

        picked = select_alerts(window, a.max_price, alerted)
        if picked:
            text = build_alert_message(self.route, a.date, a.max_price, picked, alerted)
            ok, detail = self.notify(text)
            self.log(f"조건 충족 {len(picked)}편 알림: ok={ok} {detail}")
            if ok:
                for f in picked:
                    alerted[f.key()] = f.total_fare
                self.state["last_notification_at"] = now_iso()
        else:
            self.log(f"조건 미충족 (임계 {a.max_price:,}원)")

        self.save_state(status="running", last_checked_at=now_iso(), last_error=None,
                        consecutive_failures=0, last_window_min_fare=cheapest,
                        last_window=[f.to_dict() for f in window], alerted=alerted,
                        history=history, partial=partial)

    def handle_failure(self, exc: Exception) -> None:
        count = int(self.state.get("consecutive_failures", 0)) + 1
        self.log(f"수집 실패({count}회 연속): {exc}")
        self.save_state(status="error", last_error=str(exc), consecutive_failures=count,
                        last_checked_at=now_iso())
        if self.args.max_consecutive_failures and count == self.args.max_consecutive_failures:
            ok, detail = self.notify(
                f"항공권 감시봇 이상 - {self.route} {yyyymmdd_kr(self.args.date)}\n"
                f"{count}회 연속 수집 실패로 알림이 멈출 수 있습니다.\n마지막 오류: {exc}")
            self.log(f"실패 알림 발송: ok={ok} {detail}")

    # -------------------------------------------------- 만료
    def expire(self, until: datetime) -> int:
        """감시 기간이 끝났다. 더 조회하지 않고 스스로 멈춘다."""
        self.log(f"감시 기간 종료 ({until:%Y-%m-%d %H:%M}) - 더 이상 조회하지 않습니다")
        self.stop_flag.write_text(f"expired at {now_iso()}\n", encoding="utf-8")
        self.save_state(status="expired", expired_at=now_iso())

        label = self.args.unload_launchd_label
        if label and sys.platform == "darwin":
            target = f"gui/{os.getuid()}/{label}"
            proc = subprocess.run(["launchctl", "bootout", target],
                                  capture_output=True, text=True)
            ok = proc.returncode == 0
            self.log(f"launchd 잡 해제 {target}: {'성공' if ok else '실패'} "
                     f"{(proc.stderr or '').strip()[:120]}")
        return 0

    # -------------------------------------------------- 루프
    def run(self) -> int:
        a = self.args
        until = parse_until(a.until) if a.until else None
        self.log(f"감시 시작 {self.route} {yyyymmdd_kr(a.date)} "
                 f"{hhmm(a.start_time)}~{hhmm(a.end_time)}"
                 f"{'(끝 제외)' if a.end_exclusive else '(끝 포함)'} "
                 f"임계 {a.max_price:,}원 / notify={a.notify} / once={a.once}"
                 + (f" / 종료 {until:%Y-%m-%d %H:%M}" if until else ""))
        # 만료 확인이 stop.flag 보다 먼저다. 만료로 남은 stop.flag 때문에
        # 이후 실행이 계속 실패(1)로 끝나면 스케줄러 화면이 빨갛게 뒤덮인다.
        if until is not None and is_expired(until, datetime.now().astimezone()):
            return self.expire(until)
        if self.stop_flag.exists():
            self.log(f"stop.flag 가 있어 시작하지 않습니다: {self.stop_flag}")
            return 1
        while True:
            # 대기 중에 기한이 지날 수 있으므로 매 조회 직전에 확인한다.
            if until is not None and is_expired(until, datetime.now().astimezone()):
                return self.expire(until)

            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001  루프는 어떤 실패에도 살아남아야 한다
                self.handle_failure(exc)

            if a.once or self.should_stop():
                break
            sleep_for = a.poll_seconds + random.randint(0, a.jitter_seconds)
            self.log(f"{sleep_for}초 후 재조회")
            for _ in range(sleep_for):
                if self.should_stop():
                    break
                time.sleep(1)
            if self.should_stop():
                break

        self.save_state(status="stopped")
        self.log("감시 종료")
        return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="항공권 운임 감시 (네이버 국내선)")
    p.add_argument("--dep", required=True, help="출발 공항 코드 (예: CJU)")
    p.add_argument("--arr", required=True, help="도착 공항 코드 (예: CJJ)")
    p.add_argument("--date", required=True, help="출발일 YYYYMMDD")
    p.add_argument("--start-time", default="1700", help="감시 시작 시각 HHMM")
    p.add_argument("--end-time", default="2100", help="감시 종료 시각 HHMM")
    p.add_argument("--end-exclusive", action="store_true",
                   help="종료 시각 정각 출발편을 제외한다 (기본: 포함)")
    p.add_argument("--max-price", type=int, default=100000, help="총액 임계가(원), 미만이면 알림")
    p.add_argument("--adults", type=int, default=1)
    p.add_argument("--state-dir", required=True)
    p.add_argument("--notify", choices=["stdout", "telegram", "openclaw"], default="stdout")
    p.add_argument("--telegram-chat-id")
    p.add_argument("--openclaw-target")
    p.add_argument("--openclaw-channel")
    p.add_argument("--openclaw-account")
    p.add_argument("--poll-seconds", type=int, default=1800)
    p.add_argument("--jitter-seconds", type=int, default=180, help="폴링 간격 랜덤 가산(초)")
    p.add_argument("--once", action="store_true", help="1회만 실행 (cron/launchd 용)")
    p.add_argument("--headless", action="store_true",
                   help="헤드리스로 실행. 네이버가 검색을 실행하지 않을 수 있으므로 권장하지 않음")
    p.add_argument("--collect-timeout", type=int, default=90)
    p.add_argument("--dump-dir", help="원본 SSE/스크린샷 저장 경로 (디버그용)")
    p.add_argument("--until",
                   help="감시 종료 시점 YYYYMMDDHHMM (실행 환경의 지역 시간 기준). "
                        "지나면 더 조회하지 않고 스스로 멈춘다. "
                        "해외 서버라면 TZ=Asia/Seoul 을 함께 지정할 것")
    p.add_argument("--unload-launchd-label",
                   help="만료 시 해제할 launchd 라벨 (macOS). 예: com.flightwatch.mywatch")
    p.add_argument("--smoke-notify", action="store_true",
                   help="최초 1회는 임계가와 무관하게 현재 시세를 보낸다 (기본: 보내지 않음)")
    p.add_argument("--max-consecutive-failures", type=int, default=3,
                   help="연속 실패가 이 횟수에 도달하면 봇 이상을 알린다. 0 이면 알리지 않는다")
    p.add_argument("--secrets-path", help="추가로 읽을 .env 경로")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    candidates = list(default_env_candidates())
    if args.secrets_path:
        candidates.insert(0, Path(args.secrets_path))
    load_env_candidates(candidates)
    return Watcher(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
