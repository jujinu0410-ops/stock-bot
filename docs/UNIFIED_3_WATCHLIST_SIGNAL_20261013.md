# 세 시트 · 하나의 매매신호 엔진 — 마이그레이션 기준 (2026-10-13)

## 확정 원칙
- 보유종목 / 유튜브 언급종목 / ETF: **세 개의 종목 목록만 유지**.
- 매매 판정은 **한 엔진**, 출처는 메타데이터만 다름. 같은 종목은 코드 6자리로 식별.
- 키움 실시간 시세로 발송 전에 가격·등락률 검증. GOOGLEFINANCE 지연시세는 후보 발견용.
- VWAP / OBV / ATR / 45분봉 보조검사, 위험경고 보장, 중복 방지, D0·D+3·D+5 복기 방식은 최종 공통화.
- **시장 보정은 매수 신호 우선순위를 최대 한 단계만 ±1**. 기술적 미확인에서 매수확인으로 억지 승격 금지.
- 코스피200 ETF(069500)·코스닥150 ETF(229200)는 지수의 **대용 신호**. 실제 KOSPI·KOSDAQ 지수와 동일하지 않음.
- 관련 섹터 ETF는 **매핑이 실제로 확인된 종목만** 사용; 불명확한 종목에 임의로 붙이지 않음.
- 인버스 ETF는 시장 방향 부호만 반전; 기술확인·추격매수 억제는 동일.
- 하락확인·손절·OBV15 위험 알림은 시장 보정이나 Jev 탓에 차단하지 않음.
- 자동매매/주문 API는 사용하지 않음. 별도 Shadow 전략 도입 금지.

## 2026-10-13 실제 완료
- `services/unified_signal_engine.py`: 단일 신호 단계 및 시장·섹터 보정 함수.
- 기존 `youtube_signal_service.py`에 `POST /unified-signal` 추가 (기존 API 유지).
- 세 출처 (HELD / ETF / YOUTUBE), KOSPI200·KOSDAQ150 대용 시세, 인버스 방향, 실제 키움 시세 검증 통과.
- 단위·회귀 테스트 64개 성공. Cloud Run 00026-has 리비전으로 새 API가 활성화됨. 단, 세 원천 감시기가 새 API를 호출하도록 연결한 것은 아니므로 기존 알림 판정은 그대로 유지.
- 기존 **세 원천 엔진의 이벤트 생성·메일 발송은 변경하지 않음**. 즉, **전환 완료라고 주장할 수 없음**.

## 지금 남아 있는 차이 (삭제 전 반드시 해소)
1. 보유종목: Apps Script `hmDetectEvents_` /`hmGateEvents_` /`hmEvaluate_`에서 직접 알림·상태 관리.
2. ETF: `ewPlan_` /`ewApplyJevGate_` / `ewTick5m` 별도 단계·발송.
3. 유튜브: `processCandidateRow_`와 `/confirm`의 BUY_CANDIDATE 중심 기술 판정.
4. 중복 방지: 같은 티커가 여러 시트에 있으면 출처를 가로지르는 공통 발송기록 저장소가 아직 없음.
5. 시트별 **관련 ETF 매핑**이 공통 데이터로 명확하게 확보되지 않아, 유효한 매핑이 없을 때는 시장 방향만 적용.

## 안전한 통합 순서
1. 세 입력을 `ticker, sources, stage, market, sector_etf_code, technical_verified`로 정규화.
2. 동일 티커 중복 통합, 공통 신호 계산을 **기록된 실제 이벤트로 오프라인 재생**해 누락·과잉 비교.
3. 공통 상태·발송 이력 저장소를 먼저 검증. **중복 방지가 준비되기 전 기존 메일 발송기를 모두 활성 상태로 연결하지 않음**.
4. 공통 알림기가 기존 HELD/ETF/YouTube 알림과 같은 결과를 낸다는 테스트가 성공하면 출처별 전송 기능을 **하나씩** 교체.
5. 전환 후에만 중복 트리거 및 구식 예외 코드 제거. 기존 매도 위험경고는 마지막까지 보존.

## 수용 기준
- 069500 -7% 하락 시 지연시세/Jev로 위험알림이 막히지 않음.
- 114800(인버스)은 하락장에서 후보 우선순위 상승하되 1분봉 악화 시 매수로 확정하지 않음.
- 373220처럼 45분봉 약세인 유튜브 종목은 시장 호재만으로 매수 메일 발송되지 않음.
- 103590 등 이미 검증된 OBV15·손절 경고가 그대로 전달됨.
- 한 종목이 여러 시트에 있어도 동일 판단·1회 발송. 보유수량·평단은 보유 탭에만 남음.
- 장중 5분 실행, 휴장일, NXT/aftermarket 구분, 실패 재시도, 지메일 리포트 복기 확인.


## V2 integration (2026-10-13, implementation; rollout tracked below)

- `services/unified_scan.py` and `/unified-scan` generate the same daily VWAP9/26, OBV/OBV9, completed-day Wilder ATR14, completed 45m guards and live-minute verification for every source. Existing REGIME direction thresholds are retained. Buy setup requires +0.5 ATR from the common 5-completed-day low; confirmation requires +0.6 ATR as well as the technical confirmed regime. Market confidence is +/-1 annotation, never a manufactured confirmation.
- `apps_script/unified_watchlists.gs` merges active lists by ticker before evaluation. One coordinator runs from the existing `hmTick5m` trigger. Existing ETF/legacy entrypoints delegate after ACTIVE. YouTube bootstrap retains ingestion but delegates all signal production after ACTIVE.
- Per-ticker Script Properties own confirmation, delivery, crash escalation and stop re-cross cooldown. A Gmail draft outbox with deterministic event IDs reconciles acceptance/state-write failures; ambiguous delivery never blindly resends.
- `ALERT_LOG` records actual delivery; hidden `UNIFIED_DECISIONS` records up to 3000 evaluations/holds/errors. Existing D0/D3/D5 parser reads `UNIFIED_EVENTS_JSON` from sent mail and uses the exact shared event ID. Blocked decisions remain audit evidence, not fictitious trades.
- `UNIFIED_CONTROL!B2`: OFF / PREVIEW / ACTIVE. PREVIEW checks the seven handoff/overlap cases without sending or disabling legacy risks. ACTIVE requires a successful preview, both application projects and replay parser deployed, then readback. B3:B7 stores execution status. Explicit mapping A9:C must reference the actual ETF universe.
- Existing live source checks revealed KRX-only 09:00-15:30 windows in all three monitors; there is no verified NXT eligibility implementation to preserve. V2 keeps that schedule and does not claim after-market coverage.
- Auto-order APIs and portfolio quantity, average price, stops are not mutated. Display-only stage fields are updated from the common result.
- Rollback: set control to OFF, restore pre-cutover formula snapshots and Apps Script backups, retain the hmTick5m trigger; restore ETF trigger only if standalone mode is needed.

Validation before deployment: 90 Python tests; Node union/confirmation/delivery/replay tests; existing held and ETF tests. The stale ETF test fixture was aligned with the already-live holiday gate and 120-minute candidate tolerance plus independent Kiwoom verification.
