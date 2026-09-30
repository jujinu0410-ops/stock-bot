# -*- coding: utf-8 -*-
"""Fast validation wrapper for the first YouTube Candidate Watch integrated dry run.

The full-market public listing is intentionally bypassed here. The first real-data
validation should expose registry misses as UNRESOLVED instead of stalling on an
auxiliary stock-master download. All other safety guarantees and flow fallbacks are
provided by youtube_candidate_integrated_dry_run.py.
"""
from __future__ import annotations

import scripts.youtube_candidate_integrated_dry_run as runner


def _no_public_registry():
    return []


runner.fetch_public_registry = _no_public_registry

if __name__ == "__main__":
    raise SystemExit(runner.main())
