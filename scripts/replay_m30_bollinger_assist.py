"""Replay E30 Bollinger context from saved Kiwoom 15m OHLCV CSVs, without network access."""

import argparse
from collections import Counter
from pathlib import Path
import sys

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis.bollinger_atr_30m import prepare_completed_30m_data
from src.analysis.m30_bollinger_assist import bollinger_assist_history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", type=Path, help="One CSV per ticker with At, Open, High, Low, Close, Volume")
    parser.add_argument("--split", required=True, type=pd.Timestamp, help="First holdout trading date in KST")
    args = parser.parse_args()
    counts, cases = Counter(), []
    quantiles = (0.10, 0.15, 0.20, 0.25, 0.30)
    for path in sorted(args.data_dir.glob("*.csv")):
        raw = pd.read_csv(path, index_col="At", parse_dates=["At"])
        watermark = raw.index[-1]
        raw.attrs["source_watermark"] = watermark.isoformat()
        cutoff = watermark.floor("30min")
        if cutoff.hour > 15 or (cutoff.hour == 15 and cutoff.minute > 0):
            cutoff = cutoff.normalize() + pd.Timedelta(hours=15)
        frame, quality = prepare_completed_30m_data(raw, "KIWOOM_15M_BAR_START", cutoff, allow_sparse_completed=True)
        if quality != "VALID":
            print("EXCLUDED", path.stem, quality)
            continue
        print("COMPLETED", path.stem, len(frame), frame.index[0], frame.index[-1])
        six_bar_returns = (frame.Close.shift(-6) / frame.Close - 1) * 100
        # Exclude calibration outcomes that extend into the holdout period.
        crosses_split = (frame.index < args.split) & frame.index.to_series().shift(-6).ge(args.split)
        six_bar_returns.loc[crosses_split] = float("nan")
        for quantile in quantiles:
            signals = bollinger_assist_history(frame, quantile=quantile)
            eligible = signals.dropna(subset=["percent_b", "compression_ceiling_pct"])
            for stamp, row in eligible.iterrows():
                sample = "calibration" if stamp < args.split else "holdout"
                counts[quantile, sample, "eligible"] += 1
                counts[quantile, sample, row.stage] += 1
                forward_return = six_bar_returns.at[stamp]
                if row.stage != "TRAILING_WATCH" or pd.isna(forward_return):
                    continue
                outcome = "up" if forward_return >= 1 else "down" if forward_return <= -1 else "flat"
                counts[quantile, sample, outcome] += 1
                cases.append((quantile, sample, outcome, path.stem, stamp, frame.Close.at[stamp], round(forward_return, 2)))
    for quantile in quantiles:
        for sample in ("calibration", "holdout"):
            keys = ("eligible", "COMPRESSION", "BUY_PREPARATION", "TRAILING_WATCH", "up", "down", "flat")
            print(quantile, sample, {key: counts[quantile, sample, key] for key in keys})
    for case in cases:
        print("CASE", *case)


if __name__ == "__main__":
    main()
