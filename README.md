# flight-price-watch

국내선 항공권 값이 원하는 금액 아래로 떨어지면 텔레그램으로 알려주는 봇입니다.

> 예: "9월 20일 제주→청주, 저녁 5~9시 출발편이 10만원 밑으로 내려가면 알려줘"

![데모](docs/demo-search.gif)

---

## 뭐 하는 건가요

30분마다 네이버 항공권에서 해당 날짜 운임을 가져와서, 세 가지를 확인하고 알림을 보냅니다.

1. 내가 원하는 **시간대**에 출발하는가
2. 총액이 **정한 금액 미만**인가
3. 전에 알린 편이면, **그때보다 더 싸졌는가**

3번 덕분에 같은 가격으로 알림이 도배되지 않습니다.
99,000원에 한 번 알리고, 계속 99,000원이면 조용합니다. 97,000원이 되면 다시 알립니다.

**조건에 맞을 때만 알림이 옵니다.** 평소에는 로그만 쌓이고 아무것도 오지 않습니다.

---

## 빠른 시작

### 1. 설치

```bash
git clone https://github.com/AgentBridge-Lab/flight-price-watch.git
cd flight-price-watch

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

### 2. 텔레그램 설정

[@BotFather](https://t.me/BotFather)에서 봇을 만들어 토큰을 받습니다.
그 봇에게 아무 메시지나 하나 보낸 뒤, 아래 주소를 열면 `chat id`가 보입니다.

```
https://api.telegram.org/bot<봇토큰>/getUpdates
```

둘을 설정 파일에 넣습니다.

```bash
mkdir -p ~/.config/flight-price-watch && chmod 700 ~/.config/flight-price-watch
cp .env.example ~/.config/flight-price-watch/.env
chmod 600 ~/.config/flight-price-watch/.env
# 편집기로 열어 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 를 채우세요
```

### 3. 실행

```bash
.venv/bin/python scripts/flight_watch.py \
  --dep CJU --arr CJJ --date 20260920 \
  --start-time 1700 --end-time 2100 \
  --max-price 100000 \
  --state-dir state/my-watch \
  --notify telegram
```

> **제주(CJU) → 청주(CJJ), 2026년 9월 20일, 17~21시 출발, 10만원 미만이면 알림**

끝입니다. 30분마다 알아서 확인합니다. 멈추려면 `Ctrl+C` 를 누르거나 `state/my-watch/stop.flag` 파일을 만드세요.

### 언제까지만 감시할지 정하기

비행기가 뜨고 나서도 계속 조회하면 곤란하겠죠. `--until` 로 종료 시점을 정하면 그때 스스로 멈춥니다.

```bash
  --until 202609202000    # 2026년 9월 20일 20시가 지나면 종료
```

> `--until` 은 **실행하는 컴퓨터의 지역 시간**으로 해석합니다.
> 시간대가 한국이 아닌 곳에서 돌린다면 `TZ=Asia/Seoul` 을 함께 지정하세요.
> 안 하면 종료 시각이 그만큼 어긋납니다.

만료되면 더 이상 조회하지 않고 `stop.flag` 를 남긴 뒤 종료합니다.
스케줄러에 등록해뒀다면 `--unload-launchd-label` 로 **잡까지 스스로 해제**하게 할 수 있습니다.

```bash
  --until 202609202000 --unload-launchd-label com.flightwatch.mywatch
```

---

## 알림은 이렇게 옵니다

```
항공권 가격 알림 - CJU->CJJ 2026-09-20
100,000원 미만 2편

  17:10 출발  제주항공 7C226  96,800원  (직전 알림 99,500원에서 2,700원 하락)
  18:00 출발  티웨이항공 TW844  98,300원
```

먼저 잘 돌아가는지 눈으로 보고 싶다면 `--notify stdout` 을 주세요. 터미널에 그대로 찍힙니다.

```
$ .venv/bin/python scripts/naver_collector.py CJU CJJ 20260920

CJU->CJJ 20260920  25편  partial=False
  07:20  KE1704   대한항공     59,500원  [L]
  07:50  ZE712    이스타항공    48,900원  [D]
  ...
  17:10  7C226    제주항공    115,800원  [Y]
  18:00  TW844    티웨이항공   116,800원  [Y]
