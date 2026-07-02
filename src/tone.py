"""
Management-tone signals from MD&A text (Phase 3).

Transparent, lexicon-based core: Loughran-McDonald category frequencies (length-normalized)
plus a forward-looking-statement frequency. The screen inputs are the WITHIN-company
year-over-year DELTAS, not the raw levels (cross-company levels aren't comparable -- filing
styles differ). No network here; text/dictionary are passed in, so this module is unit-tested.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import (
    FLS_CUES,
    FLS_PHRASES,
    TONE_FLS_DROP_PCT,
    TONE_HEDGING_RISE_PCT,
)
from lexicon import tokenize

# LM category column -> output signal name.
_LM_SIGNAL = {
    "Negative": "lm_negative", "Positive": "lm_positive",
    "Uncertainty": "lm_uncertainty", "Litigious": "lm_litigious",
    "Strong_Modal": "lm_strong_modal", "Weak_Modal": "lm_weak_modal",
}
# Signals we compute YoY deltas for (the actual screen inputs).
DELTA_SIGNALS = ["hedging", "fls_freq", "lm_negative", "lm_uncertainty", "net_tone"]

_FLS_CUES_LOWER = {c.lower() for c in FLS_CUES}


def compute_tone(text: str, categories: dict[str, frozenset[str]],
                 fls_cues: set[str] | None = None,
                 fls_phrases: list[str] | None = None) -> dict:
    """Length-normalized LM category proportions + hedging + net tone + FLS frequency."""
    fls_cues = _FLS_CUES_LOWER if fls_cues is None else {c.lower() for c in fls_cues}
    fls_phrases = FLS_PHRASES if fls_phrases is None else fls_phrases

    tokens = tokenize(text)
    n = len(tokens)
    out = {"doc_word_count": n}
    if n == 0:
        return {**out, **{s: np.nan for s in _LM_SIGNAL.values()},
                "hedging": np.nan, "net_tone": np.nan, "fls_freq": np.nan}

    counts = {cat: sum(1 for t in tokens if t in words)
              for cat, words in categories.items()}
    for cat, signal in _LM_SIGNAL.items():
        out[signal] = counts.get(cat, 0) / n

    out["hedging"] = (counts.get("Uncertainty", 0) + counts.get("Weak_Modal", 0)) / n
    out["net_tone"] = (counts.get("Positive", 0) - counts.get("Negative", 0)) / n

    lowered = text.lower()
    fls_hits = sum(1 for t in tokens if t.lower() in fls_cues)
    fls_hits += sum(lowered.count(p) for p in fls_phrases)
    out["fls_freq"] = fls_hits / n
    return out


def build_tone_panel(records: list[dict],
                     categories: dict[str, frozenset[str]]) -> pd.DataFrame:
    """records: dicts with keys cik/ticker/name/is_known_case/fiscal_year and `mdna` (text
    or None). Returns one row per record with tone signals; missing MD&A -> NaN + flagged."""
    rows = []
    for rec in records:
        base = {k: rec[k] for k in
                ("cik", "ticker", "name", "is_known_case", "fiscal_year")}
        mdna = rec.get("mdna")
        if not mdna:
            rows.append({**base, "mdna_found": False, "doc_word_count": 0})
            continue
        rows.append({**base, "mdna_found": True, **compute_tone(mdna, categories)})
    return pd.DataFrame(rows)


def add_yoy_deltas(panel: pd.DataFrame) -> pd.DataFrame:
    """Add within-company YoY deltas for the screen signals and the tone-shift flags.

    Deltas require CONSECUTIVE fiscal years (like the accounting scores); a gap yields NaN
    rather than silently differencing non-adjacent years."""
    df = panel.sort_values(["cik", "fiscal_year"]).reset_index(drop=True)
    g = df.groupby("cik")
    consecutive = (df["fiscal_year"] - g["fiscal_year"].shift(1)) == 1

    prev = {}
    for sig in DELTA_SIGNALS:
        if sig not in df.columns:
            continue
        p = g[sig].shift(1).where(consecutive)
        df[f"d_{sig}"] = (df[sig] - p).where(consecutive)
        prev[sig] = p

    # Tone-shift flags: illustrative relative-change thresholds (NOT predictive-tuned).
    hedge_rel = df.get("d_hedging") / prev.get("hedging").where(prev.get("hedging") > 0) \
        if "hedging" in prev else pd.Series(np.nan, index=df.index)
    fls_rel = df.get("d_fls_freq") / prev.get("fls_freq").where(prev.get("fls_freq") > 0) \
        if "fls_freq" in prev else pd.Series(np.nan, index=df.index)

    df["hedging_rise"] = (hedge_rel > TONE_HEDGING_RISE_PCT).fillna(False)
    df["fls_drop"] = (fls_rel < -TONE_FLS_DROP_PCT).fillna(False)
    df["tone_shift"] = df["hedging_rise"] | df["fls_drop"]
    return df
