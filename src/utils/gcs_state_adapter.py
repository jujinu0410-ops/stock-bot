"""
src/utils/gcs_state_adapter.py

GCS SQLite State Bundle Adapter
- Downloads state bundle from Cloud Storage and unpacks into /tmp/stockbot
- Verifies SHA-256 manifest and SQLite PRAGMA integrity
- Creates immutable bundle zip and commits current.json with Generation Precondition (CAS)
- Provides Fail-Closed protection against concurrent state overwrite
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.utils.logger import logger

STATE_FILES_TO_BUNDLE = [
    "data/stock_system.db",
    "data/policy_shadow.db",
    "config/portfolio_holdings.json",
]

# Prepared dispatch sub-paths inside the state directory / bundle
PREPARED_DISPATCH_DIR = "prepared_dispatch"
PREPARED_DISPATCH_JSON = "prepared_dispatch/dispatch_payload.json"
PREPARED_DISPATCH_XLSX = "prepared_dispatch/report.xlsx"

MANIFEST_FILENAME = "state_manifest.json"
CURRENT_POINTER_FILENAME = "current.json"


class StateAdapterError(Exception):
    """Base exception for GCS state adapter errors."""
    pass


class StateConflictError(StateAdapterError):
    """Raised when GCS generation precondition fails (CAS conflict)."""
    pass


class CorruptStateError(StateAdapterError):
    """Raised when state bundle archive or manifest checksum is invalid."""
    pass


class StateIntegrityError(StateAdapterError):
    """Raised when SQLite database fails PRAGMA integrity_check."""
    pass


def sha256_file(path: Path) -> str:
    """Computes SHA-256 hexadecimal digest of a file."""
    if not path.exists():
        return "FILE_NOT_FOUND"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def verify_sqlite_integrity(db_path: Path) -> bool:
    """Runs PRAGMA integrity_check on SQLite database file."""
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        res = cur.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        return bool(res and res[0] == "ok")
    except Exception as e:
        logger.error(f"[GCSStateAdapter] SQLite integrity check error on {db_path}: {e}")
        return False


class GCSStateAdapter:
    """
    Manages state synchronization between GCS and local state directory.
    """

    def __init__(
        self,
        bucket_name: Optional[str] = None,
        prefix: str = "stockbot-state",
        state_dir: Optional[Path] = None,
        storage_client: Any = None,
    ):
        self.bucket_name = bucket_name or os.getenv("GCS_BUCKET") or "stockbot-state-bucket"
        self.prefix = prefix.strip("/")
        self.state_dir = Path(state_dir or os.getenv("STOCKBOT_STATE_DIR") or "/tmp/stockbot").resolve()
        self._client = storage_client
        self._init_state_dir()

    def _init_state_dir(self) -> None:
        """Ensures state subdirectories exist."""
        (self.state_dir / "data").mkdir(parents=True, exist_ok=True)
        (self.state_dir / "config").mkdir(parents=True, exist_ok=True)
        (self.state_dir / "logs").mkdir(parents=True, exist_ok=True)

    def _get_storage_client(self):
        if self._client is not None:
            return self._client
        if os.getenv("STOCKBOT_TEST_MODE") == "1":
            raise StateAdapterError(
                "[TEST_MODE] Real GCS client invocation blocked in TEST_MODE. Injected mock client required."
            )
        try:
            from google.cloud import storage
            self._client = storage.Client()
            return self._client
        except Exception as e:
            logger.error(f"[GCSStateAdapter] Failed to initialize google-cloud-storage client: {e}")
            raise StateAdapterError(f"Cloud Storage client init failed: {e}") from e

    def download_current_state(self) -> Tuple[Dict[str, Any], Optional[int]]:
        """
        Downloads current state bundle specified by current.json, unpacks to state_dir,
        and validates file checksums and SQLite integrity.

        Returns:
            (manifest_dict, generation_id)
        """
        logger.info(f"[GCSStateAdapter] Fetching state pointer gs://{self.bucket_name}/{self.prefix}/{CURRENT_POINTER_FILENAME}")
        client = self._get_storage_client()
        bucket = client.bucket(self.bucket_name)

        pointer_blob = bucket.blob(f"{self.prefix}/{CURRENT_POINTER_FILENAME}")
        if not pointer_blob.exists():
            err = f"State pointer blob '{self.prefix}/{CURRENT_POINTER_FILENAME}' not found in bucket '{self.bucket_name}'."
            logger.critical(f"[GCSStateAdapter] 🛑 {err}")
            raise CorruptStateError(err)

        pointer_blob.reload()
        generation = pointer_blob.generation
        pointer_bytes = pointer_blob.download_as_bytes()

        try:
            pointer_data = json.loads(pointer_bytes.decode("utf-8"))
        except Exception as e:
            raise CorruptStateError(f"Failed to parse pointer JSON: {e}") from e

        bundle_path = pointer_data.get("bundle_path")
        expected_bundle_sha256 = pointer_data.get("bundle_sha256")
        run_id = pointer_data.get("run_id")

        if not bundle_path:
            raise CorruptStateError("Pointer data missing 'bundle_path'")

        logger.info(f"[GCSStateAdapter] Downloading state bundle '{bundle_path}' (Run ID: {run_id}, Gen: {generation})...")
        bundle_blob = bucket.blob(bundle_path)
        if not bundle_blob.exists():
            raise CorruptStateError(f"Referenced state bundle '{bundle_path}' does not exist.")

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_zip:
            tmp_zip_path = Path(tmp_zip.name)

        try:
            bundle_blob.download_to_filename(str(tmp_zip_path))

            # Verify bundle zip checksum
            actual_bundle_sha256 = sha256_file(tmp_zip_path)
            if expected_bundle_sha256 and actual_bundle_sha256 != expected_bundle_sha256:
                raise CorruptStateError(
                    f"State bundle zip checksum mismatch: expected {expected_bundle_sha256}, got {actual_bundle_sha256}"
                )

            # Safe Unpack
            self._unpack_bundle(tmp_zip_path)

            # Manifest verification
            manifest_path = self.state_dir / MANIFEST_FILENAME
            if not manifest_path.exists():
                raise CorruptStateError(f"Unpacked state bundle missing '{MANIFEST_FILENAME}'")

            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest_data = json.load(f)

            self._validate_manifest(manifest_data)
            self._validate_sqlite_integrity()

            logger.info(f"✅ [GCSStateAdapter] State bundle successfully restored into {self.state_dir}")
            return manifest_data, generation
        finally:
            if tmp_zip_path.exists():
                try:
                    os.unlink(tmp_zip_path)
                except Exception:
                    pass

    def _unpack_bundle(self, zip_path: Path) -> None:
        """Safely extracts zip contents into state_dir without path traversal."""
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                norm_name = os.path.normpath(member.filename)
                if norm_name.startswith("..") or os.path.isabs(norm_name):
                    raise CorruptStateError(f"Illegal path in zip archive: {member.filename}")
                target_path = self.state_dir / norm_name
                if member.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as source, open(target_path, "wb") as target:
                        shutil.copyfileobj(source, target)

    def _validate_manifest(self, manifest: Dict[str, Any]) -> None:
        """Validates that all expected state files exist and match manifest SHA-256."""
        checksums = manifest.get("files", {})
        if not checksums:
            raise CorruptStateError("State manifest contains empty 'files' mapping.")

        for rel_file, expected_hash in checksums.items():
            file_path = self.state_dir / rel_file
            if not file_path.exists():
                raise CorruptStateError(f"Required state file missing: {rel_file}")
            actual_hash = sha256_file(file_path)
            if actual_hash != expected_hash:
                raise CorruptStateError(
                    f"Checksum mismatch for '{rel_file}': manifest={expected_hash}, actual={actual_hash}"
                )

    def _validate_sqlite_integrity(self) -> None:
        """Validates SQLite database integrity for both operational and policy shadow DBs."""
        for db_name in ["data/stock_system.db", "data/policy_shadow.db"]:
            db_file = self.state_dir / db_name
            if db_file.exists():
                if not verify_sqlite_integrity(db_file):
                    raise StateIntegrityError(f"PRAGMA integrity_check failed on {db_name}")

    def create_and_upload_bundle(
        self,
        run_id: str,
        expected_generation: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Validates local state files, builds manifest and zip bundle, uploads to GCS,
        and atomically updates current.json with Generation Precondition (CAS).

        Returns:
            (bundle_gcs_path, manifest_dict)
        """
        logger.info(f"[GCSStateAdapter] Starting state commit for Run ID: {run_id} (Expected Gen: {expected_generation})...")

        # 1. Validate SQLite integrity prior to packing
        self._validate_sqlite_integrity()

        # 2. Build file manifest
        file_hashes: Dict[str, str] = {}
        for rel_path in STATE_FILES_TO_BUNDLE:
            p = self.state_dir / rel_path
            if not p.exists():
                raise StateIntegrityError(f"Cannot commit state: required file '{rel_path}' is missing.")
            file_hashes[rel_path] = sha256_file(p)

        now_kst_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        manifest_data = {
            "schema_version": "1.0",
            "run_id": run_id,
            "created_at_kst": now_kst_str,
            "files": file_hashes,
            "metadata": metadata or {},
        }

        # Write manifest file
        manifest_file = self.state_dir / MANIFEST_FILENAME
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, ensure_ascii=False, indent=2)

        # 3. Create zip bundle archive
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        bundle_filename = f"state_{ts_str}_{run_id}.zip"
        bundle_gcs_path = f"{self.prefix}/bundles/{bundle_filename}"

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_zip:
            tmp_zip_path = Path(tmp_zip.name)

        try:
            with zipfile.ZipFile(tmp_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                # Add manifest
                zf.write(manifest_file, arcname=MANIFEST_FILENAME)
                # Add state files
                for rel_path in STATE_FILES_TO_BUNDLE:
                    zf.write(self.state_dir / rel_path, arcname=rel_path)
                # Add prepared_dispatch/ contents if present
                pd_dir = self.state_dir / PREPARED_DISPATCH_DIR
                if pd_dir.exists():
                    for pd_file in sorted(pd_dir.iterdir()):
                        if pd_file.is_file():
                            arcname = f"{PREPARED_DISPATCH_DIR}/{pd_file.name}"
                            zf.write(pd_file, arcname=arcname)

            bundle_sha256 = sha256_file(tmp_zip_path)
            bundle_size = tmp_zip_path.stat().st_size

            logger.info(f"[GCSStateAdapter] Created state bundle {bundle_filename} ({bundle_size:,} bytes, sha256={bundle_sha256[:12]}...)")

            client = self._get_storage_client()
            bucket = client.bucket(self.bucket_name)

            # 4. Upload immutable bundle zip
            bundle_blob = bucket.blob(bundle_gcs_path)
            bundle_blob.upload_from_filename(str(tmp_zip_path))
            logger.info(f"✅ [GCSStateAdapter] Uploaded immutable state bundle to gs://{self.bucket_name}/{bundle_gcs_path}")

            # 5. Prepare pointer JSON
            pointer_data = {
                "bundle_path": bundle_gcs_path,
                "bundle_sha256": bundle_sha256,
                "run_id": run_id,
                "created_at_kst": now_kst_str,
                "manifest": manifest_data,
            }
            pointer_json_bytes = json.dumps(pointer_data, ensure_ascii=False, indent=2).encode("utf-8")

            # 6. Commit pointer with Generation Precondition (CAS)
            pointer_blob = bucket.blob(f"{self.prefix}/{CURRENT_POINTER_FILENAME}")
            try:
                if expected_generation is not None:
                    pointer_blob.upload_from_string(
                        pointer_json_bytes,
                        content_type="application/json",
                        if_generation_match=expected_generation,
                    )
                else:
                    pointer_blob.upload_from_string(
                        pointer_json_bytes,
                        content_type="application/json",
                    )
                logger.info(f"✅ [GCSStateAdapter] Atomically updated current state pointer with CAS condition (Gen: {expected_generation})")
            except Exception as e_cas:
                err_msg = f"CAS Update failed for {CURRENT_POINTER_FILENAME} (expected gen={expected_generation}): {e_cas}"
                logger.critical(f"🛑 [GCSStateAdapter] {err_msg}")
                raise StateConflictError(err_msg) from e_cas

            return bundle_gcs_path, manifest_data
        finally:
            if tmp_zip_path.exists():
                try:
                    os.unlink(tmp_zip_path)
                except Exception:
                    pass

    def get_dispatch_receipt_path(self, run_id: str) -> str:
        """Returns GCS object path for dispatch receipt."""
        return f"{self.prefix}/dispatch_receipts/{run_id}.json"

    def has_dispatch_receipt(self, run_id: str) -> bool:
        """Checks if an immutable dispatch receipt exists on GCS for run_id."""
        client = self._get_storage_client()
        bucket = client.bucket(self.bucket_name)
        receipt_blob = bucket.blob(self.get_dispatch_receipt_path(run_id))
        return receipt_blob.exists()

    def create_dispatch_receipt(
        self,
        run_id: str,
        receipt_data: Dict[str, Any],
    ) -> str:
        """
        Creates an immutable dispatch receipt on GCS with if_generation_match=0
        to atomically prevent duplicate dispatch records for the same run_id.
        """
        path = self.get_dispatch_receipt_path(run_id)
        client = self._get_storage_client()
        bucket = client.bucket(self.bucket_name)
        receipt_blob = bucket.blob(path)

        receipt_bytes = json.dumps(receipt_data, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            receipt_blob.upload_from_string(
                receipt_bytes,
                content_type="application/json",
                if_generation_match=0,
            )
            logger.info(f"✅ [GCSStateAdapter] Created immutable dispatch receipt gs://{self.bucket_name}/{path}")
            return path
        except Exception as e:
            err_msg = f"Failed to create dispatch receipt for {run_id} (blob may already exist): {e}"
            logger.error(f"[GCSStateAdapter] {err_msg}")
            raise StateConflictError(err_msg) from e

    # -------------------------------------------------------------------------
    # Prepared Dispatch — persist email payload before state CAS commit
    # -------------------------------------------------------------------------

    def save_prepared_dispatch(
        self,
        run_id: str,
        subject: str,
        html_content: str,
        report_mode: str,
        xlsx_path: Optional[Path],
        extra_meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Persists the email payload (subject, HTML, XLSX) into
        state_dir/prepared_dispatch/ before the GCS CAS commit.

        Returns the SHA-256 of the JSON payload for manifest inclusion.
        """
        pd_dir = self.state_dir / PREPARED_DISPATCH_DIR
        pd_dir.mkdir(parents=True, exist_ok=True)

        # Copy XLSX into prepared_dispatch/
        xlsx_in_bundle: Optional[str] = None
        if xlsx_path and Path(xlsx_path).exists():
            dest = self.state_dir / PREPARED_DISPATCH_XLSX
            import shutil as _shutil
            _shutil.copy2(str(xlsx_path), str(dest))
            xlsx_in_bundle = PREPARED_DISPATCH_XLSX

        payload = {
            "run_id": run_id,
            "report_mode": report_mode,
            "subject": subject,
            "html_content": html_content,
            "xlsx_bundle_path": xlsx_in_bundle,
            "created_at_kst": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **(extra_meta or {}),
        }
        payload_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
        payload["payload_sha256"] = payload_sha256

        payload_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        with open(self.state_dir / PREPARED_DISPATCH_JSON, "wb") as f:
            f.write(payload_bytes)

        logger.info(
            f"[GCSStateAdapter] Prepared dispatch saved for run_id={run_id} "
            f"sha256={payload_sha256[:12]}..."
        )
        return payload_sha256

    def load_prepared_dispatch(self) -> Optional[Dict[str, Any]]:
        """
        Loads the prepared dispatch payload from state_dir/prepared_dispatch/.
        Returns None if the file does not exist.
        """
        json_path = self.state_dir / PREPARED_DISPATCH_JSON
        if not json_path.exists():
            return None
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[GCSStateAdapter] Failed to load prepared dispatch JSON: {e}")
            return None

    def validate_prepared_dispatch(
        self,
        payload: Dict[str, Any],
        run_id: str,
    ) -> bool:
        """
        Validates prepared dispatch payload:
        - run_id matches current run
        - required fields present (subject, html_content, report_mode)
        - SHA-256 of stored content matches recorded hash
        - XLSX file exists in bundle if listed

        Returns True if valid, False otherwise (Fail-Closed).
        """
        if payload is None:
            logger.error("[GCSStateAdapter] Prepared dispatch validation failed: payload is None")
            return False

        # run_id match
        if payload.get("run_id") != run_id:
            logger.error(
                f"[GCSStateAdapter] Prepared dispatch run_id mismatch: "
                f"payload={payload.get('run_id')} expected={run_id}"
            )
            return False

        # required fields
        for field in ("subject", "html_content", "report_mode", "payload_sha256"):
            if not payload.get(field):
                logger.error(f"[GCSStateAdapter] Prepared dispatch missing required field: {field}")
                return False

        # SHA-256 recompute
        recorded_sha256 = payload["payload_sha256"]
        check_payload = {k: v for k, v in payload.items() if k != "payload_sha256"}
        check_bytes = json.dumps(check_payload, ensure_ascii=False, indent=2).encode("utf-8")
        actual_sha256 = hashlib.sha256(check_bytes).hexdigest()
        if actual_sha256 != recorded_sha256:
            logger.error(
                f"[GCSStateAdapter] Prepared dispatch SHA-256 mismatch: "
                f"stored={recorded_sha256[:12]} actual={actual_sha256[:12]}"
            )
            return False

        # XLSX existence check
        xlsx_bundle = payload.get("xlsx_bundle_path")
        if xlsx_bundle:
            xlsx_file = self.state_dir / xlsx_bundle
            if not xlsx_file.exists():
                logger.error(f"[GCSStateAdapter] Prepared dispatch XLSX missing: {xlsx_file}")
                return False

        logger.info(
            f"✅ [GCSStateAdapter] Prepared dispatch validated for run_id={run_id} "
            f"sha256={recorded_sha256[:12]}..."
        )
        return True

    def get_committed_run_id(self) -> Optional[str]:
        """
        Reads current.json from GCS and returns the committed run_id.
        Returns None if current.json does not exist or cannot be parsed.
        Used by the state machine to detect RESUME_PENDING_EMAIL state.
        """
        try:
            client = self._get_storage_client()
            bucket = client.bucket(self.bucket_name)
            pointer_blob = bucket.blob(f"{self.prefix}/{CURRENT_POINTER_FILENAME}")
            if not pointer_blob.exists():
                return None
            data = json.loads(pointer_blob.download_as_bytes().decode("utf-8"))
            return data.get("run_id")
        except Exception as e:
            logger.warning(f"[GCSStateAdapter] Could not read committed run_id: {e}")
            return None

