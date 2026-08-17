---
name: atr-v4-trade-management
description: Capital allocation, position sizing, 3-phase scale-in, ratcheting stop loss, and trailing take-profit protocol based on Wilder ATR14 volatility.
methodology_version: 1.0
compatible_system: v1.0.0-shadow-prod
status: FROZEN_METHOD_V1
---

# ATR V4 Trade & Position Risk Management System

## 1. Skill Name
`atr-v4-trade-management`

## 2. Purpose
This skill establishes an immutable mathematical and execution contract for equity position sizing, 3-phase scale-in accumulation, ratcheting stop loss protection, and trailing profit realization based on Wilder's 14-day Average True Range (ATR14). 

The primary objective is **capital preservation and asymmetry**: total risk per trade is strictly budgeted as a fraction of total portfolio equity, initial entry establishes immutable risk anchors ($P_0, A_0, S_0$), loss cuts are ratcheted monotonically upward, and trailing take-profit protects accrued gains without premature capping.

---

## 3. When to Use
- When determining the maximum allowable share quantity for a new stock candidate based on account risk budget.
- When generating price levels for 1st entry, 2nd pullback scale-in, and 3rd breakout scale-in.
- When evaluating end-of-day stop loss ratchets ($S_{final}$) based on daily closing highs ($H_{close}$).
- When managing active trailing take-profit lines after prices exceed the $+3.0 A_0$ activation hurdle.
- When handling account concentration risk caps and defensive portfolio recovery modes.

---

## 4. Required Inputs
1. **Account Equity Context**:
   - `total_equity`: Current total account liquidation value in KRW.
   - `current_position_weight`: Existing percentage of portfolio allocated to the target stock.
2. **Volatility & Price Data**:
   - `completed_daily_atr14`: Prior day completed 14-day Wilder ATR ($A_t$ or $A_0$).
   - `current_market_price`: Current traded price in KRW.
   - `daily_highest_close`: Highest daily closing price achieved during the active position cycle ($H_{close}$).
   - `post_activation_peak_high`: Peak intraday high price achieved after trailing activation ($H_{high}$).
3. **Active Position Anchors** (if holding an existing position):
   - `P0`: Fill-weighted average execution price of the 1st entry tranche.
   - `A0`: Prior day completed daily ATR14 at the exact time of 1st entry execution (fixed for the entire position cycle).
   - `S_prev`: Previously confirmed and recorded stop loss price.
   - `profit_trail_prev`: Previously confirmed trailing floor price.
   - `trailing_activation_status`: `ACTIVE` or `INACTIVE`.

---

## 5. Optional Inputs
- `candidate_reference_price`: Current market price used in pre-entry / watchlist screening (strictly distinct from executed $P_0$).
- `candidate_reference_atr`: Latest completed daily ATR used for pre-entry simulation (strictly distinct from locked $A_0$).
- `portfolio_mode_override`: `NORMAL`, `RECOVERY`, `CONCENTRATION_RISK`, `USER_OVERRIDE`, or `RISK_LOCK`.

---

