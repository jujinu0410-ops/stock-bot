"""
cloud_runner.py

StockBot Google Cloud Run Jobs Orchestrator (Phase 1)

State Machine
-------------
A. GCS Dispatch Receipt exists
   -> ALREADY_COMPLETED: skip all computation and email.

B. Receipt missing
   AND current state manifest committed_run_id == current run_id
   AND prepared dispatch validates OK
   -> RESUME_PENDING_EMAIL: V4/Policy/state mutation forbidden;
      resume from Gmail using persisted prepared dispatch only.

   AND prepared dispatch validation FAILS
   -> PREPARED_DISPATCH_INVALID: Fail-Closed immediately.
      Never fall through to FRESH.

C. Receipt missing
   AND committed_run_id != current run_id (or no current.json)
   -> FRESH execution:
      1. Download GCS state bundle
      2. V4 analysis (skip_email=True)
      3. Validate report_payload (Fail-Closed: None -> RuntimeError)
      4. Persist prepared dispatch (subject, HTML, XLSX) to state_dir
      5. GCS bundle CAS commit (includes prepared_dispatch/)
      6. Gmail dispatch (best-effort idempotent, duplicate risk minimized,
         NOT exactly-once guaranteed)
      7. Create immutable GCS dispatch receipt (if_generation_match=0)
      8. Record SQLite secondary audit

Gmail SMTP caveat
-----------------
There is a narrow crash window between Gmail server reception and
receipt creation (Crash Point E). A re-run may send a duplicate email.
The implementation is best-effort idempotent / duplicate risk minimized.
The phrases "exactly once" and "100%% duplicate-free" must NOT be used.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from src.utils.logger import logger
from src.utils.gcs_state_adapter import (
    GCSStateAdapter,
    StateAdapterError,
    StateConflictError,
    CorruptStateError,
    StateIntegrityError,
    PREPARED_DISPATCH_XLSX,
)
from src.database.db_manager import DatabaseManager

_SM_ALREADY_COMPLETED = "ALREADY_COMPLETED"
_SM_RESUME = "RESUME_PENDING_EMAIL"
_SM_FRESH = "FRESH"


def get_current_kst_time() -> datetime:
    """Returns current datetime in Asia/Seoul timezone (KST / UTC+9)."""
    try:
        return datetime.now(ZoneInfo("Asia/Seoul"))
    except Exception:
        # Fallback for environments lacking tzdata package (e.g. Windows offline)
        from datetime import timezone, timedelta
        return datetime.now(timezone(timedelta(hours=9)))


def resolve_session_code(kst_dt: datetime, report_mode: str) -> str:
    """Returns the scheduled report session represented by a KST run time."""
    mode = (report_mode or "").strip().upper()
    if mode == "INTRADAY":
        # Both intraday schedulers pass only --mode INTRADAY.  Their KST
        # trigger windows must therefore have distinct idempotency keys.
        return "1335" if kst_dt.hour >= 13 else "1120"
    elif mode == "POSTMARKET":
        return "1535"
    else:
        raise ValueError(
            f"INVALID_REPORT_MODE: {mode!r}. "
            "Allowed modes are INTRADAY or POSTMARKET."
        )


def build_run_id(kst_dt: datetime, report_mode: str) -> str:
    """Builds deterministic run ID, e.g. 20260901_INTRADAY_1120."""
    mode = (report_mode or "").strip().upper()
    session_code = resolve_session_code(kst_dt, mode)
    date_str = kst_dt.strftime("%Y%m%d")
    return f"{date_str}_{mode}_{session_code}"


def _validate_report_payload(payload: Any, run_id: str) -> None:
    """
    FIX-2: Fail-Closed validation of report_payload.
    Raises RuntimeError if payload is None or missing required fields.
    State commit, Gmail, and receipt creation MUST NOT happen if this raises.
    """
    if payload is None:
        raise RuntimeError(
            f"REPORT_PAYLOAD_MISSING: run_id={run_id} - "
            "run_post_market_analysis returned None payload. "
            "State commit, Gmail, and receipt creation are forbidden."
        )
    required = ("subject", "html_report", "notifier", "dispatch_id", "dispatch_tag")
    missing = [f for f in required if not payload.get(f)]
    if missing:
        raise RuntimeError(
            f"REPORT_PAYLOAD_MISSING_FIELDS: run_id={run_id} missing={missing}. "
            "State commit, Gmail, and receipt creation are forbidden."
        )


class CloudRunner:
    """
    Orchestrates single-run Cloud Run Job execution with 3-state machine:
    ALREADY_COMPLETED / RESUME_PENDING_EMAIL / FRESH.
    """

    def __init__(
        self,
        report_mode: Optional[str] = None,
        bucket_name: Optional[str] = None,
        prefix: str = "stockbot-state",
        state_dir: Optional[Path] = None,
        storage_client: Any = None,
        force: bool = False,
    ):
        self.kst_now = get_current_kst_time()
        self.report_mode = self._resolve_report_mode(report_mode)
        self.session_code = resolve_session_code(self.kst_now, self.report_mode)
        self.run_id = build_run_id(self.kst_now, self.report_mode)
        self.dispatch_id = f"{self.kst_now.strftime('%Y%m%d')}_{self.session_code}"

        self.state_dir = Path(
            state_dir or os.getenv("STOCKBOT_STATE_DIR") or "/tmp/stockbot"
        ).resolve()
        self.force = force

        self.state_adapter = GCSStateAdapter(
            bucket_name=bucket_name,
            prefix=prefix,
            state_dir=self.state_dir,
            storage_client=storage_client,
        )

    def _resolve_report_mode(self, explicit_mode: Optional[str]) -> str:
        mode = (explicit_mode or os.getenv("REPORT_MODE") or "").strip().upper()
        if mode in ("INTRADAY", "POSTMARKET"):
            return mode
        err = (
            f"INVALID_REPORT_MODE: {mode!r}. "
            "Allowed modes are INTRADAY or POSTMARKET. "
            "Auto-detection is forbidden."
        )
        logger.critical(f"[CloudRunner] {err}")
        raise ValueError(err)

    def _detect_state(self) -> str:
        """
        Detects state machine branch with minimal GCS I/O.
        No full bundle download at this stage.
        """
        if not self.force and self.state_adapter.has_dispatch_receipt(self.run_id):
            return _SM_ALREADY_COMPLETED
        committed_run_id = self.state_adapter.get_committed_run_id()
        if committed_run_id == self.run_id:
            return _SM_RESUME
        return _SM_FRESH

    def _dispatch_email(
        self,
        notifier: Any,
        subject: str,
        html_content: str,
        attachments: List,
        run_id: str,
    ) -> bool:
        """
        Sends Gmail. Returns sent_success.
        SMTP is best-effort idempotent - duplicate risk minimized, NOT exactly-once.
        """
        sent_success = notifier.send_email(
            subject=subject,
            html_content=html_content,
            attachments=attachments,
        )
        if not sent_success and os.getenv("STOCKBOT_TEST_MODE") != "1":
            raise RuntimeError(
                f"Gmail dispatch failed for {run_id} to {notifier.recipient_email}"
            )
        logger.info(
            f"[CloudRunner] Gmail step completed "
            f"(sent_success={sent_success}, "
            "best-effort idempotent - duplicate risk minimized)."
        )
        return sent_success

    def _create_receipt(
        self,
        run_id: str,
        dispatch_id: str,
        notifier: Any,
        subject: str,
        bundle_path: str,
        held_count: int,
    ) -> str:
        receipt_data = {
            "run_id": run_id,
            "dispatch_id": dispatch_id,
            "report_mode": self.report_mode,
            "sent_at_kst": get_current_kst_time().strftime("%Y-%m-%d %H:%M:%S"),
            "recipient_email": getattr(notifier, "recipient_email", "test@test.com"),
            "subject": subject,
            "bundle_path": bundle_path,
            "held_count": held_count,
        }
        return self.state_adapter.create_dispatch_receipt(run_id, receipt_data)

    def _run_already_completed(self) -> Dict[str, Any]:
        logger.info(
            f"[ALREADY_COMPLETED] GCS Dispatch Receipt "
            f"{self.state_adapter.get_dispatch_receipt_path(self.run_id)!r} "
            "already exists. Skipping duplicate execution."
        )
        return {
            "status": _SM_ALREADY_COMPLETED,
            "run_id": self.run_id,
            "dispatch_id": self.dispatch_id,
            "message": "Duplicate execution skipped via GCS immutable dispatch receipt.",
        }

    def _run_resume(
        self,
        bundle_path: str,
        db: DatabaseManager,
    ) -> Dict[str, Any]:
        """
        RESUME_PENDING_EMAIL:
        V4 / Policy / state mutation are FORBIDDEN.
        Retries Gmail only using persisted prepared dispatch.
        """
        logger.info(
            f"[RESUME_PENDING_EMAIL] run_id={self.run_id}: "
            "State already committed. Resuming from Gmail only. "
            "V4, Policy, and state mutation are FORBIDDEN."
        )

        pd = self.state_adapter.load_prepared_dispatch()
        if not self.state_adapter.validate_prepared_dispatch(pd, self.run_id):
            raise RuntimeError(
                f"PREPARED_DISPATCH_INVALID: run_id={self.run_id} - "
                "State already committed, but prepared dispatch validation failed. "
                "Cannot resume safely - aborting."
            )

        subject = pd["subject"]
        html_content = pd["html_content"]
        report_mode = pd["report_mode"]

        attachments: List = []
        xlsx_bundle = pd.get("xlsx_bundle_path")
        if xlsx_bundle:
            xlsx_file = self.state_dir / xlsx_bundle
            if xlsx_file.exists():
                attachments.append(xlsx_file)

        from src.notifications.gmail_notifier import GmailNotifier
        notifier = GmailNotifier()

        logger.info(
            f"[CloudRunner RESUME] Dispatching Gmail (resume) "
            f"to {notifier.recipient_email} - "
            "best-effort idempotent, duplicate risk minimized."
        )
        self._dispatch_email(notifier, subject, html_content, attachments, self.run_id)

        receipt_path = self._create_receipt(
            run_id=self.run_id,
            dispatch_id=self.dispatch_id,
            notifier=notifier,
            subject=subject,
            bundle_path=bundle_path,
            held_count=0,
        )

        db.record_dispatch_success(
            self.dispatch_id,
            f"RESUMED_{report_mode}",
            getattr(notifier, "recipient_email", "test@test.com"),
            subject,
        )

        logger.info(f"[CloudRunner RESUME] Completed. Receipt={receipt_path}")
        return {
            "status": _SM_RESUME,
            "run_id": self.run_id,
            "dispatch_id": self.dispatch_id,
            "receipt_path": receipt_path,
            "message": "Resumed from prepared dispatch after prior state commit.",
        }

    def _run_fresh(
        self,
        manifest: Dict[str, Any],
        current_generation: Optional[int],
        db: DatabaseManager,
    ) -> Dict[str, Any]:
        """
        Full fresh execution:
        V4 -> validate payload -> save prepared dispatch ->
        CAS commit -> Gmail -> receipt -> SQLite.
        """
        if not self.force and db.is_dispatch_already_sent(self.dispatch_id):
            logger.info(
                f"[ALREADY_COMPLETED] Dispatch ID {self.dispatch_id!r} "
                "was already recorded in SQLite. Skipping."
            )
            return {
                "status": _SM_ALREADY_COMPLETED,
                "run_id": self.run_id,
                "dispatch_id": self.dispatch_id,
                "message": "Duplicate execution skipped via secondary SQLite check.",
            }

        os.environ["STOCKBOT_STATE_DIR"] = str(self.state_dir)
        os.environ["STOCKBOT_CLOUD_MODE"] = "1"

        from main import run_post_market_analysis

        logger.info(f"[CloudRunner FRESH] Executing V4 analysis ({self.report_mode})...")
        res_v4 = run_post_market_analysis(
            force=self.force,
            skip_email=True,
            report_asof=self.kst_now,
            report_session=self.session_code,
            report_run_id=self.run_id,
        )

        if isinstance(res_v4, tuple) and len(res_v4) == 3:
            held_status, caught_signals, report_payload = res_v4
        else:
            held_status = res_v4[0] if res_v4 else []
            caught_signals = res_v4[1] if len(res_v4) > 1 else []
            report_payload = None

        # FIX-2: Fail-Closed - state commit/Gmail/receipt FORBIDDEN if this raises
        _validate_report_payload(report_payload, self.run_id)

        notifier = report_payload["notifier"]
        subject = report_payload["subject"]
        html_report = report_payload["html_report"]
        attachments = report_payload.get("attachments", [])
        dispatch_tag = report_payload["dispatch_tag"]
        fingerprint_id = report_payload.get("fingerprint_id")
        excel_path = report_payload.get("excel_path")

        # Persist prepared dispatch BEFORE CAS commit
        logger.info("[CloudRunner FRESH] Persisting prepared dispatch to state bundle...")
        self.state_adapter.save_prepared_dispatch(
            run_id=self.run_id,
            subject=subject,
            html_content=html_report,
            report_mode=self.report_mode,
            xlsx_path=excel_path,
            extra_meta={
                "dispatch_id": self.dispatch_id,
                "dispatch_tag": dispatch_tag,
                "held_count": len(held_status),
                "signals_count": len(caught_signals),
            },
        )

        self.state_adapter._validate_sqlite_integrity()

        logger.info("[CloudRunner FRESH] Committing state bundle to GCS (CAS)...")
        bundle_path, _ = self.state_adapter.create_and_upload_bundle(
            run_id=self.run_id,
            expected_generation=current_generation,
            metadata={
                "report_mode": self.report_mode,
                "dispatch_id": self.dispatch_id,
                "held_count": len(held_status),
                "signals_count": len(caught_signals),
            },
        )

        logger.info(
            f"[CloudRunner FRESH] Dispatching Gmail to {notifier.recipient_email} - "
            "best-effort idempotent, duplicate risk minimized."
        )
        self._dispatch_email(notifier, subject, html_report, attachments, self.run_id)

        receipt_path = self._create_receipt(
            run_id=self.run_id,
            dispatch_id=self.dispatch_id,
            notifier=notifier,
            subject=subject,
            bundle_path=bundle_path,
            held_count=len(held_status),
        )

        db.record_dispatch_success(
            self.dispatch_id,
            dispatch_tag,
            getattr(notifier, "recipient_email", "test@test.com"),
            subject,
        )
        if fingerprint_id:
            db.record_dispatch_success(
                fingerprint_id,
                dispatch_tag,
                getattr(notifier, "recipient_email", "test@test.com"),
                subject,
            )

        logger.info("=" * 60)
        logger.info("[CloudRunner FRESH] Execution Successfully Completed!")
        logger.info(f"  Committed Bundle: {bundle_path}")
        logger.info(f"  Dispatch Receipt: {receipt_path}")
        logger.info(f"  Run ID: {self.run_id}")
        logger.info("=" * 60)

        return {
            "status": "SUCCESS",
            "run_id": self.run_id,
            "dispatch_id": self.dispatch_id,
            "bundle_path": bundle_path,
            "receipt_path": receipt_path,
            "held_count": len(held_status),
            "signals_count": len(caught_signals),
        }

    def run(self) -> Dict[str, Any]:
        """
        Executes Cloud Run Job with 3-state machine.
        SMTP: best-effort idempotent - duplicate risk minimized.
        """
        logger.info("=" * 60)
        logger.info("[CloudRunner] Starting StockBot Cloud Run Job")
        logger.info(f"  Run ID: {self.run_id}")
        logger.info(f"  Dispatch ID: {self.dispatch_id}")
        logger.info(f"  Report Mode: {self.report_mode} (Session: {self.session_code})")
        logger.info(f"  KST Time: {self.kst_now.strftime('%Y-%m-%d %H:%M:%S KST')}")
        logger.info(f"  State Dir: {self.state_dir}")
        logger.info("=" * 60)

        initial_state = self._detect_state()

        if initial_state == _SM_ALREADY_COMPLETED:
            return self._run_already_completed()

        manifest, current_generation = self.state_adapter.download_current_state()
        bundle_path = manifest.get("bundle_path", "")
        logger.info(
            f"[CloudRunner] Restored state bundle from generation {current_generation}"
        )

        db_path = self.state_dir / "data" / "stock_system.db"
        db = DatabaseManager(db_path=str(db_path))

        # Phase 3: RESUME vs FRESH
        # CRITICAL: If state was committed for this run_id, invalid prepared dispatch
        # MUST fail-closed immediately. Never fall through to FRESH!
        if initial_state == _SM_RESUME:
            pd = self.state_adapter.load_prepared_dispatch()
            if self.state_adapter.validate_prepared_dispatch(pd, self.run_id):
                return self._run_resume(bundle_path, db)
            else:
                err = (
                    f"PREPARED_DISPATCH_INVALID: run_id={self.run_id} - "
                    "State bundle was already committed for this run_id, "
                    "but prepared dispatch is missing, corrupt, or invalid. "
                    "Re-running V4/Policy or falling through to FRESH is strictly forbidden."
                )
                logger.critical(f"[CloudRunner] {err}")
                raise RuntimeError(err)

        return self._run_fresh(manifest, current_generation, db)


def main():
    parser = argparse.ArgumentParser(description="StockBot Cloud Run Job Entrypoint")
    parser.add_argument(
        "--mode", type=str, required=True,
        choices=["INTRADAY", "POSTMARKET", "intraday", "postmarket"],
        help="Report mode",
    )
    parser.add_argument("--force", action="store_true", help="Bypass idempotency check")
    parser.add_argument("--state-dir", type=str, help="State directory")
    parser.add_argument("--bucket", type=str, help="GCS bucket name")
    parser.add_argument("--prefix", type=str, default="stockbot-state", help="GCS prefix")
    args = parser.parse_args()

    runner = CloudRunner(
        report_mode=args.mode,
        bucket_name=args.bucket,
        prefix=args.prefix,
        state_dir=Path(args.state_dir) if args.state_dir else None,
        force=args.force,
    )

    try:
        res = runner.run()
        print(f"CloudRunner Result: {res.get('status')}")
        if res.get("status") in ("SUCCESS", _SM_ALREADY_COMPLETED, _SM_RESUME):
            sys.exit(0)
        else:
            sys.exit(1)
    except Exception as e:
        logger.critical(f"[CloudRunner] Fatal execution error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
