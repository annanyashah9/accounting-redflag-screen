"""
Point-in-time date discipline (Phase 2).

This module attaches "when was this knowable?" dates to the Phase 1 scores WITHOUT
recomputing them. It is deliberately signal-agnostic: `point_in_time_view` operates on any
dataframe carrying an `available_as_of` column, so Phase 3 can stamp transcript-derived
signals (knowable as of the call date) and reuse the exact same as-of filter.

Two date sources (see plan):
  * PRIMARY  -- the companyfacts `filed` date already carried per-fact in Phase 1's long
    table. Because Phase 1 selects the earliest-filed, fy-matched fact per period, this is
    the ORIGINALLY-REPORTED filing date, not a later restatement's.
  * SECONDARY -- the EDGAR submissions API, joined by accession, for the intra-day
    acceptance timestamp, the authoritative form (10-K / 20-F / ...), and amendment history.

The available-as-of date obeys the LATEST-INPUT RULE: a score for fiscal year Y consumes
facts from years {Y, Y-1, Y-2}, so it cannot be computed until the last of those is filed;
available_as_of = max(filing_date) over the consumed years. Since later fiscal years file
later, this reduces to year Y's annual-report filing date (we compute the max explicitly).
"""
from __future__ import annotations

import pandas as pd

from config import CONCEPT_MAP, MIN_FISCAL_YEAR
from extract import (  # reuse Phase 1 tag-mapping / selection helpers
    _annual_records,
    _best_per_year,
    _candidate_specs,
    _parse,
)

# Annual forms whose filing dates can stamp a score (domestic + foreign annual reports).
ANNUAL_FILING_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

# A filing accepted at/after ~21:00 UTC is after the US market close (16:00 ET) year-round
# (ET close is 20:00 UTC in summer, 21:00 in winter; 10-Ks cluster in winter, so 21:00 is the
# conservative boundary). Such data is realistically actionable only the next session.
_AFTER_CLOSE_UTC_HOUR = 21


# ---------------------------------------------------------------------------
# Filing index (from the submissions API)
# ---------------------------------------------------------------------------
def build_filing_index(submissions_json: dict, cik: int | None = None) -> pd.DataFrame:
    """One row per annual filing: accession, form, filing_date, acceptance_datetime,
    period_of_report. Reads `filings.recent` (covers the full XBRL era for this universe)."""
    rec = submissions_json.get("filings", {}).get("recent", {})
    acc = rec.get("accessionNumber", [])
    n = len(acc)
    form = rec.get("form", [])
    filing_date = rec.get("filingDate", [])
    acceptance = rec.get("acceptanceDateTime", [None] * n)
    report_date = rec.get("reportDate", [None] * n)

    rows = []
    for i in range(n):
        if form[i] not in ANNUAL_FILING_FORMS:
            continue
        rows.append({
            "cik": cik,
            "accession": acc[i],
            "form": form[i],
            "filing_date": filing_date[i],
            "acceptance_datetime": acceptance[i] if i < len(acceptance) else None,
            "period_of_report": report_date[i] if i < len(report_date) else None,
        })
    return pd.DataFrame(rows, columns=["cik", "accession", "form", "filing_date",
                                       "acceptance_datetime", "period_of_report"])


def _after_close(acceptance_dt: str | None) -> bool:
    """True if the filing was accepted after the US market close (approx; see constant)."""
    if not isinstance(acceptance_dt, str) or len(acceptance_dt) < 13:
        return False
    try:
        return int(acceptance_dt[11:13]) >= _AFTER_CLOSE_UTC_HOUR
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Available-as-of stamping (the latest-input rule)
# ---------------------------------------------------------------------------
def _year_filing_meta(long_df: pd.DataFrame) -> pd.DataFrame:
    """Per (cik, fiscal_year): the binding filing (max `filed` over that year's inputs) and
    its accession / period-end / form -- all taken from companyfacts, so they are present
    regardless of submissions-API pagination. This is the date year Y's data was knowable."""
    tmp = long_df.dropna(subset=["filed"]).sort_values("filed")
    binding = tmp.groupby(["cik", "fiscal_year"], as_index=False).tail(1)
    return binding[["cik", "fiscal_year", "filed", "accn", "period_end", "form"]].rename(
        columns={"filed": "year_filing_date", "accn": "year_accession",
                 "period_end": "year_period_end", "form": "year_form"})