## 6. Immutable Rules & Core Formulas

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               ATR V4 Core Formula Hierarchy                            │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 1. Immutable Anchors:                                                                  │
│    • P0 = Fill-Weighted Average Execution Price of Tranche 1 (1st 50% entry)           │
│    • A0 = Prior-day Completed Wilder ATR14 at Tranche 1 inception (LOCKED for cycle)   │
│    • S0 = P0 - (1.5 × A0) [Initial Protective Stop Floor]                              │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 2. Position Sizing & 20% Concentration Guardrail:                                      │
│    • Risk Budget = Total Equity × (0.50% default | 0.75% top-tier confirmed)           │
│    • Risk Per Share = (P0 - S0) + Slippage Buffer [where buffer = max(0.1*A0, 5 ticks)]│
│    • Target Quantity = min( floor(Risk Budget / Risk Per Share), Concentration Guard ) │
│    • Single-stock 20% Concentration Guardrail:                                         │
│      - If new entry or scale-in is projected to exceed 20% weight ──> PROHIBIT BUY.    │
│      - If existing position exceeds 20% weight due to price appreciation:              │
│        ──> Enter CONCENTRATION mode (Block new buys, review risk, manual rebalance).   │
│        ──> CRITICAL: NEVER automatically trigger forced sale or machine liquidation.   │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 3. 3-Phase Scale-in Protocol:                                                          │
│    • Tranche 1 (50%): Executed upon verified BUY_ALLOWED confirmation.                 │
│    • Tranche 2 (30%): Price reaches P0 - 1.5*A0 ──> Watch Active (DO NOT BUY YET)      │
│                       ──> Rebounds by +0.5*A0 from pullback low ──> Execute 30%.       │
│    • Tranche 3 (20%): Price recovers above P0 or breaks out to new high ──> Execute 20%│
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 4. Monotonic Stop Ratchet (End-of-Day):                                                │
│    • S_new = H_close - (1.5 × At)                                                      │
│    • S_final = max( S_prev, S0, S_new )  ──> STOP LEVEL CAN NEVER MOVE DOWN!           │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 5. NORMAL Trailing Profit Take:                                                        │
│    • Activation Hurdle: High >= P0 + (3.0 × A0)                                        │
│    • Trailing Floor: T_final = max( T_prev, H_high - (0.8 × At) )                      │
│    • Execution Exit Line: Exit_Line = max( S_final, T_final )                          │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Detailed Invariant Breakdown
1. **$P_0$ and $A_0$ Cycle Immutability**:
   - $P_0$ and $A_0$ are permanently fixed upon Tranche 1 execution. They must never be recalculated using subsequent market prices or newly shifting intraday ATRs during the same position cycle.
2. **Watchlist Pre-Entry Distinction**:
   - In pre-entry screening, outputs must be explicitly labeled `candidate_ref_price`, `candidate_stop_price`, and `candidate_target_price`. They must **never** be referenced as true $P_0, A_0, S_0$.
3. **No Blind Averaging Down on Tranche 2**:
   - Reaching $P_0 - 1.5 A_0$ is strictly a **Watch Trigger Line**, not an execution trigger. Buying Tranche 2 is strictly prohibited until a confirmed reversal bounce of $+0.4 \sim 0.5 A_0$ (default $+0.5 A_0$) from the lowest pullback point is verified.
4. **Monotonic Non-Decreasing Stop Loss**:
   - $S_{final} = \max(S_{prev}, S_0, S_{new})$. A stop loss price can only ratchet upward as closing prices reach new highs; it can **never** be adjusted downward for any reason.
5. **NORMAL Trailing Delta is $0.8 \times A_t$**:
   - Once activated at $P_0 + 3.0 A_0$, the profit trailing line is calculated as $H_{high} - (0.8 \times A_t)$, where $H_{high}$ is the highest market price recorded post-activation. $H_{high}$ and the trailing floor $T_{final}$ are monotonically non-decreasing.
6. **$+3.0 A_0$ is an Activation Hurdle, Not an Automatic Sale**:
   - Reaching $+3.0 A_0$ activates trailing monitoring. It is **not** an automatic 50% partial exit trigger in standard NORMAL mode.
7. **Single-Stock 20% Concentration Guardrail**:
   - New order sizing ensures the position does not exceed 20% of total portfolio equity at entry.
   - If price appreciation subsequently elevates the position weight above 20%, the system enters `CONCENTRATION_RISK` mode, which blocks all additional buying and signals human risk review.
   - **Crucial Invariant**: Simply exceeding 20% due to unrealized market gains does **NOT** trigger automatic liquidation, forced selling, or immediate mechanical trimming.
8. **RECOVERY Mode is Exception-Only**:
   - RECOVERY mode (Activation: $P_0 + 1.2 A_0$, Trailing Delta: $0.3 A_t$) is a manual/strategic defensive state. It must **never** be entered automatically simply because a position holds an unrealized loss.
9. **Position Mode Precedence Hierarchy**:
   - `DATA_HOLD / SUSPENDED` > `USER_OVERRIDE` > `CONCENTRATION_RISK` > `RECOVERY` > `NORMAL`.

---

## 7. Decision Flow & Execution Logic