```

---

## 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--dep` / `--arr` | 필수 | 출발·도착 공항 코드 (`CJU`, `CJJ`, `GMP`, `PUS` …) |
| `--date` | 필수 | 출발일 `YYYYMMDD` |
| `--start-time` / `--end-time` | `1700` / `2100` | 출발시각 범위 `HHMM` |
| `--end-exclusive` | 꺼짐 | 종료 시각 정각 출발편 제외 (기본은 21:00 출발도 포함) |
| `--max-price` | `100000` | 이 금액 **미만**이면 알림. 정확히 10만원은 보내지 않음 |
| `--adults` | `1` | 성인 인원 |
| `--notify` | `stdout` | `stdout` / `telegram` / `openclaw` |
| `--poll-seconds` | `1800` | 조회 간격(초) |
| `--once` | 꺼짐 | 한 번만 실행하고 종료 (cron·launchd 용) |
| `--until` | 없음 | 감시 종료 시점 `YYYYMMDDHHMM`. 지나면 스스로 멈춤 |
| `--unload-launchd-label` | 없음 | 만료 시 해제할 launchd 라벨 (macOS) |
| `--smoke-notify` | 꺼짐 | 최초 1회는 금액과 무관하게 현재 시세 발송 (동작 확인용) |
| `--max-consecutive-failures` | `3` | 연속 실패 시 "봇 이상" 알림. `0` 이면 끔 |
| `--dump-dir` | 없음 | 원본 응답·스크린샷 저장 (문제 생겼을 때) |

공항 코드: 제주 `CJU` · 청주 `CJJ` · 김포 `GMP` · 김해(부산) `PUS` · 대구 `TAE` · 광주 `KWJ` · 여수 `RSU`

---

## 계속 켜두기

노트북을 계속 띄워두는 대신, 스케줄러에 맡기는 편이 안전합니다. 재부팅해도 알아서 돌아옵니다.

| 방법 | 비용 | 컴퓨터를 켜둬야 하나 | 비고 |
|---|---|---|---|
| **macOS launchd** | 무료 | 예 | 30분마다 브라우저 창이 잠깐 뜸 |
| **Linux cron** | 서버 비용 | 아니오 | `xvfb-run` 으로 창 없이. 가장 안정적 |

### macOS (launchd)

`~/Library/LaunchAgents/com.flightwatch.mywatch.plist` 를 만듭니다.
경로는 본인 것으로 바꾸세요.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.flightwatch.mywatch</string>
  <key>ProgramArguments</key><array>
    <string>/path/to/flight-price-watch/.venv/bin/python</string>
    <string>/path/to/flight-price-watch/scripts/flight_watch.py</string>
    <string>--dep</string><string>CJU</string>
    <string>--arr</string><string>CJJ</string>
    <string>--date</string><string>20260920</string>
    <string>--start-time</string><string>1700</string>
    <string>--end-time</string><string>2100</string>
    <string>--max-price</string><string>100000</string>
    <string>--state-dir</string><string>/path/to/flight-price-watch/state/my-watch</string>
    <string>--notify</string><string>telegram</string>
    <string>--until</string><string>202609202000</string>
    <string>--unload-launchd-label</string><string>com.flightwatch.mywatch</string>
    <string>--once</string>
  </array>
  <key>WorkingDirectory</key><string>/path/to/flight-price-watch</string>
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>/path/to/flight-price-watch/logs/launchd.err</string>
</dict></plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.flightwatch.mywatch.plist
launchctl list | grep flightwatch    # 두 번째 값이 마지막 종료코드 (0이면 정상)
launchctl unload ~/Library/LaunchAgents/com.flightwatch.mywatch.plist   # 중지
```

> launchd 는 PATH 가 최소화되어 있습니다. `openclaw` 같은 외부 명령을 쓴다면
> `EnvironmentVariables` 에 절대경로를 넣어야 알림이 나갑니다.
>
> 맥이 잠들면 실행되지 않고, 놓친 회차는 깨어날 때 한 번으로 합쳐집니다.
> 실제 간격은 30분보다 불규칙합니다.

### Linux (cron)

리눅스 서버에는 화면이 없으므로 `xvfb-run` 으로 가상 디스플레이를 붙입니다.

```cron
*/30 * * * * cd /path/to/flight-price-watch && xvfb-run -a .venv/bin/python scripts/flight_watch.py --dep CJU --arr CJJ --date 20260920 --max-price 100000 --state-dir state/my-watch --notify telegram --once >> logs/cron.log 2>&1
```

---

## 왜 브라우저를 띄우나요

가장 궁금해하실 부분입니다. `requests` 몇 줄이면 될 것 같은데 크롬을 통째로 띄우는 데는 이유가 있습니다.
아래는 전부 2026-08-04에 직접 확인한 내용입니다.

**1. 일반 HTTP 요청은 막힙니다**

운임 API인 `flight-api.naver.com` 은 브라우저가 아닌 클라이언트에 대해
User-Agent·Referer·Origin 을 다 붙여도 **경로와 무관하게 503** 을 반환합니다.
`/` 조차 열리지 않습니다. 헤더로는 넘을 수 없습니다.

**2. 주소만 열어서는 검색이 실행되지 않습니다**

`flight.naver.com/flights/domestic/CJU-CJJ-20260920` 로 들어가면
출발지·도착지·날짜가 채워진 화면이 나오지만, 운임 요청은 나가지 않습니다.
**검색 버튼을 실제로 눌러야** 합니다.

**3. 헤드리스로는 아무것도 못 받습니다**

`--headless` 로 띄우면 검색 버튼을 눌러도 응답이 **한 건도** 오지 않습니다.
그래서 화면이 있는 환경이 필요합니다. (리눅스는 `xvfb-run`)

**4. 대신 데이터는 깨끗하게 옵니다**

검색이 실행되면 `POST /flight/domestic/searchFlights` 가
SSE(`text/event-stream`)로 여행사별 결과를 순차 전송합니다.
화면 글자를 읽는 게 아니라 이 JSON 을 가로채기 때문에, 디자인이 바뀌어도 잘 버팁니다.

```jsonc
{
  "status": { "isComplete": true, "airlinesCodeMap": { "7C": "제주항공" } },
  "flights": [{
    "segment": { "airlineCode": "7C", "flightNumber": "226",
                 "departure": { "airportCode": "CJU", "time": "1710" } },
    "minFare": 115800,              // ← 이 값을 씁니다
    "fares": [ { "adultTotalFare": 115800, "agtCode": "..." }, ... ]
  }]
}
```

**가격은 어떻게 정하나** — `minFare`(= 여행사별 `adultTotalFare` 중 최저가)를 씁니다.
**성인 1인 편도 총액**이고 운임 + 유류할증료 + 공항시설사용료 + 발권수수료가 모두 포함된,
네이버 화면에 표시되는 그 금액입니다. 카드사 할인가는 제외합니다.

같은 항공편이 좌석등급(특가 `L` / 할인 `D` / 일반 `Y` / 비즈니스 `C`)별로 여러 건 오므로,
**편명 하나당 최저가 1건**으로 묶습니다.

---

## 구조

파일은 셋뿐입니다.

```
scripts/
  naver_collector.py   크롬 띄워 운임 가져오기 (수집 / 파싱 분리)
  flight_watch.py      판정 + 알림 + 상태 관리
  env_utils.py         .env 읽기
