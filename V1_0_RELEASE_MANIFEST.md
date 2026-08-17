# Stock Analysis & Autonomous Trading System V1.0 Release Manifest

## 1. Release Overview
- **Release Tag**: `v1.0.0-shadow-prod`
- **Release Date**: `2026-08-17`
- **System Lifecycle State**: **`V1.0 FEATURE COMPLETE / SHADOW PRODUCTION (FROZEN)`**
- **Regression Test Coverage**: **111 / 111 Unit Tests Passing (100% OK)**
- **Engineering Policy**: **`STRICT INVARIANT FREEZE`** (기존 F/T/Fundamental/Forward/Industry/Technical/ATR/Risk/BUY 로직 100% 동결. $N < 30$ 표본 미만 시 자동 파라미터 튜닝 일체 금지).

---

## 2. Multi-Layer Integrated Architecture

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        Stock Analysis & Trading System V1.0 Architecture               │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ Layer 0: Weekly Industry Radar & Shadow Matrix (Phase 6.3 Source Replay & Authenticity)│
│          • 9대 산업 바텀업 증거 정규화 (DRIVER_CAPPED_LINEAR, Factor 상한)             │
│          • Origin: LIVE_FETCHED / REFERENCE_VERIFIED / DERIVED_INTERNAL / MANUAL_QUAL  │
│          • Gate: INDUSTRY_PASS_STRONG / PASS / CONDITIONAL / WAIT / BLOCK              │
│          • 기업 Exposure Type (DIRECT_CORE, DIRECT_PARTIAL, INDIRECT, THEME_ONLY)      │
│          • Multi-Layer Shadow Matrix Synthesis (Primary Blocker & All Blockers 보존)   │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ Layer 1: Kiwoom REST & 45m Intraday Technical Gate (Phase 3.1 Reliability & Lineage)   │
│          • ADX, DMI, OBV(9), Chaikin Oscillator(13,26), 일목균형표 45분봉 정밀판정    │
│          • Gate: BUY_ALLOWED / BUY_ALLOWED_CONDITIONAL / BUY_WAIT / BUY_BLOCKED        │
│          • 3단계 분할매수 신호 생성기 (1차 50% / 2차 P0-1.5A0 감시선 도달 후 +0.5A0 반등 시 30% / 3차 돌파 20%) │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ Layer 2: ATR V4 Trailing & Position Risk Engine (Phase 2 Capital & Risk Engine)        │
│          • 총자산 0.5% 리스크 예산, P0/A0/S0 1차 체결 기반 원자적 확정                │
│          • 트레일링 익절 (+3.0 ATR 활성화, -0.8 ATR 추락 청산), 초기손절 (-1.5 ATR)    │
│          • RISK_LOCK 수동 예외 방어 모드 지원                                          │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ Layer 3: OpenDART 8-Quarter Fundamental Evidence Layer (Phase 4.1 Flow/Stock & Fresh)  │
│          • 최근 8개 분기 연결재무제표(CFS) 자동 탐색 (2026 Q2 반기보고서 실시간 반영) │
│          • OPM, ROE, FCF, OCF, 부채비율, 자본잠식/적자지속 경고 자동 검증              │
│          • Fundamental State (STRONG / STABLE / IMPROVING / WEAKENING / DISTRESSED)    │
│          • Turnaround Type (A: OPERATING_TURNAROUND, B: TROUGH_RECOVERY, C: EXPANSION) │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ Layer 4: Disclosure & Order Visibility Layer (Phase 5.3 Forward Lineage & Materiality) │
│          • 대형수주, 계약변경, 공장증설, 유상증자, 전환사채 등 수시공시 정밀 분류      │
│          • Discrete Single Quarter Book-to-Bill (수주잔고 브릿지 역산 & UNKNOWN 추적) │
│          • Forward Opportunity State (VERY_STRONG / STRONG / MODERATE / WEAK)          │
│          • Forward Risk State (NONE / LOW / REVIEW_REQUIRED / HIGH)                    │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ Layer 5: Shadow Scan Journal & Outcome Attribution Engine (Phase 7 Canonical Snapshot) │
│          • Canonical Metric Registry (OPM, Order Backlog, Backlog YoY 일원화 감사)    │
│          • Append-Only Immutable Snapshot 테이블: scan_journal                         │
│          • 실제 거래일(5d/10d/20d/40d) 성과 추적 테이블: signal_outcomes               │
│          • MFE/MAE (% 및 ATR 배수), +1/+2/+3 ATR 도달률, -1.5 ATR 손절 도달률          │
│          • Blocker Effectiveness 및 다차원 성과 귀인 분석 (No-Auto-Tuning 가이드 적용) │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Database Schema Overview
1. `stock_info`: 종목 마스터 메타데이터
2. `dart_financials`: 연간 사업보고서 확정 재무
3. `quarterly_financials`: 8분기 Flow/Stock 연결 분기재무 시계열
4. `kiwoom_daily`: 일봉/수급 시세 데이터
5. `disclosure_events`: 정정공시 체인 및 중대성 수시공시 이벤트
6. `order_backlog_metrics`: Book-to-Bill 및 수주잔고 브릿지 지표
7. `industry_runs`: 산업 Radar 주기별 실행 마스터 (Production vs Fixture 분리)
8. `industry_scores`: 6대 Factor 상향식 산업 스코어
9. `industry_evidence`: 출처·해시·파서·Replay 검증 산업 증거 (Canonical Fact JSON)
10. `industry_company_map`: 종목-산업 매핑 및 Exposure Type 이력
11. `scan_journal`: 스캔 시점 전 계층 판단 불변 스냅샷 (Append-Only)
12. `signal_outcomes`: 5d/10d/20d/40d 실제 거래일 기준 사후 성과 및 변동 극값

---

## 4. Operation & Maintenance Rules
1. **정상 스캔 운용**:
   - `scan_stock_for_gems.py` 또는 일괄 스캐너 실행 시 각 종목의 판단이 `scan_journal`에 자동 append-only 적재됩니다.
2. **사후 성과 갱신**:
   - `OutcomeEvaluator`가 5d, 10d, 20d, 40d 실제 거래일 경과 시 `signal_outcomes`의 MFE/MAE, 수익률, ATR 레벨 터치를 자동 갱신합니다.
3. **No Auto-Tuning**:
   - 표본 수 $N < 30$ (권장 50) 미만에서는 파라미터/임계값을 자동 수정하지 않고 순수 관찰 모드를 유지합니다.
4. **코드 동결**:
   - V1.1 정식 변경 요청 또는 버그 수정을 제외하고 분석/리스크/매수 엔진을 수정하지 않습니다.
