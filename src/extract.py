"""
XBRL tag-mapping & annual-fact extraction -- where the landmine lives.

companyfacts gives us, per concept tag, a flat list of facts spanning every filing the
company ever made (annual, quarterly, originals, amendments, prior-year comparatives).
This module turns that into a tidy annual table by:

  1. For each LOGICAL input, trying its candidate tags (config.CONCEPT_MAP) in priority
     order and recording WHICH tag actually supplied each year's value.
  2. Keeping only ANNUAL 10-K facts (form 10-K / 10-K/A; full-year duration for flow
     items; the fiscal-year-end snapshot for stock items).
  3. Picking, per fiscal year, the highest-priority tag and -- among duplicates for the
     same economic period -- the ORIGINALLY-REPORTED value (earliest `filed`). This is
     deliberate: it keeps point-in-time integrity and avoids silently using restated
     numbers (companyfacts retains every filing's value; the latest one reflects
     restatements). Phase 2 will lean on the `filed`/`accn` metadata carried through here.
  4. Leaving an input MISSING (NaN) and flagged when no candidate tag has data -- never
     substituting a wrong tag.

Output of `extract_annual_facts` is a LONG dataframe carrying full filing metadata
(`filed`, `accn`, `form`, `period_end`) so Phase 2 can attach point-in-time stamps
without touching the scoring code.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from config import CONCEPT_MAP, MIN_FISCAL_YEAR

ANNUAL_FORMS = {"10-K", "10-K/A"}
# A "full fiscal year" duration, with slack for 52/53-week fiscal calendars.
_MIN_DURATION_DAYS = 300
_MAX_DURATION_DAYS = 400

LONG_COLUMNS = [
    "cik", "ticker", "name", "is_known_case", "logical_input", "tag_used",
    "taxonomy", "unit", "fiscal_year", "period_start", "period_end",
    "value", "filed", "accn", "form",
]


def _parse(d: str | None) -> date | None:
    if not d:
        return None
    return date.fromisoformat(d)


def _candidate_specs(spec: dict) -> list[dict]:
    """Flatten a CONCEPT_MAP entry (plus any `fallbacks`) into ordered candidates.

    Each candidate = {taxonomy, tag, kind, priority}. Lower priority == tried first.
    """
    out: list[dict] = []
    prio = 0
    for tag in spec["tags"]:
        out.append({"taxonomy": spec["taxonomy"], "tag": tag,
                    "kind": spec["kind"], "priority": prio})
        prio += 1
    for fb in spec.get("fallbacks", []):
        for tag in fb["tags"]:
            out.append({"taxonomy": fb["taxonomy"], "tag": tag,
                        "kind": fb["kind"], "priority": prio})
            prio += 1
    return out


def _pick_unit(units: dict) -> str | None:
    """Choose the relevant unit list for a concept (USD for money, shares otherwise)."""
    for preferred in ("USD", "shares"):
        if preferred in units:
            return preferred
    return next(iter(units), None)


def _annual_records(facts_json: dict, cand: dict) -> list[dict]:
    """Pull annual 10-K facts for one candidate (taxonomy, tag, kind).

    Fiscal year is taken from the SEC-reported `fy` field (with `fp == "FY"`), NOT from
    the calendar year of the period-end date. This matters for 52/53-week filers whose
    fiscal year ends in early January (e.g. J&J's FY2009 ended 2010-01-03): keying on
    end.year would mislabel it and spawn phantom one-input "years" from cover-page facts.
    """
    try:
        concept = facts_json["facts"][cand["taxonomy"]][cand["tag"]]
    except KeyError:
        return []

    unit = _pick_unit(concept.get("units", {}))
    if unit is None:
        return []

    records: list[dict] = []
    for f in concept["units"][unit]:
        if f.get("form") not in ANNUAL_FORMS or f.get("fp") != "FY":
            continue
        fy = f.get("fy")
        end = _parse(f.get("end"))
        if fy is None or end is None:
            continue

        if cand["kind"] == "duration":
            start = _parse(f.get("start"))
            if start is None:
                continue
            days = (end - start).days
            if not (_MIN_DURATION_DAYS <= days <= _MAX_DURATION_DAYS):
                continue

        records.append({
            "tag_used": cand["tag"], "taxonomy": cand["taxonomy"], "unit": unit,
            "priority": cand["priority"], "fiscal_year": int(fy), "end": end,
            "period_start": f.get("start"), "period_end": f.get("end"),
            "value": f.get("val"), "filed": f.get("filed"),
            "accn": f.get("accn"), "form": f.get("form"),
        })
    return records


def _best_per_year(records: list[dict]) -> dict[int, dict]:
    """Pick one record per fiscal year.

    Within a fiscal-year group (a given filing's facts), prefer:
      1. the highest-priority tag (handles the ASC 606 revenue-tag transition);
      2. then the latest period-end -- the filing's CURRENT period, not a prior-year
         comparative carried in the same 10-K;
      3. then the earliest `filed` -- the originally-reported figure (10-K over 10-K/A),
         preserving point-in-time integrity for Phase 2.
    """
    by_year: dict[int, dict] = {}
    for r in records:
        yr = r["fiscal_year"]
        cur = by_year.get(yr)
        if cur is None or _is_better(r, cur):
            by_year[yr] = r
    return by_year


def _is_better(r: dict, cur: dict) -> bool:
    """True if record `r` should replace `cur` for its fiscal year.

    Lower priority (better tag) wins; then LATER period-end (current vs comparative);
    then EARLIER `filed` (originally-reported vs amendment).
    """
    if r["priority"] != cur["priority"]:
        return r["priority"] < cur["priority"]
    if r["end"] != cur["end"]:
        return r["end"] > cur["end"]
    return (r["filed"] or "9999") < (cur["filed"] or "9999")


def extract_annual_facts(facts_json: dict, company: dict, cik: int) -> pd.DataFrame:
    """Build the LONG annual-fact table for one company.

    `company` is a UNIVERSE entry (ticker/name/is_known_case). Returns one row per
    (logical_input, fiscal_year) that has data, with full filing metadata carried.
    """
    rows: list[dict] = []
    for logical_input, spec in CONCEPT_MAP.items():
        all_records: list[dict] = []
        for cand in _candidate_specs(spec):
            all_records.extend(_annual_records(facts_json, cand))

        for yr, rec in _best_per_year(all_records).items():
            if yr < MIN_FISCAL_YEAR:
                continue
            rows.append({
                "cik": cik, "ticker": company["ticker"], "name": company["name"],
                "is_known_case": company["is_known_case"],
                "logical_input": logical_input, **rec,
            })

    if not rows:
        return pd.DataFrame(columns=LONG_COLUMNS)
    df = pd.DataFrame(rows)
    df = df.drop(columns=["priority"])
    return df[LONG_COLUMNS]


def build_fundamentals(long_df: pd.DataFrame) -> pd.DataFrame:
    """Pivot the long table to one row per (company, fiscal year) with input columns.

    Also carries a representative `filing_date` / `source_form` per (cik, fiscal_year)
    -- the latest filing date among that year's inputs -- so Phase 2 has a point-in-time
    anchor without re-fetching. (Full per-concept metadata stays in `long_df`.)
    """
    if long_df.empty:
        return pd.DataFrame()

    wide = long_df.pivot_table(
        index=["cik", "ticker", "name", "is_known_case", "fiscal_year"],
        columns="logical_input", values="value", aggfunc="first",
    ).reset_index()
    wide.columns.name = None

    # Representative filing metadata per (cik, fiscal_year): the latest `filed` across
    # the year's inputs (the date by which the full annual report was available).
    meta = (
        long_df.sort_values("filed")
        .groupby(["cik", "fiscal_year"], as_index=False)
        .agg(filing_date=("filed", "last"), source_form=("form", "last"))
    )
    wide = wide.merge(meta, on=["cik", "fiscal_year"], how="left")

    return wide.sort_values(["ticker", "fiscal_year"]).reset_index(drop=True)
