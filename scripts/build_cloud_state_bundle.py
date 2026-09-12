"""
scripts/build_cloud_state_bundle.py

StockBot Cloud Run Phase 1 - Authoritative State Bootstrap Tool
- Safely snapshots local operational SQLite databases via sqlite3 online backup API.
- Preflight source integrity checks (PRAGMA integrity_check, table checks, JSON sync).
- Builds cloud_bootstrap_export directory and stockbot_state_bootstrap.zip.
- Validates candidate bundle integrity, hashes, and manifest.
- Zero network I/O & Zero operational state mutation.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent

# Paths to authoritative local operational state files
OP_STOCK_DB = BASE_DIR / "data" / "stock_system.db"
OP_POLICY_DB = BASE_DIR / "data" / "policy_shadow.db"
OP_HOLDINGS_JSON = BASE_DIR / "config" / "portfolio_holdings.json"

KST = timezone(timedelta(hours=9))


def sha256_file(path: Path) -> str:
    """Computes SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def verify_sqlite_integrity_ro(db_path: Path) -> Tuple[bool, str]:
    """Runs PRAGMA integrity_check in read-only URI mode on an SQLite database."""
    if not db_path.exists():
        return False, "FILE_NOT_FOUND"
    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        try:
            cur = conn.cursor()
            rows = cur.execute("PRAGMA integrity_check;").fetchall()
            if rows and rows[0][0] == "ok":
                return True, "ok"
            err_msg = "; ".join(r[0] for r in rows) if rows else "EMPTY_RESULT"
            return False, err_msg
        finally:
            conn.close()
    except Exception as e:
        return False, str(e)


