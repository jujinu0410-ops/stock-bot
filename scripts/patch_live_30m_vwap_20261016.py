"""Apply an exact-match 30m VWAP callsite fix to the running service image.

The stock-bot main branch does not yet contain the deployed three-lists-v2
service source. This patch is deliberately *not* a full service replacement.
Run it only on a pinned production image, then execute the sparse-bar tests.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/app/services/youtube_signal_service.py")
old = (
    "        df_30m, source, quality = get_strictly_sliced_30m_df(ticker, cutoff)\n"
    "        if df_30m is None or quality != \"VALID\" or len(df_30m) < 26:\n"
    "            return {\"available\": False, \"reason\": quality, \"source\": source}"
)
new = (
    "        df_30m, source, quality = get_strictly_sliced_30m_df(\n"
    "            ticker, cutoff, allow_sparse_completed=True\n"
    "        )\n"
    "        if df_30m is None or quality != \"VALID\" or len(df_30m) < 26:\n"
    "            app.logger.info(\"30M VWAP missing ticker=%s reason=%s source=%s\", ticker, quality, source)\n"
    "            return {\"available\": False, \"reason\": quality, \"source\": source}"
)
src = path.read_text(encoding="utf-8")
if src.count(old) != 1:
    raise SystemExit("FAIL_CLOSED: cannot identify exact live _fetch_30m_vwap_gate source")
path.write_text(src.replace(old, new, 1), encoding="utf-8")
print("PATCHED_VWAP_GATE=" + str(path))
