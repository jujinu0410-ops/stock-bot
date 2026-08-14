import sys
import os
import inspect
from pathlib import Path

BASE_DIR = Path("C:/Users/jooji/.gemini/antigravity/scratch/stock_analysis_system")
sys.path.insert(0, str(BASE_DIR))

def search_files_for_terms(directory, terms):
    results = {term: [] for term in terms}
    for p in Path(directory).rglob("*.py"):
        try:
            content = p.read_text(encoding="utf-8")
            for idx, line in enumerate(content.splitlines(), start=1):
                for term in terms:
                    if term.lower() in line.lower():
                        results[term].append(f"{p.relative_to(BASE_DIR)}:{idx} -> {line.strip()}")
        except Exception:
            pass
    return results

terms = [
    "atr", "true_range", "adjust_krx_tick_size", "highest_close",
    "confirmed_stop", "trailing", "target_profit", "stop_loss", "2.5", "3.0", "1.5", "2.0"
]

res = search_files_for_terms(BASE_DIR, terms)
for term, lines in res.items():
    print(f"\n==================== TERM: {term} (Total {len(lines)}) ====================")
    for l in lines[:15]:
        print("  ", l)
