from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

import google.auth
from google.auth import impersonated_credentials
from google.auth.transport.requests import AuthorizedSession

CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


def _session(*scopes: str) -> AuthorizedSession:
    requested = list(dict.fromkeys(scopes))
    source_credentials, _ = google.auth.default(scopes=[CLOUD_SCOPE])

    # Cloud Run metadata credentials normally carry cloud-platform scope only.
    # For consumer Google Drive/Sheets access, generate a short-lived OAuth token
    # scoped to Drive by impersonating the same runtime service account. The SA must
    # have Service Account Token Creator on itself; Drive still limits access to files
    # explicitly shared with that service-account email.
    if DRIVE_SCOPE in requested:
        principal = (os.environ.get("V8_SPECIAL_DRIVE_PRINCIPAL") or "").strip()
        if not principal:
            raise RuntimeError("V8_SPECIAL_DRIVE_PRINCIPAL_MISSING")
        credentials = impersonated_credentials.Credentials(
            source_credentials=source_credentials,
            target_principal=principal,
            target_scopes=requested,
            lifetime=1800,
        )
    else:
        credentials = source_credentials
    return AuthorizedSession(credentials)


def _raise_for_status(response, label: str) -> None:
    if 200 <= response.status_code < 300:
        return
    text = (response.text or "")[:600]
    raise RuntimeError(f"{label}:HTTP_{response.status_code}:{text}")


def _gcs_media_url(bucket: str, object_name: str) -> str:
    return (
        "https://storage.googleapis.com/storage/v1/b/"
        f"{quote(bucket, safe='')}/o/{quote(object_name, safe='')}?alt=media"
    )


def read_gcs_json(bucket: str, object_name: str, default: Dict[str, Any] | None = None) -> Dict[str, Any]:
    response = _session(CLOUD_SCOPE).get(_gcs_media_url(bucket, object_name), timeout=30)
    if response.status_code == 404 and default is not None:
        return dict(default)
    _raise_for_status(response, f"GCS_READ:{object_name}")
    return response.json()


def write_gcs_json(bucket: str, object_name: str, payload: Dict[str, Any]) -> None:
    url = f"https://storage.googleapis.com/upload/storage/v1/b/{quote(bucket, safe='')}/o"
    response = _session(CLOUD_SCOPE).post(
        url,
        params={"uploadType": "media", "name": object_name},
        data=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=30,
    )
    _raise_for_status(response, f"GCS_WRITE:{object_name}")


def _validate_bundle_freshness(current_data: Dict[str, Any], now: datetime) -> None:
    raw = str(current_data.get("created_at_kst") or "").strip()
    if not raw:
        raise RuntimeError("GCS_CURRENT_CREATED_AT_MISSING")
    try:
        created = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        if now.tzinfo is not None:
            created = created.replace(tzinfo=now.tzinfo)
    except ValueError as exc:
        raise RuntimeError(f"GCS_CURRENT_CREATED_AT_INVALID:{raw}") from exc

    age_hours = (now - created).total_seconds() / 3600.0
    weekday = now.weekday()
    max_hours = 84.0 if weekday == 0 else 72.0 if weekday in (5, 6) else 36.0
    if age_hours < -0.1 or age_hours > max_hours:
        raise RuntimeError(
            f"GCS_CURRENT_STALE:age_hours={age_hours:.1f}:max_hours={max_hours:.1f}:created={raw}"
        )


