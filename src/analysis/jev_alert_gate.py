# -*- coding: utf-8 -*-
"""Jev-backed final delivery gate for StockBot alert candidates.

The existing technical rules decide which events are worth *considering*.
This module is deliberately a second, fail-open gate: it may suppress a
non-critical candidate only when Jev is configured, responds successfully,
and the explicit policy says to hold it.  It never creates orders or changes
portfolio state.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Mapping

import requests


JEV_SYSTEMONE_URL = "https://api.typesafe.ai/v1/systemone"
HARD_RISK_EVENT_KEYS = {"STOP_BREACH", "STOP_BREACH_INITIAL"}


class JevAlertGateError(RuntimeError):
    """A transport or response-shape failure from the Jev API."""


@dataclass(frozen=True)
class JevGateSettings:
    api_key: str
    mode: str = "SHADOW"
    model: str = "jev-latest"
    send_threshold: float = 0.70
    deterioration_threshold: float = 0.70
    min_urgency: float = 2.0
    timeout_seconds: float = 8.0

    @classmethod
    def from_env(cls, source: str = "") -> "JevGateSettings":
        source_key = str(source or "").strip().upper()
        mode_name = "JEV_ALERT_GATE_" + source_key + "_MODE" if source_key else ""
        mode = str(os.getenv(mode_name) or os.getenv("JEV_ALERT_GATE_MODE") or "SHADOW").strip().upper()
        if mode not in {"OFF", "SHADOW", "ACTIVE"}:
            mode = "SHADOW"
        return cls(
            api_key=str(os.getenv("JEV_API_KEY") or "").strip(),
            mode=mode,
            model=str(os.getenv("JEV_MODEL") or "jev-latest").strip() or "jev-latest",
            send_threshold=_bounded_env("JEV_ALERT_GATE_SEND_THRESHOLD", 0.70, 0.0, 1.0),
            deterioration_threshold=_bounded_env("JEV_ALERT_GATE_DETERIORATION_THRESHOLD", 0.70, 0.0, 1.0),
            min_urgency=_bounded_env("JEV_ALERT_GATE_MIN_URGENCY", 2.0, 0.0, 4.0),
            timeout_seconds=_bounded_env("JEV_ALERT_GATE_TIMEOUT_SECONDS", 8.0, 1.0, 20.0),
        )


def _bounded_env(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.getenv(name) or default).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _event_keys(payload: Mapping[str, Any]) -> set[str]:
    raw = payload.get("event_keys") or payload.get("event_key") or []
    if isinstance(raw, str):
        raw = raw.split("|")
    if not isinstance(raw, (list, tuple, set)):
        return set()
    return {str(item).strip().upper() for item in raw if str(item).strip()}


def _state(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Send compact, serializable evidence only; never pass credentials."""
    return {
        "source": str(payload.get("source") or "STOCKBOT").upper(),
        "as_of": str(payload.get("as_of_date") or ""),
        "ticker": str(payload.get("ticker") or ""),
        "name": str(payload.get("name") or ""),
        "existing_rule_candidate": True,
        "event_keys": sorted(_event_keys(payload)),
        "event_level": str(payload.get("event_level") or ""),
        "technical": {
            "current_price": payload.get("current_price"),
            "change_pct": payload.get("change_pct"),
            "atr_band": payload.get("atr_band"),
            "volume_pace": payload.get("volume_pace"),
            "stop_broken": payload.get("stop_broken"),
            "vwap9_direction": payload.get("vwap9_dir"),
            "vwap26_direction": payload.get("vwap26_dir"),
            "vwap_relation": payload.get("vwap_rel"),
            "obv_direction": payload.get("obv_dir"),
            "obv9_direction": payload.get("obv9_dir"),
            "obv_relation": payload.get("obv_rel"),
            "regime": payload.get("regime"),
            "etf_signal": payload.get("etf_signal"),
        },
        "context": payload.get("context") or {},
    }


