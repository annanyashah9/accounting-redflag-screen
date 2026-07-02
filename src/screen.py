"""
Phase 4: combine the accounting scores (P1-2) and tone signals (P3) into one red-flag
screen. Combination + explanation only -- no signal is recomputed here.

The rule is deliberately transparent and specified ON PRINCIPLE: four independent binary
flags, each using its own pre-published / Phase-set threshold, equal-weighted into a count.
No weights are fit and nothing is tuned to the seeded known cases.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import PIOTROSKI_WEAK, SCREEN_MIN_FLAGS

# columns pulled from each prior-phase output
_ACCT_COLS = ["cik", "ticker", "name", "is_known_case", "fiscal_year", "fscore",
              "mscore", "mscore_flag", "fiscal_period_end", "available_as_of"]
_TONE_COLS = ["cik", "fiscal_year", "mdna_found", "hedging", "d_hedging", "fls_freq",
              "d_fls_freq", "hedging_rise", "fls_drop", "available_as_of"]

FLAG_COLS = ["flag_fscore_weak", "flag_mscore_manip", "flag_hedging_rise", "flag_fls_drop"]


def _as_bool(series: pd.Series) -> pd.Series:
    """Coerce a possibly-string/NaN boolean column to real bools (NaN/'' -> False)."""
    return series.map(lambda v: str(v).strip().lower() == "true")


def _reason(row: pd.Series) -> str:
    """Human-readable drill-down: exactly which signals fired, with their values."""
    parts = []
    if row["flag_fscore_weak"]:
        parts.append(f"F-Score={int(row['fscore'])} (weak <= {PIOTROSKI_WEAK})")
    if row["flag_mscore_manip"]:
        parts.append(f"M-Score={row['mscore']:.2f} (> -1.78, manipulation-like)")
    if row["flag_hedging_rise"]:
        prev = row["hedging"] - row["d_hedging"]
        rel = f" ({row['d_hedging'] / prev:+.0%})" if prev and prev > 0 else ""
        parts.append(f"hedging {row['d_hedging']:+.4f} YoY{rel} (rise)")
    if row["flag_fls_drop"]:
        prev = row["fls_freq"] - row["d_fls_freq"]
        rel = f" ({row['d_fls_freq'] / prev:+.0%})" if prev and prev > 0 else ""
        parts.append(f"fwd-looking {row['d_fls_freq']:+.4f} YoY{rel} (drop)")
    return "; ".join(parts)


def build_screen(scores_pit: pd.DataFrame, tone_signals: pd.DataFrame) -> pd.DataFrame:
    """Merge accounting + tone into one screen with flags, red-flag count, combined
    point-in-time date, and a drill-down `reasons` string."""
    acct = scores_pit[[c for c in _ACCT_COLS if c in scores_pit.columns]].copy()
    tone = tone_signals[[c for c in _TONE_COLS if c in tone_signals.columns]].copy()

    df = acct.merge(tone, on=["cik", "fiscal_year"], how="outer",
                    suffixes=("_acct", "_tone"))

    # --- four independent red flags (each on its own pre-set threshold) ---
    df["flag_fscore_weak"] = (df["fscore"] <= PIOTROSKI_WEAK).fillna(False)
    df["flag_mscore_manip"] = _as_bool(df["mscore_flag"])
    df["flag_hedging_rise"] = _as_bool(df["hedging_rise"])
    df["flag_fls_drop"] = _as_bool(df["fls_drop"])

    df["red_flags"] = df[FLAG_COLS].sum(axis=1).astype(int)
    df["screen_flagged"] = df["red_flags"] >= SCREEN_MIN_FLAGS

    # --- combined available_as_of: latest of the constituent signals (no lookahead) ---
    date_cols = [c for c in ("available_as_of_acct", "available_as_of_tone") if c in df]
    df["combined_available_as_of"] = df[date_cols].max(axis=1)

    df["reasons"] = df.apply(_reason, axis=1)

    order = ["cik", "ticker", "name", "is_known_case", "fiscal_year",
             "fscore", "mscore", "mscore_flag", "hedging", "d_hedging",
             "fls_freq", "d_fls_freq", *FLAG_COLS, "red_flags", "screen_flagged",
             "fiscal_period_end", "combined_available_as_of", "reasons"]
    order = [c for c in order if c in df.columns]
    return df[order].sort_values(["ticker", "fiscal_year"]).reset_index(drop=True)
