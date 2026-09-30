# -*- coding: utf-8 -*-
"""Bootstrap registered-IP YouTube candidate dry run with local Kiwoom credentials.

This module exists only to bridge the existing local `.env` loading in
``config.settings`` into the process environment expected by the read-only Kiwoom
OAuth helper.  It never prints secrets and does not add any write capability.
"""
from __future__ import annotations

import os
import runpy

from config.settings import KIWOOM_APP_KEY, KIWOOM_APP_SECRET


def _usable(value: str | None, placeholder: str) -> str:
    text = str(value or "").strip().strip('"').strip("'")
    if not text or text == placeholder:
        return ""
    return text


def main() -> None:
    app_key = _usable(KIWOOM_APP_KEY, "YOUR_KIWOOM_APP_KEY_HERE")
    app_secret = _usable(KIWOOM_APP_SECRET, "YOUR_KIWOOM_APP_SECRET_HERE")

    if app_key and not os.getenv("KIWOOM_APP_KEY"):
        os.environ["KIWOOM_APP_KEY"] = app_key
    if app_secret and not os.getenv("KIWOOM_APP_SECRET"):
        os.environ["KIWOOM_APP_SECRET"] = app_secret

    # Execute the existing validated read-only runner with the original CLI args.
    runpy.run_module("scripts.youtube_candidate_registered_ip_validated_dry_run", run_name="__main__")


if __name__ == "__main__":
    main()