```mermaid
flowchart TD
    Sub[Daily Close / Price Tick] --> ModeCheck{Position Mode?}
    ModeCheck -- "NORMAL" --> CheckAct{High >= P0 + 3.0*A0?}
    ModeCheck -- "RECOVERY" --> CheckRec{High >= P0 + 1.2*A0?}
    
    CheckAct -- "Yes" --> SetActive[Set Trailing: ACTIVE]
    CheckAct -- "No" --> KeepInactive[Set Trailing: INACTIVE]
    
    SetActive --> CalcTrail[Calculate T_new = H_high - 0.8*At]
    CalcTrail --> LockTrail[T_final = max(T_prev, T_new)]
    
    KeepInactive --> StopCalc[Calculate S_new = H_close - 1.5*At]
    LockTrail --> StopCalc
    
    StopCalc --> Ratchet[S_final = max(S_prev, S0, S_new)]
    Ratchet --> ExitLine[Effective Exit Line = max(S_final, T_final)]
    
    ExitLine --> TickAdjust[Apply KRX Tick Size Floor Adjustment]
    TickAdjust --> ComparePrice{Current Price <= Exit Line?}
    
    ComparePrice -- "Yes" --> TriggerExit[Emit Signal: STOP or TRAIL_LIQUIDATE]
    ComparePrice -- "No" --> HoldPosition[Emit Signal: HOLD / TRAIL_ACTIVE]
```

---

## 8. Forbidden Behaviors
- **DO NOT** reset $P_0$ or $A_0$ to current market prices after partial fills or subsequent tranches.
- **DO NOT** lower a previously established stop loss price ($S_{prev}$) under any circumstances.
- **DO NOT** execute 2nd tranche purchases immediately when price touches $P_0 - 1.5 A_0$ without waiting for the $+0.5 A_0$ rebound.
- **DO NOT** execute new entries or scale-in additions if the projected allocation exceeds the 20% Single-Stock Concentration Guardrail.
- **DO NOT** trigger automatic forced liquidation or mechanical selling simply because price appreciation elevates existing position weight above 20%.
- **DO NOT** use intraday unfinished candle ATRs to replace the canonical completed daily Wilder ATR14.

---

## 9. Output Contract
Every execution and risk evaluation must emit the following structured data:

```json
{
  "stock_code": "STRING (6 digits)",
  "trade_mode": "NORMAL | RECOVERY | CONCENTRATION_RISK | USER_OVERRIDE | DATA_HOLD",
  "anchors": {
    "P0": "FLOAT (KRW)",
    "A0": "FLOAT (KRW)",
    "S0": "FLOAT (KRW)",
    "At_current": "FLOAT (KRW)"
  },
  "scale_in_status": {
    "tranche_1_filled": "BOOLEAN (50%)",
    "tranche_2_watch_active": "BOOLEAN (Price touched P0 - 1.5*A0)",
    "tranche_2_pullback_low": "FLOAT (KRW) | null",
    "tranche_2_trigger_price": "FLOAT (KRW) [Low + 0.5*A0]",
    "tranche_2_filled": "BOOLEAN (30%)",
    "tranche_3_filled": "BOOLEAN (20%)"
  },
  "protection_levels": {
    "H_close": "FLOAT (KRW)",
    "S_new": "FLOAT (KRW)",
    "S_final": "FLOAT (KRW)",
    "profit_activation_hurdle": "FLOAT (KRW) [P0 + 3.0*A0]",
    "profit_activation_status": "ACTIVE | INACTIVE",
    "H_high_post_activation": "FLOAT (KRW) | null",
    "trailing_floor_price": "FLOAT (KRW) [H_high - 0.8*At]",
    "effective_exit_line": "FLOAT (KRW)",
    "krx_adjusted_exit_tick": "INTEGER (KRW)"
  },
  "position_sizing": {
    "account_risk_pct": "FLOAT (0.0050 | 0.0075)",
    "risk_budget_krw": "FLOAT (KRW)",
    "risk_per_share_krw": "FLOAT (KRW)",
    "max_allowed_shares": "INTEGER",
    "concentration_guard_shares": "INTEGER",
    "target_order_shares": "INTEGER"
  },
  "action_signal": "HOLD | SCALE_IN_BUY | TRAIL_ACTIVE | STOP_TRIGGERED | TRAIL_EXIT_TRIGGERED"
}
```