def stamp_available_as_of(scores_df: pd.DataFrame, long_df: pd.DataFrame,
                          filing_index: pd.DataFrame) -> pd.DataFrame:
    """Attach available_as_of (+ form / accession / acceptance / knowable_next_day) to each
    score, per the latest-input rule over dependency years {Y, Y-1, Y-2}."""
    out = scores_df.copy()
    ym = _year_filing_meta(long_df)

    # Bring year Y, Y-1, Y-2 binding-filing metadata onto each score row.
    per_lag = ["year_filing_date", "year_accession", "year_period_end", "year_form"]
    for lag in (0, 1, 2):
        m = ym.copy()
        m["fiscal_year"] = m["fiscal_year"] + lag  # shift so it joins onto a score in year Y
        m = m.rename(columns={c: f"_{c}{lag}" for c in per_lag})
        out = out.merge(m[["cik", "fiscal_year"] + [f"_{c}{lag}" for c in per_lag]],
                        on=["cik", "fiscal_year"], how="left")

    fd_cols = [f"_year_filing_date{lag}" for lag in (0, 1, 2)]
    # String YYYY-MM-DD compares chronologically; max(skipna) = the last input filed.
    out["available_as_of"] = out[fd_cols].max(axis=1)

    def _binding(row, field):
        # The binding (latest-filed) consumed year; prefer year Y (lag 0) on ties.
        for lag in (0, 1, 2):
            if pd.notna(row[f"_year_filing_date{lag}"]) and \
                    row[f"_year_filing_date{lag}"] == row["available_as_of"]:
                return row[f"_{field}{lag}"]
        return None

    out["available_as_of_accession"] = out.apply(lambda r: _binding(r, "year_accession"), axis=1)
    out["fiscal_period_end"] = out.apply(lambda r: _binding(r, "year_period_end"), axis=1)
    out["available_as_of_form"] = out.apply(lambda r: _binding(r, "year_form"), axis=1)

    # Enrich ONLY with the intra-day acceptance timestamp from submissions (may be absent
    # for some accessions; the core stamp above does not depend on it).
    if not filing_index.empty:
        fi = filing_index.drop_duplicates("accession").rename(
            columns={"accession": "available_as_of_accession"})
        out = out.merge(fi[["available_as_of_accession", "acceptance_datetime"]],
                        on="available_as_of_accession", how="left")
    else:
        out["acceptance_datetime"] = pd.NA
    out["knowable_next_day"] = out["acceptance_datetime"].map(_after_close)

    helper = [f"_{c}{lag}" for lag in (0, 1, 2) for c in per_lag]
    return out.drop(columns=[c for c in helper if c in out.columns])


def point_in_time_view(df: pd.DataFrame, as_of_date: str,
                       date_col: str = "available_as_of") -> pd.DataFrame:
    """Return only rows knowable by `as_of_date` (YYYY-MM-DD). Signal-agnostic: any frame
    with an as-of date column works -- Phase 3 reuses this for transcript signals."""
    as_of = str(as_of_date)
    return df[df[date_col].notna() & (df[date_col] <= as_of)].copy()


# ---------------------------------------------------------------------------
# Restatement detection (free-data: value divergence across filings)
# ---------------------------------------------------------------------------
# A revision larger than this factor is implausible as a GAAP restatement; it is almost
# always an XBRL unit/scale quirk (e.g. shares tagged in millions vs absolute) or an
# early-filing data-quality artifact. We classify those separately, not as "restated".
_SCALE_ARTIFACT_FACTOR = 10.0