def _questions() -> dict[str, Any]:
    return {
        "signal_direction": {
            "type": "choice",
            "instructions": "Classify the market implication of the supplied evidence, not a trading instruction.",
            "criteria": {
                "UP": "Evidence is predominantly improving or bullish.",
                "DOWN": "Evidence is predominantly deteriorating or risk-off.",
                "NEUTRAL": "Evidence is mixed, stale, or insufficient for a directional conclusion.",
            },
        },
        "signal_stage": {
            "type": "choice",
            "instructions": "Rate the upward opportunity stage only. A DOWN signal should normally be NONE or DEVELOPING, never BUY_CONFIRMED.",
            "criteria": {
                "NONE": "No credible upward setup is established.",
                "DEVELOPING": "Early or incomplete upward setup; track but conviction is limited.",
                "BUY_WATCH": "Multiple supplied signals support an actionable watch-level upward setup.",
                "BUY_CONFIRMED": "Strong supplied evidence supports a confirmed upward setup, while still not being an order instruction.",
            },
        },
        "alert_urgency": {
            "type": "score",
            "instructions": "Score how urgently a human should receive this existing alert candidate.",
            "criteria": [
                "0: record only; no material change.",
                "1: weak or noisy; can wait.",
                "2: useful monitoring information.",
                "3: a timely human alert is warranted.",
                "4: urgent risk or unusually strong, time-sensitive evidence.",
            ],
        },
        "send_alert_now": {
            "type": "noul",
            "instructions": "Given the supplied state and the fact that the rule engine already nominated this candidate, should a human receive an alert now? This is only an alert decision, never an order decision.",
        },
        "signal_deteriorating": {
            "type": "noul",
            "instructions": "Is the supplied evidence materially deteriorating such that withholding a risk alert would be undesirable?",
        },
    }


def _number(answer: Mapping[str, Any] | None, key: str, default: float = 0.0) -> float:
    try:
        return float((answer or {}).get(key, default))
    except (TypeError, ValueError):
        return default


def _choice(answer: Mapping[str, Any] | None) -> str:
    return str((answer or {}).get("choice") or "UNKNOWN").upper()


def _policy_decision(
    payload: Mapping[str, Any], answers: Mapping[str, Any], settings: JevGateSettings
) -> tuple[str, str]:
    """Return (decision, reason). Critical risk and provider failures are fail-open."""
    event_keys = _event_keys(payload)
    if event_keys & HARD_RISK_EVENT_KEYS:
        return "SEND", "HARD_RISK_BYPASS"
    if settings.mode != "ACTIVE":
        return "SEND", "SHADOW_RECORD_ONLY"

    urgency = _number(answers.get("alert_urgency"), "score")
    send_probability = _number(answers.get("send_alert_now"), "noul")
    deterioration = _number(answers.get("signal_deteriorating"), "noul")
    direction = _choice(answers.get("signal_direction"))
    risk_event = any("DOWN" in key or "RISK" in key for key in event_keys)

    if risk_event and direction == "DOWN" and deterioration >= settings.deterioration_threshold and urgency >= 1.0:
        return "SEND", "ACTIVE_DOWN_RISK"
    if send_probability >= settings.send_threshold and urgency >= settings.min_urgency:
        return "SEND", "ACTIVE_THRESHOLD_MET"
    return "HOLD", "ACTIVE_THRESHOLD_NOT_MET"


def evaluate_jev_alert_gate(
    payload: Mapping[str, Any],
    *,
    settings: JevGateSettings | None = None,
    session: Any = requests,
) -> dict[str, Any]:
    """Evaluate a candidate and return a typed, fail-open delivery decision."""
    cfg = settings or JevGateSettings.from_env(str(payload.get("source") or ""))
    base = {
        "ok": True,
        "mode": cfg.mode,
        "source": str(payload.get("source") or "STOCKBOT").upper(),
        "ticker": str(payload.get("ticker") or ""),
        "event_keys": sorted(_event_keys(payload)),
        "safety": {"order_api": False, "portfolio_mutation": False, "sheet_write": False},
    }
    if cfg.mode == "OFF":
        return {**base, "status": "OFF", "decision": "SEND", "reason": "GATE_DISABLED"}
    if not cfg.api_key:
        return {**base, "status": "NOT_CONFIGURED", "decision": "SEND", "reason": "JEV_API_KEY_MISSING"}

    request_body = {"model": cfg.model, "state": _state(payload), "questions": _questions()}
    try:
        response = session.post(
            JEV_SYSTEMONE_URL,
            headers={"Authorization": f"Bearer {cfg.api_key}", "content-type": "application/json"},
            json=request_body,
            timeout=cfg.timeout_seconds,
        )
    except Exception as exc:  # Requests-level errors must never suppress an existing alert.
        return {**base, "status": "ERROR", "decision": "SEND", "reason": f"JEV_TRANSPORT:{type(exc).__name__}"}

    if int(getattr(response, "status_code", 0)) != 200:
        return {**base, "status": "ERROR", "decision": "SEND", "reason": f"JEV_HTTP_{getattr(response, 'status_code', 0)}"}
    try:
        body = response.json()
        answers = body["answers"]
        if not isinstance(answers, Mapping):
            raise TypeError("answers is not an object")
    except Exception as exc:
        return {**base, "status": "ERROR", "decision": "SEND", "reason": f"JEV_RESPONSE:{type(exc).__name__}"}

    decision, reason = _policy_decision(payload, answers, cfg)
    return {
        **base,
        "status": "OK",
        "decision": decision,
        "reason": reason,
        "model": str(body.get("model") or cfg.model),
        "answers": dict(answers),
        "usage": body.get("usage") or {},
    }