```

한 번의 조회는 이렇게 흘러갑니다.

```
① 수집    크롬 실행 → 검색 클릭 → SSE 가로채기 → 편명별 최저가
② 판정    시간대 필터 → 금액 필터 → 직전 알림가와 비교
③ 알림    조건에 맞는 편만 텔레그램 발송
④ 기록    state.json 에 가격 추이와 알림 이력 저장
```

**LLM 은 쓰지 않습니다.** 운임이 안정적인 JSON 으로 오기 때문에 LLM 파서가 필요 없고,
가격 비교를 LLM 에 맡길 이유도 없습니다. 전부 평범한 파이썬 코드입니다.

### 상태 파일

`--state-dir` 아래에 생깁니다.

| 파일 | 내용 |
|---|---|
| `state.json` | 마지막 조회 시각, 가격 추이(`history`), 알림 이력(`alerted`), 오류 |
| `watch.log` | 조회 기록. `tail -f` 로 진행 상황을 볼 수 있습니다 |
| `stop.flag` | 이 파일을 만들면 루프가 멈춥니다 |

---

## 테스트

```bash
.venv/bin/python -m pytest tests/ -q
```

시간대·금액 경계값(16:59 / 17:00 / 21:00, 99,999원 / 100,000원)과 중복 알림 방지를 확인하고,
실제 응답을 저장해둔 fixture 로 파서 회귀를 검사합니다. 네이버가 응답 구조를 바꾸면 테스트가 먼저 깨집니다.

---

## 잘 안 될 때

| 증상 | 확인할 것 |
|---|---|
| 운임을 못 가져옴 | `--dump-dir logs/debug` 로 실행 후 스크린샷 확인. 헤드리스로 돌리고 있진 않은지 |
| 알림이 안 옴 | 조건 미충족일 수 있습니다. `watch.log` 의 최저가를 먼저 보세요 |
| 토큰을 넣었는데 무시됨 | `.env` 에 `your_..._here` 같은 예시 값이 남아 있으면 무시됩니다 |
| 리눅스에서 실패 | `xvfb-run -a` 를 붙였는지 확인 |

---

## 주의

- 네이버가 만들거나 인가한 도구가 **아닙니다.** 응답 구조는 예고 없이 바뀔 수 있고,
  이 저장소를 통해 이루어지는 모든 활동의 책임은 사용자에게 있습니다.
- 조회 간격을 과도하게 줄이지 마세요. 국내선 운임은 분 단위로 움직이지 않습니다.
  기본값 30분이면 충분합니다.
- 예약이나 결제 기능은 없습니다. 가격 알림만 합니다.
- `--until` 을 쓰지 않으면 여행 날짜가 지나도 계속 조회합니다. 잊지 말고 지정하세요.

## 라이선스

MIT