# Inputs excluded from restatement value-divergence detection: share counts are
# retroactively adjusted for stock SPLITS (e.g. Apple's 7:1 and 4:1) and change with
# issuance/buybacks -- legitimate, not financial-statement restatements. (Phase 1 still
# uses the originally-reported share count, so its point-in-time stamp is unaffected.)
_RESTATEMENT_SKIP = {"shares"}


def _classify_change(ov, lv, tol: float) -> tuple[float | None, str, bool]:
    """Classify originally-reported (ov) vs latest-reported (lv): returns
    (relative_change, change_type, is_restatement)."""
    if ov is None or lv is None:
        return None, "missing", False
    if ov == 0:
        return None, "from_zero", False  # relative change undefined; don't flag
    rel = abs(lv - ov) / abs(ov)
    if rel <= tol:
        return rel, "none", False
    factor = abs(lv / ov)
    if factor >= _SCALE_ARTIFACT_FACTOR or factor <= 1 / _SCALE_ARTIFACT_FACTOR:
        return rel, "scale_or_unit", False  # data-quality artifact, not a restatement
    return rel, "restated", True


def detect_restatements(facts_json: dict, company: dict, cik: int,
                        tol: float = 0.005) -> pd.DataFrame:
    """For each (logical_input, fiscal_year) Phase 1 uses, compare the originally-reported
    value (earliest-filed, best-tag -- exactly what Phase 1 used) against the most-recently
    reported value for the SAME economic period across all later filings. Flag `restated`
    when the relative change exceeds `tol`."""
    rows = []
    for logical_input, spec in CONCEPT_MAP.items():
        if logical_input in _RESTATEMENT_SKIP:
            continue
        records = []
        for cand in _candidate_specs(spec):
            records.extend(_annual_records(facts_json, cand))
        if not records:
            continue

        # Phase 1's exact per-fiscal-year selection gives the ORIGINALLY-reported value
        # (one per fiscal year). For each, find the latest report of that SAME period
        # (same tag; period-end within a few days to absorb 52/53-week date wobble).
        for fy, original in _best_per_year(records).items():
            if fy is None or fy < MIN_FISCAL_YEAR:
                continue
            end = _parse(original["period_end"])
            same_period = [
                r for r in records
                if r["tag_used"] == original["tag_used"] and r["period_end"]
                and abs((_parse(r["period_end"]) - end).days) <= 4
            ]
            latest = max(same_period, key=lambda r: (r["filed"] or ""))

            ov, lv = original["value"], latest["value"]
            rel_change, change_type, restated = _classify_change(ov, lv, tol)

            rows.append({
                "cik": cik, "ticker": company["ticker"], "name": company["name"],
                "fiscal_year": fy, "logical_input": logical_input,
                "originally_reported": ov, "latest_reported": lv,
                "original_filed": original["filed"], "latest_filed": latest["filed"],
                "n_versions": len({r["value"] for r in same_period}),
                "rel_change": rel_change, "change_type": change_type,
                "restated": restated,
            })

    return pd.DataFrame(rows)


def summarize_restatements(restatements_df: pd.DataFrame) -> pd.DataFrame:
    """Collapse the restatement long table to per (cik, fiscal_year): which inputs for THAT
    year's data were later revised. (Prior-year contamination of t-1/t-2 inputs is real too
    and is discussed in the README, but the per-year view is the interpretable headline.)"""
    if restatements_df.empty:
        return pd.DataFrame(columns=["cik", "fiscal_year", "restated_inputs",
                                     "n_restated_inputs"])
    flagged = restatements_df[restatements_df["restated"]]
    if flagged.empty:
        return pd.DataFrame(columns=["cik", "fiscal_year", "restated_inputs",
                                     "n_restated_inputs"])
    grp = flagged.groupby(["cik", "fiscal_year"])
    out = grp["logical_input"].agg(lambda s: ",".join(sorted(set(s)))).reset_index()
    out = out.rename(columns={"logical_input": "restated_inputs"})
    out["n_restated_inputs"] = grp.size().values
    return out