---

## 10. Validation Checklist
- [ ] Is $A_0$ locked to the completed daily ATR14 at Tranche 1 inception?
- [ ] Is $S_0$ mathematically equal to $P_0 - (1.5 \times A_0)$?
- [ ] For Tranche 2, is the execution waiting for the $+0.5 A_0$ rebound from the pullback low?
- [ ] Is the stop ratchet verifying $S_{final} \ge S_{prev}$ (monotonic non-decreasing)?
- [ ] In NORMAL mode, is the trailing delta exactly $0.8 \times A_t$?
- [ ] Has the tick size floor adjustment been applied according to KRX price bracket rules?
- [ ] Does new order sizing verify that initial entry or scale-in remains within the 20.0% Single-Stock Concentration Guardrail?
- [ ] Is it confirmed that positions exceeding 20% due to price appreciation enter `CONCENTRATION_RISK` without automated forced liquidation?

---

## 11. Failure / Unknown Handling
- **Missing or Zero ATR14**: If ATR cannot be computed due to insufficient daily candles ($N < 14$), lock trade mode to `DATA_HOLD`, prohibit new position entries, and maintain the last valid confirmed stop price.
- **Extreme Overnight Gap Below S0**: If the market opens with a gap below $S_{final}$, execute market liquidation immediately upon market open without waiting for intraday recovery.
- **Corrupted High/Low Feed**: If $H_{close}$ or $H_{high}$ reports values lower than previous confirmed historical values, reject the update and retain previous historical peaks (`FAIL_CLOSED`).

---

## 12. Example Usage

```text
[Position Inception Walkthrough]
- Total Account Equity: 100,000,000 KRW
- Target Stock: Standard Industrials
- Executed 1st Tranche Price (P0): 50,000 KRW (50% allocated)
- Completed Daily ATR14 at Entry (A0): 2,000 KRW

[Calculated Anchors]
- Initial Stop (S0) = 50,000 - (1.5 * 2,000) = 47,000 KRW
- Risk per Share = (50,000 - 47,000) + 200 (buffer) = 3,200 KRW
- Risk Budget (0.5%) = 500,000 KRW
- Total Target Quantity = floor(500,000 / 3,200) = 156 shares (Tranche 1 = 78 shares)

[Scale-in Management]
- Tranche 2 Watch Line = 50,000 - (1.5 * 2,000) = 47,000 KRW
- Price drops to 46,800 KRW (Watch Activated, Pullback Low = 46,800 KRW)
- Required Rebound = 46,800 + (0.5 * 2,000) = 47,800 KRW
- Price rises to 47,850 KRW ──> Tranche 2 (30% = 47 shares) Executed.

[Trailing Management]
- Later, stock surges to 57,000 KRW (>= 50,000 + 3.0*2,000 = 56,000 KRW Hurdle)
- Trailing Activation Status: ACTIVE
- Price reaches peak high H_high = 60,000 KRW (Current At = 2,200 KRW)
- Trailing Floor = 60,000 - (0.8 * 2,200) = 58,240 KRW (KRX adjusted: 58,200 KRW)
- Stop Ratchet = max(S_prev, H_close - 1.5*2,200) = 55,500 KRW
- Effective Exit Line = max(55,500, 58,200) = 58,200 KRW
```

---

## 13. Version & Inter-Skill Relationships
- **Methodology Version**: `1.0`
- **Compatible System**: `v1.0.0-shadow-prod`
- **Status**: `FROZEN_METHOD_V1`
- **Referenced Skills**:
  - `korean-equity-decision-system`: Provides the approved candidate signal and trigger condition before this skill activates position sizing and risk anchors.
  - `shadow-performance-validator`: Tracks MFE/MAE and trailing stop touches against the fixed $A_0$ anchor.
  - `equity-system-integrity-auditor`: Independently audits formula consistency, tick sizes, and monotonic stop ratchets.
