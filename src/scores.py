"""
Score engine: Piotroski F-Score (9-point) and Beneish M-Score (8-variable).

Design notes
------------
* Vectorized over the whole panel. Prior-year (t-1) and t-2 values are obtained by an
  EXPLICIT self-merge on fiscal_year (not a positional .shift), so a missing year never
  silently aligns two non-adjacent years.
* Missing inputs propagate to NaN. A signal/variable whose inputs are missing is NaN
  (not 0); a composite score is only reported when ALL of its parts are computable.
  `*_available` columns count how many parts were computable, so partial coverage is
  visible rather than hidden.
* Scoring functions take and return plain dataframes and IGNORE the filing-date
  metadata columns -- Phase 2 can re-stamp those without changing these signatures.

Design-scope limits (see config.SCOPE_NOTES, surfaced in output):
  Piotroski -> high book-to-market value/distressed firms; binary, magnitude-blind.
  Beneish   -> 1982-1992 manufacturers; excludes financials; high false-positive rate.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import BENEISH_COEF, BENEISH_FLAG_THRESHOLD, BENEISH_INPUTS, PIOTROSKI_INPUTS

# Logical inputs the scorers expect as columns (guaranteed present via _ensure_columns).
# cogs/gross_profit aren't in the *_INPUTS reporting lists (tracked via the derived
# "gross_margin" token) but ARE needed as columns for the gross-margin computation.
_ALL_INPUTS = sorted(set(PIOTROSKI_INPUTS) | set(BENEISH_INPUTS) | {"cogs", "gross_profit"})

PIOTROSKI_SIGNALS = [
    "f_roa", "f_cfo", "f_droa", "f_accrual",
    "f_dleverage", "f_dliquidity", "f_neqissue",
    "f_dmargin", "f_dturnover",
]
BENEISH_VARS = ["DSRI", "GMI", "AQI", "SGI", "DEPI", "SGAI", "TATA", "LVGI"]

_KEYS = ["cik", "ticker", "name", "is_known_case", "fiscal_year"]


def _ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in _ALL_INPUTS:
        if col not in df.columns:
            df[col] = np.nan
    return df


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    """Element-wise divide; 0/NaN denominators -> NaN (never inf)."""
    den = den.where((den != 0) & den.notna())
    return (num / den).replace([np.inf, -np.inf], np.nan)


def _with_lags(df: pd.DataFrame) -> pd.DataFrame:
    """Attach t-1 (`_p1`) and t-2-assets (`_p2`) columns via explicit fiscal-year merge."""
    df = _ensure_columns(df).sort_values(["cik", "fiscal_year"])

    lag_cols = ["net_income", "total_assets", "long_term_debt", "current_assets",
                "current_liabilities", "shares", "revenue", "cogs", "gross_profit",
                "receivables", "ppe_net", "depreciation", "sga", "income_continuing",
                "cfo"]

    prev = df[["cik", "fiscal_year"] + lag_cols].copy()
    prev["fiscal_year"] = prev["fiscal_year"] + 1
    prev = prev.rename(columns={c: f"{c}_p1" for c in lag_cols})

    prev2 = df[["cik", "fiscal_year", "total_assets"]].copy()
    prev2["fiscal_year"] = prev2["fiscal_year"] + 2
    prev2 = prev2.rename(columns={"total_assets": "total_assets_p2"})

    out = df.merge(prev, on=["cik", "fiscal_year"], how="left")
    out = out.merge(prev2, on=["cik", "fiscal_year"], how="left")
    return out


def _binary(condition: pd.Series, *inputs: pd.Series) -> pd.Series:
    """1.0/0.0 where all inputs are present; NaN where any input is missing."""
    valid = pd.concat([s.notna() for s in inputs], axis=1).all(axis=1)
    return condition.astype(float).where(valid)


def _gross_margin(rev: pd.Series, cogs: pd.Series, gp: pd.Series) -> pd.Series:
    """Gross margin = (Rev - COGS)/Rev, falling back to GrossProfit/Rev where COGS is
    absent. NaN when revenue is missing/zero or neither cost line is reported."""
    gross_profit = (rev - cogs).where(cogs.notna(), gp)
    return _safe_div(gross_profit, rev)


def piotroski_fscore(wide: pd.DataFrame) -> pd.DataFrame:
    """Compute the 9 Piotroski signals + total. ROA & turnover use beginning-of-year
    (t-1) assets, per Piotroski (2000)."""
    d = _with_lags(wide)

    roa_t = _safe_div(d["net_income"], d["total_assets_p1"])
    roa_p1 = _safe_div(d["net_income_p1"], d["total_assets_p2"])
    cfo_to_assets = _safe_div(d["cfo"], d["total_assets_p1"])

    avg_assets_t = (d["total_assets"] + d["total_assets_p1"]) / 2
    avg_assets_p1 = (d["total_assets_p1"] + d["total_assets_p2"]) / 2
    lev_t = _safe_div(d["long_term_debt"], avg_assets_t)
    lev_p1 = _safe_div(d["long_term_debt_p1"], avg_assets_p1)

    curr_t = _safe_div(d["current_assets"], d["current_liabilities"])
    curr_p1 = _safe_div(d["current_assets_p1"], d["current_liabilities_p1"])

    gm_t = _gross_margin(d["revenue"], d["cogs"], d["gross_profit"])
    gm_p1 = _gross_margin(d["revenue_p1"], d["cogs_p1"], d["gross_profit_p1"])

    turn_t = _safe_div(d["revenue"], d["total_assets_p1"])
    turn_p1 = _safe_div(d["revenue_p1"], d["total_assets_p2"])

    sig = pd.DataFrame(index=d.index)
    sig["f_roa"] = _binary(roa_t > 0, roa_t)
    sig["f_cfo"] = _binary(d["cfo"] > 0, d["cfo"])
    sig["f_droa"] = _binary(roa_t > roa_p1, roa_t, roa_p1)
    sig["f_accrual"] = _binary(cfo_to_assets > roa_t, cfo_to_assets, roa_t)
    sig["f_dleverage"] = _binary(lev_t < lev_p1, lev_t, lev_p1)
    sig["f_dliquidity"] = _binary(curr_t > curr_p1, curr_t, curr_p1)
    sig["f_neqissue"] = _binary(d["shares"] <= d["shares_p1"], d["shares"], d["shares_p1"])
    sig["f_dmargin"] = _binary(gm_t > gm_p1, gm_t, gm_p1)
    sig["f_dturnover"] = _binary(turn_t > turn_p1, turn_t, turn_p1)

    available = sig.notna().sum(axis=1)
    complete = available == len(PIOTROSKI_SIGNALS)
    fscore = sig.sum(axis=1).where(complete)  # only report a full 0-9 score

    out = d[_KEYS].copy()
    out = pd.concat([out, sig], axis=1)
    out["fscore"] = fscore
    out["fscore_available"] = available
    out["fscore_missing_inputs"] = _missing_inputs(
        d, PIOTROSKI_INPUTS, {"gross_margin": _gm_available(d)})
    return out


def beneish_mscore(wide: pd.DataFrame) -> pd.DataFrame:
    """Compute the 8 Beneish variables + M-Score + manipulation flag."""
    d = _with_lags(wide)

    recv_ratio_t = _safe_div(d["receivables"], d["revenue"])
    recv_ratio_p1 = _safe_div(d["receivables_p1"], d["revenue_p1"])
    DSRI = _safe_div(recv_ratio_t, recv_ratio_p1)

    gm_t = _gross_margin(d["revenue"], d["cogs"], d["gross_profit"])
    gm_p1 = _gross_margin(d["revenue_p1"], d["cogs_p1"], d["gross_profit_p1"])
    GMI = _safe_div(gm_p1, gm_t)

    nonquality_t = 1 - _safe_div(d["current_assets"] + d["ppe_net"], d["total_assets"])
    nonquality_p1 = 1 - _safe_div(d["current_assets_p1"] + d["ppe_net_p1"], d["total_assets_p1"])
    AQI = _safe_div(nonquality_t, nonquality_p1)

    SGI = _safe_div(d["revenue"], d["revenue_p1"])

    dep_rate_t = _safe_div(d["depreciation"], d["depreciation"] + d["ppe_net"])
    dep_rate_p1 = _safe_div(d["depreciation_p1"], d["depreciation_p1"] + d["ppe_net_p1"])
    DEPI = _safe_div(dep_rate_p1, dep_rate_t)

    sga_ratio_t = _safe_div(d["sga"], d["revenue"])
    sga_ratio_p1 = _safe_div(d["sga_p1"], d["revenue_p1"])
    SGAI = _safe_div(sga_ratio_t, sga_ratio_p1)

    TATA = _safe_div(d["income_continuing"] - d["cfo"], d["total_assets"])

    lev_t = _safe_div(d["current_liabilities"] + d["long_term_debt"], d["total_assets"])
    lev_p1 = _safe_div(d["current_liabilities_p1"] + d["long_term_debt_p1"], d["total_assets_p1"])
    LVGI = _safe_div(lev_t, lev_p1)

    var = pd.DataFrame(
        {"DSRI": DSRI, "GMI": GMI, "AQI": AQI, "SGI": SGI,
         "DEPI": DEPI, "SGAI": SGAI, "TATA": TATA, "LVGI": LVGI},
        index=d.index,
    )

    c = BENEISH_COEF
    m = (c["intercept"]
         + c["DSRI"] * var["DSRI"] + c["GMI"] * var["GMI"] + c["AQI"] * var["AQI"]
         + c["SGI"] * var["SGI"] + c["DEPI"] * var["DEPI"] + c["SGAI"] * var["SGAI"]
         + c["TATA"] * var["TATA"] + c["LVGI"] * var["LVGI"])

    available = var.notna().sum(axis=1)
    complete = available == len(BENEISH_VARS)
    mscore = m.where(complete)  # require all 8 variables

    out = d[_KEYS].copy()
    out = pd.concat([out, var], axis=1)
    out["mscore"] = mscore
    out["mscore_flag"] = (mscore > BENEISH_FLAG_THRESHOLD).where(mscore.notna())
    out["mscore_available"] = available
    out["mscore_missing_inputs"] = _missing_inputs(
        d, BENEISH_INPUTS, {"gross_margin": _gm_available(d)})
    return out


def _gm_available(d: pd.DataFrame) -> pd.Series:
    """Gross margin is computable when revenue plus either COGS or GrossProfit exist."""
    return d["revenue"].notna() & (d["cogs"].notna() | d["gross_profit"].notna())


def _missing_inputs(d: pd.DataFrame, inputs: list[str],
                    extra: dict[str, pd.Series] | None = None) -> pd.Series:
    """Comma-joined list of THIS-year inputs that are missing (empty if none).

    `extra` maps a synthetic token (e.g. "gross_margin") to an availability mask, for
    derived inputs that don't correspond to a single column.
    """
    present = {c: d[c].notna() for c in inputs}
    present.update(extra or {})
    cols = inputs + list(extra or {})
    return pd.Series(
        [",".join(c for c in cols if not present[c].iloc[i]) for i in range(len(d))],
        index=d.index,
    )


def score_all(wide: pd.DataFrame) -> pd.DataFrame:
    """Run both scores and return one tidy company x fiscal-year table."""
    if wide.empty:
        return pd.DataFrame()
    f = piotroski_fscore(wide)
    m = beneish_mscore(wide).drop(columns=_KEYS)
    scores = pd.concat([f, m], axis=1)

    # Carry the Phase-2 point-in-time anchor through if present.
    meta_cols = [c for c in ("filing_date", "source_form") if c in wide.columns]
    if meta_cols:
        scores = scores.merge(wide[_KEYS + meta_cols], on=_KEYS, how="left")
    return scores.sort_values(["ticker", "fiscal_year"]).reset_index(drop=True)
