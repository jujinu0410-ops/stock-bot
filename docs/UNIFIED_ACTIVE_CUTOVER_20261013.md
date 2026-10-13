# 주식감시 3시트 단일 엔진 — 운영 전환 검증 (2026-10-13)

## 고정 원칙
- 보유종목·ETF·유튜브 언급종목은 각각의 시트를 그대로 유지한다.
- 기술 판정은 공통 Cloud Run `/unified-scan`, 발송·이력·복기는 Apps Script 공통 실행기로 통합한다.
- 키움 실시간 확인 없는 매수 알림은 보내지 않는다. 손절/OBV15/급락 위험경보를 Jev가 차단하지 않는다.
- 종목별 원본 수식과 자동주문은 변경하지 않는다.

## 확정 운영 상태 (KST 2026-10-13 13:57)
- `UNIFIED_CONTROL!B2=ACTIVE`, `STATUS=ACTIVE_COMPLETE`, `HEALTH=OK`.
- 활성 목록: 보유 5 + ETF 30 + YouTube 170, 중복 4, **고유 201종목**.
- 첫 실제 예약실행: `universe=201, evaluated=201, sent=51, errors=0, cursor=202`.
- 두 번째 예약실행: `universe=201, evaluated=201, sent=71, errors=0, cursor=202`.
- 두 실행에서 Gmail 통합신호 **총 7통** (3+4), 발송처리 이벤트 **총 122개**, 이벤트 ID 122개 모두 고유.
- 기존 KODEX200의 MARKET_CRASH 7% 경보는 동일 단계 중복 재발송하지 않음.
- 전환 후 별도 보유종목/ETF/유튜브 신호 메일은 최초 검증 시점까지 추가 발송되지 않음.
- 원본 확인: `ETF_WATCHLIST!K` 수식, `REGIME!Q:R` 수식, YouTube `CANDIDATES!W:Y` 기존 분석값 보존.

## 배포/소스
- Cloud Run project `stock-monitoring-prod` / region `asia-northeast3` / service `youtube-signal-service`.
- 새 공통 API 리비전 `youtube-signal-service-00030-sig`, 운영 100% (전환 당시).
- Apps Script IDs:
  - HELD/ETF/unified: `1zXn_L305g2D687tdhPzFgghvZ8wNSEbPiZnnx3gAbtePgifIHY_cFgVG`
  - YOUTUBE: `1CDBtqB-MUQkthBQYJVXcjAW3PylY8MORHjAi8l0pWZ0t75anWvXetQq8`
  - D0/D+3/D+5: `17SXYgY3jtGI0z074xBAa-FIZnLu392GK1W121h91HLV-0X73E6OOQO2y`
- Git branch `feat/unified-three-watchlists-20261013`, 안전수정 `d95d20b`, 시간대/커서 보완 `d3dbf39`.
- 기존 7개 HELD 파일의 해시 일치 확인 후 신규 `UnifiedWatchlists.js`만 갱신. YOUTUBE는 신규 부트스트랩 연결부만 갱신.

## 201종목 부하 및 안전성
- 실제 Cloud Run 별도 읽기 전용 검증 3회:
  1. 4 동시/무재시도: 201건 68.0초, 네트워크 503 27건.
  2. 2 동시/무재시도: 201건 116.5초, 네트워크 503 4건.
  3. 2 동시/503 최대2회 재시도: 201건 129.6초, 오류 0건.
- 실패 서버 로그는 키움 연결의 `No route to host`였으며, 근본적인 VPC/네트워크 원인은 별도 미확정.
- 새 통합 Apps Script: 2종목 동시 요청, 503/429 재시도(1.5초/4.2초), 최대20종목 묶음 메일, 원본표에 결과 덮어쓰기 금지.
- 검사 오류가 남으면 `ACTIVE_PARTIAL / API_ERRORS`이며 성공 완료로 표기하지 않음. 기존 설치 트리거는 롤백을 위해 유지.
- `PREVIEW`→`ACTIVE` 같은 5분 구간에서도 커서를 모드별 분리하여 종목 누락 방지.
- Python 통합·회귀 75개와 Node 기존 ETF·통합/201종목/시간대 테스트 통과.

## 롤백 방법 (코드 덮어쓰기 불필요)
- `UNIFIED_CONTROL!B2`를 `ACTIVE`에서 `PREVIEW`로 되돌린다.
- 기존 `hmTick5m`, `ewTick5m`, YouTube 후보 실행 진입점·트리거를 삭제하지 않았으므로 이전 경로를 다시 사용한다.
- 롤백 시 원본 시트의 수식 및 보유 수량/평단을 복원할 필요가 없도록 보존했다.

## 아직 실전 확인이 필요한 항목
- 두 번째 이후 5분 실행에서 상태/중복 재발송 억제, 이후 D0/D+3/D+5 실제 보고서 도착 확인.
- NXT 15:30 이후: 새 공통 엔진은 정규장만 검증. YouTube 후보 수집/이전 감시 호출 경로는 장후 유지하되, **실제 NXT 가능 종목/실거래시세 판정의 통합은 아직 미완료**.
- 사용자의 원칙: NXT 거래 가능 개별주식만 장후 감시; ETF는 정규장 종료. 실제 체결시각 확인되지 않은 가격으로 경고 금지.
- 구조 안정화 후 필요할 때만 ETF 대표 매핑·메일 중요도, DB 보존기간 등을 재평가. 중복 시스템이나 새로운 Shadow 엔진은 추가하지 않는다.
