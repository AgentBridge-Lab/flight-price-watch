"""판정 로직 경계값 테스트. 브라우저 없이 순수 함수만 검증한다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from flight_watch import select_alerts, select_window  # noqa: E402
from naver_collector import Flight, parse_flights, parse_sse_events, repair_mojibake  # noqa: E402


def make(dep_time: str, fare: int, flight_no: str = "7C226") -> Flight:
    return Flight(flight_no=flight_no, airline_code=flight_no[:2], airline_name="제주항공",
                  dep_airport="CJU", arr_airport="CJJ", dep_date="20260920",
                  dep_time=dep_time, arr_time="1810", total_fare=fare, seat_class="Y")


# ------------------------------------------------------------ 시간 창 경계값

@pytest.mark.parametrize("dep_time,expected", [
    ("1659", False),  # 창 시작 1분 전
    ("1700", True),   # 창 시작 정각
    ("1900", True),
    ("2059", True),
    ("2100", True),   # 기본은 종료 정각 포함
    ("2101", False),
])
def test_window_inclusive_end(dep_time, expected):
    flights = [make(dep_time, 90000)]
    assert bool(select_window(flights, "1700", "2100", end_inclusive=True)) is expected


@pytest.mark.parametrize("dep_time,expected", [
    ("1700", True),
    ("2059", True),
    ("2100", False),  # --end-exclusive 면 종료 정각 제외
])
def test_window_exclusive_end(dep_time, expected):
    flights = [make(dep_time, 90000)]
    assert bool(select_window(flights, "1700", "2100", end_inclusive=False)) is expected


# ------------------------------------------------------------ 가격 경계값

@pytest.mark.parametrize("fare,expected", [
    (99_999, True),    # 임계 미만 -> 알림
    (100_000, False),  # 임계 정각 -> 알림 없음 ("10만원 아래" 이므로 미만만)
    (100_001, False),
])
def test_price_threshold(fare, expected):
    picked = select_alerts([make("1800", fare)], 100_000, {})
    assert bool(picked) is expected


# ------------------------------------------------------------ 중복 알림 방지

def test_no_realert_at_same_price():
    flights = [make("1800", 95_000)]
    assert select_alerts(flights, 100_000, {"20260920-7C226": 95_000}) == []


def test_no_realert_when_price_rises():
    flights = [make("1800", 97_000)]
    assert select_alerts(flights, 100_000, {"20260920-7C226": 95_000}) == []


def test_realert_when_price_drops_further():
    flights = [make("1800", 91_000)]
    picked = select_alerts(flights, 100_000, {"20260920-7C226": 95_000})
    assert [f.total_fare for f in picked] == [91_000]


def test_alerts_sorted_by_price():
    flights = [make("1800", 98_000, "7C226"), make("1900", 81_000, "TW844"),
               make("2000", 90_000, "LJ410")]
    assert [f.total_fare for f in select_alerts(flights, 100_000, {})] == [81_000, 90_000, 98_000]


# ------------------------------------------------------------ 파서 (실응답 회귀)

FIXTURE = ROOT / "tests" / "fixtures" / "searchflights_cju_cjj_20260920.sse"


@pytest.mark.skipif(not FIXTURE.exists(), reason="실응답 fixture 없음")
def test_parse_real_response():
    flights, partial = parse_flights(parse_sse_events(FIXTURE.read_bytes()))
    assert partial is False
    assert len(flights) >= 20
    assert all(f.dep_airport == "CJU" and f.arr_airport == "CJJ" for f in flights)
    assert all(30_000 < f.total_fare < 1_000_000 for f in flights)
    # 편명은 중복 없이 최저가 1건으로 집계된다
    assert len({f.flight_no for f in flights}) == len(flights)
    # 한글 항공사명이 깨지지 않아야 한다
    assert any(f.airline_name == "제주항공" for f in flights)


def test_repair_mojibake_is_lossless_for_valid_text():
    for text in ["대한항공", "ASCII only", "제주항공/에어로케이", '{"a": 1}']:
        assert repair_mojibake(text) == text


def test_parse_ignores_records_without_fare():
    event = {"status": {"isComplete": True, "airlinesCodeMap": {"7C": "제주항공"}},
             "flights": [{"itineraryId": "x", "seatClass": "Y", "fares": [],
                          "segment": {"airlineCode": "7C", "flightNumber": "226",
                                      "departure": {"airportCode": "CJU", "date": "20260920", "time": "1710"},
                                      "arrival": {"airportCode": "CJJ", "time": "1810"}}}]}
    flights, _ = parse_flights([event])
    assert flights == []


def test_parse_falls_back_to_fare_components():
    event = {"status": {"isComplete": True, "airlinesCodeMap": {"7C": "제주항공"}},
             "flights": [{"itineraryId": "x", "seatClass": "Y",
                          "fares": [{"adultFare": 60000, "aFuel": 16500, "aTax": 4000, "publishFee": 1000}],
                          "segment": {"airlineCode": "7C", "flightNumber": "226",
                                      "departure": {"airportCode": "CJU", "date": "20260920", "time": "1710"},
                                      "arrival": {"airportCode": "CJJ", "time": "1810"}}}]}
    flights, _ = parse_flights([event])
    assert [f.total_fare for f in flights] == [81_500]


def test_parse_sse_skips_malformed_lines():
    body = 'data: {"status":{"isComplete":true},"flights":[]}\ndata: not-json\n\n'
    assert len(parse_sse_events(body)) == 1