def load_holdings_from_stockbot_bundle(
    bucket: str,
    current_object: str,
    now: datetime,
) -> List[Dict[str, Any]]:
    session = _session(CLOUD_SCOPE)
    current_response = session.get(_gcs_media_url(bucket, current_object), timeout=30)
    _raise_for_status(current_response, "GCS_CURRENT_READ")
    current_data = current_response.json()
    _validate_bundle_freshness(current_data, now)

    bundle_path = str(current_data.get("bundle_path") or "").strip()
    if not bundle_path:
        raise RuntimeError("GCS_CURRENT_BUNDLE_PATH_MISSING")

    bundle_response = session.get(_gcs_media_url(bucket, bundle_path), timeout=60)
    _raise_for_status(bundle_response, "GCS_BUNDLE_READ")

    try:
        with zipfile.ZipFile(io.BytesIO(bundle_response.content), "r") as archive:
            db_member = "data/stock_system.db"
            if db_member not in archive.namelist():
                raise RuntimeError("GCS_BUNDLE_DB_MISSING")
            db_bytes = archive.read(db_member)
    except zipfile.BadZipFile as exc:
        raise RuntimeError("GCS_BUNDLE_BAD_ZIP") from exc

    with tempfile.TemporaryDirectory(prefix="v8-portfolio-special-") as tmp:
        db_path = Path(tmp) / "stock_system.db"
        db_path.write_bytes(db_bytes)
        conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT p.stock_code, s.stock_name, p.quantity, p.avg_buy_price, p.updated_at
                FROM portfolio_positions p
                JOIN stock_info s ON p.stock_code = s.stock_code
                WHERE p.quantity > 0
                ORDER BY p.stock_code ASC
                """
            ).fetchall()
        finally:
            conn.close()

    if not rows:
        raise RuntimeError("PORTFOLIO_POSITIONS_EMPTY")
    return [dict(row) for row in rows]


def load_portfolio_config_values(spreadsheet_id: str) -> List[List[Any]]:
    if not spreadsheet_id:
        raise RuntimeError("PORTFOLIO_CONFIG_SHEET_ID_MISSING")
    range_name = "PORTFOLIO_CONFIG!A1:K100"
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{quote(spreadsheet_id, safe='')}/values/"
        f"{quote(range_name, safe='!:$')}"
    )
    response = _session(DRIVE_SCOPE).get(url, params={"majorDimension": "ROWS"}, timeout=30)
    _raise_for_status(response, "SHEETS_PORTFOLIO_CONFIG_READ")
    return response.json().get("values") or []


def upload_markdown_to_drive(folder_id: str, filename: str, text: str) -> Dict[str, str]:
    """Replace the contents of a pre-created, user-owned Drive Markdown bridge file.

    Service accounts have no consumer Drive storage quota, so creating a new My Drive
    file from Cloud Run fails with storageQuota errors even when the destination folder
    is shared. The safe pattern is: the user owns one Markdown file, shares that file
    with the runtime service account as Writer, and the job only updates its bytes.

    ``folder_id`` and ``filename`` remain in the signature for compatibility with the
    existing runner, but the bridge file ID is authoritative.
    """

    del folder_id, filename
    file_id = (os.environ.get("V8_SPECIAL_RAW_FILE_ID") or "").strip()
    if not file_id:
        raise RuntimeError("V8_SPECIAL_RAW_FILE_ID_MISSING")

    session = _session(DRIVE_SCOPE)
    upload_url = f"https://www.googleapis.com/upload/drive/v3/files/{quote(file_id, safe='')}"
    response = session.patch(
        upload_url,
        params={"uploadType": "media"},
        data=text.encode("utf-8"),
        headers={"Content-Type": "text/markdown; charset=utf-8"},
        timeout=90,
    )
    _raise_for_status(response, "DRIVE_MARKDOWN_UPDATE")

    metadata_url = f"https://www.googleapis.com/drive/v3/files/{quote(file_id, safe='')}"
    metadata_response = session.get(
        metadata_url,
        params={"fields": "id,name,webViewLink"},
        timeout=30,
    )
    _raise_for_status(metadata_response, "DRIVE_MARKDOWN_METADATA")
    data = metadata_response.json()
    return {
        "id": str(data.get("id") or file_id),
        "name": str(data.get("name") or "V8_보유종목_최신원자료.md"),
        "webViewLink": str(data.get("webViewLink") or ""),
    }