def snapshot_sqlite_online_backup(src_path: Path, dst_path: Path) -> None:
    """
    Creates a consistent SQLite snapshot using the official online backup API.
    Avoids raw file copy during potential concurrent reads/writes.
    """
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if dst_path.exists():
        dst_path.unlink()

    src_conn = sqlite3.connect(f"file:{src_path.resolve()}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(str(dst_path))
    try:
        with dst_conn:
            src_conn.backup(dst_conn, pages=100)
    finally:
        dst_conn.close()
        src_conn.close()


def preflight_source_validation() -> Dict[str, Any]:
    """
    Preflight validation on live operational files.
    Fails closed with BOOTSTRAP_SOURCE_INVALID if any corruption or mismatch is detected.
    """
    print("\n" + "=" * 70)
    print("[PREFLIGHT] 1. Validating Authoritative Local Source Files (READ-ONLY)")
    print("=" * 70)

    # 1. stock_system.db check
    if not OP_STOCK_DB.exists():
        raise RuntimeError(f"BOOTSTRAP_SOURCE_INVALID: {OP_STOCK_DB} does not exist!")
    ok, msg = verify_sqlite_integrity_ro(OP_STOCK_DB)
    if not ok:
        raise RuntimeError(f"BOOTSTRAP_SOURCE_INVALID: {OP_STOCK_DB} integrity check failed: {msg}")
    print(f"  - stock_system.db integrity: ok ({OP_STOCK_DB.stat().st_size:,} bytes)")

    # Verify key operational tables exist in stock_system.db
    conn = sqlite3.connect(f"file:{OP_STOCK_DB.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        required_tables = {"portfolio_positions", "stock_info", "dispatch_history", "trading_signals"}
        missing_tables = required_tables - tables
        if missing_tables:
            raise RuntimeError(f"BOOTSTRAP_SOURCE_INVALID: Missing required tables in stock_system.db: {missing_tables}")

        # Active holdings from DB
        db_held = cur.execute("""
            SELECT p.stock_code, s.stock_name, p.quantity, p.avg_buy_price, p.anchor_price_p0, p.anchor_atr_a0,
                   p.ratchet_stop, p.confirmed_stop_price, p.effective_exit_line, p.trade_mode, p.position_cycle_id
            FROM portfolio_positions p
            LEFT JOIN stock_info s ON p.stock_code = s.stock_code
            WHERE p.quantity > 0
            ORDER BY p.stock_code
        """).fetchall()
        db_holdings = [dict(r) for r in db_held]
    finally:
        conn.close()

    # 2. policy_shadow.db check
    if not OP_POLICY_DB.exists():
        raise RuntimeError(f"BOOTSTRAP_SOURCE_INVALID: {OP_POLICY_DB} does not exist!")
    ok, msg = verify_sqlite_integrity_ro(OP_POLICY_DB)
    if not ok:
        raise RuntimeError(f"BOOTSTRAP_SOURCE_INVALID: {OP_POLICY_DB} integrity check failed: {msg}")
    print(f"  - policy_shadow.db integrity: ok ({OP_POLICY_DB.stat().st_size:,} bytes)")

    # 3. portfolio_holdings.json check
    if not OP_HOLDINGS_JSON.exists():
        raise RuntimeError(f"BOOTSTRAP_SOURCE_INVALID: {OP_HOLDINGS_JSON} does not exist!")
    try:
        with open(OP_HOLDINGS_JSON, "r", encoding="utf-8") as f:
            json_holdings = json.load(f)
    except Exception as e:
        raise RuntimeError(f"BOOTSTRAP_SOURCE_INVALID: Failed to parse {OP_HOLDINGS_JSON}: {e}")

    # Validate cross-consistency between DB holdings and JSON holdings
    db_codes = {r["stock_code"]: r["quantity"] for r in db_holdings}
    json_codes = {r["stock_code"]: r.get("quantity") for r in json_holdings if (r.get("quantity") or 0) > 0}

    if set(db_codes.keys()) != set(json_codes.keys()):
        raise RuntimeError(
            f"BOOTSTRAP_SOURCE_INVALID: Holdings mismatch between DB ({set(db_codes.keys())}) and JSON ({set(json_codes.keys())})"
        )

    for code, qty in db_codes.items():
        if json_codes[code] != qty:
            raise RuntimeError(
                f"BOOTSTRAP_SOURCE_INVALID: Quantity mismatch for {code}: DB={qty} vs JSON={json_codes[code]}"
            )

    print(f"  - portfolio_holdings.json integrity: ok (Active holdings: {len(json_holdings)})")
    print(f"  - DB vs JSON holdings parity: 100% matched ({len(db_codes)} stocks)")
    print("=" * 70)

    return {
        "db_holdings": db_holdings,
        "json_holdings": json_holdings,
        "holdings_count": len(db_holdings),
    }


def create_bootstrap_bundle(
    staging_dir: Path,
    output_zip: Path,
    bundle_type: str = "INITIAL_BOOTSTRAP",
) -> Dict[str, Any]:
    """
    Creates cloud_bootstrap_export staging files, state_manifest.json, and the ZIP bundle.
    """
    preflight = preflight_source_validation()
    db_holdings = preflight["db_holdings"]
    holdings_count = preflight["holdings_count"]

    now_kst = datetime.now(KST)
    now_utc = now_kst.astimezone(timezone.utc)
    created_at_kst = now_kst.strftime("%Y-%m-%d %H:%M:%S")
    created_at_utc = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    print("\n" + "=" * 70)
    print(f"[BOOTSTRAP] 2. Creating Consistent Snapshots via SQLite Online Backup API")
    print("=" * 70)

    # 1. Prepare staging directory
    if staging_dir.exists():
        import shutil
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    staged_stock_db = staging_dir / "data" / "stock_system.db"
    staged_policy_db = staging_dir / "data" / "policy_shadow.db"
    staged_holdings_json = staging_dir / "config" / "portfolio_holdings.json"
    staged_manifest_file = staging_dir / "state_manifest.json"

    # 2. Perform online backups for SQLite DBs
    print(f"  - Snapshotting {OP_STOCK_DB.name} -> {staged_stock_db.relative_to(staging_dir)}")
    snapshot_sqlite_online_backup(OP_STOCK_DB, staged_stock_db)

    print(f"  - Snapshotting {OP_POLICY_DB.name} -> {staged_policy_db.relative_to(staging_dir)}")
    snapshot_sqlite_online_backup(OP_POLICY_DB, staged_policy_db)

    # 3. Snapshot portfolio_holdings.json
    print(f"  - Copying {OP_HOLDINGS_JSON.name} -> {staged_holdings_json.relative_to(staging_dir)}")
    staged_holdings_json.parent.mkdir(parents=True, exist_ok=True)
    with open(OP_HOLDINGS_JSON, "r", encoding="utf-8") as f_src, open(staged_holdings_json, "w", encoding="utf-8") as f_dst:
        f_dst.write(f_src.read())

    # 4. Verify integrity of snapshot files
    print("\n" + "=" * 70)
    print("[VALIDATION] 3. Verifying Snapshot Integrity & Computing Hashes")
    print("=" * 70)

    ok, msg = verify_sqlite_integrity_ro(staged_stock_db)
    if not ok:
        raise RuntimeError(f"Snapshot integrity check failed on staged stock_system.db: {msg}")
    print(f"  - Staged stock_system.db integrity: ok")

    ok, msg = verify_sqlite_integrity_ro(staged_policy_db)
    if not ok:
        raise RuntimeError(f"Snapshot integrity check failed on staged policy_shadow.db: {msg}")
    print(f"  - Staged policy_shadow.db integrity: ok")

    # Compute manifest file entries
    # Runtime canonical contract requires "files": { relative_path: sha256_string }
    files_manifest = {
        "data/stock_system.db": sha256_file(staged_stock_db),
        "data/policy_shadow.db": sha256_file(staged_policy_db),
        "config/portfolio_holdings.json": sha256_file(staged_holdings_json),
    }

    # Detailed file metadata preserved under "file_details" for auditability
    file_details = {
        "data/stock_system.db": {
            "relative_path": "data/stock_system.db",
            "size_bytes": staged_stock_db.stat().st_size,
            "sha256": sha256_file(staged_stock_db),
            "integrity": "ok",
        },
        "data/policy_shadow.db": {
            "relative_path": "data/policy_shadow.db",
            "size_bytes": staged_policy_db.stat().st_size,
            "sha256": sha256_file(staged_policy_db),
            "integrity": "ok",
        },
        "config/portfolio_holdings.json": {
            "relative_path": "config/portfolio_holdings.json",
            "size_bytes": staged_holdings_json.stat().st_size,
            "sha256": sha256_file(staged_holdings_json),
            "integrity": "ok",
        },
    }

    manifest = {
        "schema_version": "1.0",
        "bundle_type": bundle_type,
        "created_at_kst": created_at_kst,
        "created_at_utc": created_at_utc,
        "source": "LOCAL_OPERATIONAL_STATE",
        "intended_cloud_region": "asia-northeast3",
        "stock_system_integrity": "ok",
        "policy_shadow_integrity": "ok",
        "holdings_count": holdings_count,
        "source_snapshot_method": "sqlite_online_backup",
        "cloud_policy_lineage": "BOOTSTRAP_FROM_LOCAL_THEN_CLOUD_AUTHORITATIVE",
        "previous_bundle": None,
        "files": files_manifest,
        "file_details": file_details,
    }

    with open(staged_manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"  - Staged state_manifest.json created ({staged_manifest_file.stat().st_size:,} bytes)")

    # 5. Pack into ZIP bundle
    print("\n" + "=" * 70)
    print(f"[PACKAGE] 4. Building Candidate ZIP: {output_zip.name}")
    print("=" * 70)

    output_zip.parent.mkdir(parents=True, exist_ok=True)
    if output_zip.exists():
        output_zip.unlink()

    entries_to_pack = [
        ("data/stock_system.db", staged_stock_db),
        ("data/policy_shadow.db", staged_policy_db),
        ("config/portfolio_holdings.json", staged_holdings_json),
        ("state_manifest.json", staged_manifest_file),
    ]

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, filepath in entries_to_pack:
            zf.write(filepath, arcname=arcname)
            print(f"  + Added to ZIP: {arcname}")

    zip_size = output_zip.stat().st_size
    zip_sha = sha256_file(output_zip)

    # 6. Post-creation Exhaustive Verification of ZIP
    print("\n" + "=" * 70)
    print("[POST-CHECK] 5. Exhaustive Post-Creation Verification of ZIP Artifact")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp_extract_dir:
        tmp_extract_path = Path(tmp_extract_dir)
        with zipfile.ZipFile(output_zip, "r") as zf:
            zip_entries = zf.namelist()
            expected_entries = {
                "data/stock_system.db",
                "data/policy_shadow.db",
                "config/portfolio_holdings.json",
                "state_manifest.json",
            }
            if set(zip_entries) != expected_entries:
                raise RuntimeError(
                    f"ZIP entries mismatch! Expected {expected_entries}, found {set(zip_entries)}"
                )

            zf.extractall(tmp_extract_path)

        # Verify extracted DBs
        extracted_stock = tmp_extract_path / "data" / "stock_system.db"
        extracted_policy = tmp_extract_path / "data" / "policy_shadow.db"
        extracted_json = tmp_extract_path / "config" / "portfolio_holdings.json"
        extracted_manifest = tmp_extract_path / "state_manifest.json"

        ok, msg = verify_sqlite_integrity_ro(extracted_stock)
        assert ok, f"Extracted stock_system.db corruption: {msg}"

        ok, msg = verify_sqlite_integrity_ro(extracted_policy)
        assert ok, f"Extracted policy_shadow.db corruption: {msg}"

        # Verify JSON
        with open(extracted_json, "r", encoding="utf-8") as f:
            ej = json.load(f)
        assert len(ej) == holdings_count, f"Extracted JSON holdings count mismatch: {len(ej)} vs {holdings_count}"

        # Verify Manifest using exact runtime canonical contract
        with open(extracted_manifest, "r", encoding="utf-8") as f:
            em = json.load(f)

        for rel_path, expected_hash in em["files"].items():
            assert isinstance(expected_hash, str), (
                f"files mapping value for '{rel_path}' must be a str SHA-256, got {type(expected_hash).__name__}"
            )
            extracted_file = tmp_extract_path / rel_path
            assert extracted_file.exists(), f"Extracted file '{rel_path}' missing"
            actual_hash = sha256_file(extracted_file)
            assert actual_hash == expected_hash, (
                f"Checksum mismatch for '{rel_path}': expected {expected_hash}, got {actual_hash}"
            )

        # Verify full runtime GCSStateAdapter compatibility
        sys.path.insert(0, str(BASE_DIR))
        from src.utils.gcs_state_adapter import GCSStateAdapter
        runtime_adapter = GCSStateAdapter(
            bucket_name="test-bucket",
            prefix="stockbot-state",
            state_dir=tmp_extract_path,
        )
        runtime_adapter._validate_manifest(em)
        runtime_adapter._validate_sqlite_integrity()

    print("  - All 4 entries verified successfully inside ZIP.")
    print("  - Extracted SQLite integrity: 100% OK.")
    print("  - Manifest SHA-256 match: 100% OK.")
    print("  - No extraneous or forbidden files in ZIP.")
    print("=" * 70)

    return {
        "staging_dir": staging_dir,
        "output_zip": output_zip,
        "zip_size": zip_size,
        "zip_sha": zip_sha,
        "manifest": manifest,
        "db_holdings": db_holdings,
    }


def main():
    parser = argparse.ArgumentParser(description="StockBot Authoritative State Bootstrap Tool")
    parser.add_argument(
        "--staging-dir",
        type=str,
        default=str(BASE_DIR / "cloud_bootstrap_export"),
        help="Staging directory for exported state files",
    )
    parser.add_argument(
        "--output-zip",
        type=str,
        default=str(BASE_DIR / "cloud_bootstrap_export" / "stockbot_state_bootstrap.zip"),
        help="Output ZIP path for candidate bootstrap bundle",
    )
    args = parser.parse_args()

    # Measure PRE operational hashes
    pre_hashes = {
        "stock_system.db": sha256_file(OP_STOCK_DB) if OP_STOCK_DB.exists() else None,
        "policy_shadow.db": sha256_file(OP_POLICY_DB) if OP_POLICY_DB.exists() else None,
        "portfolio_holdings.json": sha256_file(OP_HOLDINGS_JSON) if OP_HOLDINGS_JSON.exists() else None,
    }

    result = create_bootstrap_bundle(
        staging_dir=Path(args.staging_dir),
        output_zip=Path(args.output_zip),
    )

    # Measure POST operational hashes
    post_hashes = {
        "stock_system.db": sha256_file(OP_STOCK_DB) if OP_STOCK_DB.exists() else None,
        "policy_shadow.db": sha256_file(OP_POLICY_DB) if OP_POLICY_DB.exists() else None,
        "portfolio_holdings.json": sha256_file(OP_HOLDINGS_JSON) if OP_HOLDINGS_JSON.exists() else None,
    }

    assert pre_hashes == post_hashes, f"Operational state mutated during bootstrap! PRE={pre_hashes} POST={post_hashes}"

    print("\n" + "=" * 70)
    print("[SUCCESS] STATE BOOTSTRAP CANDIDATE BUNDLE GENERATION COMPLETED")
    print("=" * 70)
    print(f"Staging Dir  : {result['staging_dir'].resolve()}")
    print(f"Candidate ZIP: {result['output_zip'].resolve()}")
    print(f"ZIP Size     : {result['zip_size']:,} bytes ({result['zip_size']/1024:.1f} KB)")
    print(f"ZIP SHA-256  : {result['zip_sha']}")
    print(f"Created At   : {result['manifest']['created_at_kst']} KST")
    print(f"Active Stocks: {len(result['db_holdings'])} holdings")
    print("=" * 70)


if __name__ == "__main__":
    main()
