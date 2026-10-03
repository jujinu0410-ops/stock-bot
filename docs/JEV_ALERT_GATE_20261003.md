# JEV_ALERT_GATE 운영 기준 — 2026-10-03

## 목적

Jev는 StockBot의 새 매수신호를 만드는 모델이 아니다. 기존 기술·수급·공시·뉴스 규칙이 이미 **메일 후보**로 올린 건에 대해, 사용자에게 지금 통지할 가치가 있는지를 구조화해 판정하는 최종 발송 게이트다.

자동주문, 포트폴리오 변경, Google Sheet 매수·매도 기록은 수행하지 않는다.

## 단계별 적용

| 대상 | 최초 모드 | 실제 역할 |
|---|---|---|
| YouTube 언급 매수후보 | `ACTIVE` | `/confirm`의 기존 Kiwoom·DART·뉴스 확인 뒤, Jev가 `HOLD`면 메일을 보내지 않는다. |
| 보유종목 5분 감시 | `SHADOW` | 기존 메일을 그대로 보내고 Jev 판정만 `JEV_GATE_LOG`에 기록한다. |
| ETF 5분 감시 | `SHADOW` | 기존 2회 확인 메일을 그대로 보내고 Jev 판정을 기록한다. |

보유종목 또는 ETF를 Active로 바꾸려면 Cloud Run 환경변수 `JEV_ALERT_GATE_HELD_MODE=ACTIVE` 또는 `JEV_ALERT_GATE_ETF_MODE=ACTIVE`를 설정한다. 공통 기본값은 `JEV_ALERT_GATE_MODE=SHADOW`다.

## Active 발송 정책

Jev API가 정상 응답한 경우에만 비중대 후보를 보류할 수 있다.

- 상승·일반 후보: `send_alert_now >= 0.70` 및 `alert_urgency >= 2.0`이면 발송
- 하락 위험 후보: 방향이 `DOWN`, `signal_deteriorating >= 0.70`, 긴급도 1 이상이면 발송
- 손절선 최초 침범: Jev 판정과 무관하게 즉시 발송
- Jev API 키 누락, HTTP 오류, 시간초과, 형식 오류: 기존 규칙대로 발송(fail-open)

초기 임계값은 운영 시작값일 뿐 정답이 아니다. `JEV_GATE_LOG`에 최소 50~100건을 쌓은 뒤, `SEND/HOLD`와 후속 1·3·5일 성과 및 실제 유용성을 함께 비교해 조정한다.

## Jev 입력과 출력

Cloud Run만 `JEV_API_KEY`를 Secret으로 보관한다. Apps Script와 Google Sheet에는 Jev 키를 저장하지 않는다.

입력에는 기존 후보의 종목명·코드, 이벤트 키, 가격·등락률·ATR·VWAP·OBV·국면, 그리고 기존 Cloud Run이 이미 조회한 수급·공시·뉴스 요약만 들어간다.

Jev는 한 요청에서 다음을 반환한다.

- `signal_direction`: `UP` / `DOWN` / `NEUTRAL`
- `signal_stage`: `NONE` / `DEVELOPING` / `BUY_WATCH` / `BUY_CONFIRMED`
- `alert_urgency`: 0~4 기대점수
- `send_alert_now`: 지금 알릴 확률
- `signal_deteriorating`: 훼손·위험 확률

## 배포와 최초 확인

1. Windows의 기존 `.env`에 `JEV_API_KEY=<발급키>`를 추가한다. 키는 채팅·Sheet·GitHub에 붙여넣지 않는다.
2. `scripts/deploy_youtube_signal_service.ps1`을 실행한다. 이 스크립트는 Secret Manager에 `youtube-jev-api-key`를 만들고, YouTube=Active / 공통=Shadow 환경변수로 Cloud Run을 재배포한다.
3. Apps Script 프로젝트의 기존 `HeldMonitor5m_v1.gs`는 `apps_script/held_monitor_5m_v3.gs` 전체로 교체하고, 기존 ETF 파일은 `apps_script/etf_watch_addon.gs` 전체로 교체한다. 새 트리거는 설치하지 않는다.
4. `hmCheckContextApiHealth()`가 200이고 `/jev-gate`가 health 응답 endpoint 목록에 보이면 연결 완료다.
5. YouTube 실제 후보 한 건의 결과에서 `mail_sent=true` 또는 `suppressed_by_jev=true`와 `jev_gate`를 확인한다. 보유·ETF는 첫 메일 뒤 `JEV_GATE_LOG` 탭이 생성되는지 확인한다.

## 운영상 주의

- Jev의 `HOLD`는 매수 부정이나 종목 배제가 아니라, **그 시점의 메일 보류**다.
- YouTube 후보는 한 번 보류된 뒤에도 후보 TTL·기술 조건에 따라 다음 유의미한 새 신호에서 다시 평가된다.
- 하락·손절 위험 알림은 억제보다 누락 방지가 우선이다.
- 보유종목·ETF의 API 비용·입력 토큰은 `JEV_GATE_LOG`의 `InputTokens`에 기록한다. YouTube는 Cloud Run 응답의 `jev_gate.usage`과 서비스 로그에 남는다.
