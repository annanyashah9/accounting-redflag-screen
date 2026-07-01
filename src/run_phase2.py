"""
Phase 2 entry point: stamp Phase 1 scores with point-in-time filing dates.

Run:  python src/run_phase2.py [AS_OF_DATE]   (default as-of 2015-04-01)

Pipeline (reuses Phase 1 unchanged):
  run_phase1.collect_long_facts  -> annual facts (long)            [Phase 1]
  extract.build_fundamentals     -> fundamentals (wide)            [Phase 1]
  scores.score_all               -> F-Score / M-Score table        [Phase 1]
  pit.stamp_available_as_of      -> attach available_as_of dates    [Phase 2]
  pit.detect_restatements        -> flag later-revised figures      [Phase 2]
  pit.point_in_time_view         -> the screen as of a past date    [Phase 2]

Outputs land in results/; a README note is written explaining the lookahead-bias argument
and the restatement limitation. No NLP / evaluation (Phases 3-4).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

from config import UNIVERSE
from edgar import get_companyfacts, get_submissions, load_ticker_cik_map, resolve_cik
from extract import build_fundamentals
from pit import (
    build_filing_index,
    detect_restatements,
    point_in_time_view,
    stamp_available_as_of,
    summarize_restatements,
)
from run_phase1 import collect_long_facts
from scores import score_all

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
README = Path(__file__).resolve().parent.parent / "README.md"
DEFAULT_AS_OF = "2015-04-01"


def _company_by_cik() -> dict[int, dict]:
    ticker_map = load_ticker_cik_map()
    out = {}
    for company in UNIVERSE:
        cik = company.get("cik") or resolve_cik(company["ticker"], ticker_map)
        if cik is not None:
            out[int(cik)] = company
    return out


def gather_pit_metadata(report: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch submissions (filing index) and companyfacts (restatements) for OK companies."""
    comp_by_cik = _company_by_cik()
    filing_frames, rest_frames = [], []

    for r in report:
        if not str(r["status"]).startswith("ok") or r["cik"] is None:
            continue
        cik = int(r["cik"])
        company = comp_by_cik.get(cik)

        subs = get_submissions(cik)
        if subs is not None:
            filing_frames.append(build_filing_index(subs, cik))

        facts = get_companyfacts(cik)
        if facts is not None and company is not None:
            rest_frames.append(detect_restatements(facts, company, cik))

    filing_index = (pd.concat(filing_frames, ignore_index=True) if filing_frames
                    else pd.DataFrame())
    restatements = (pd.concat(rest_frames, ignore_index=True) if rest_frames
                    else pd.DataFrame())
    return filing_index, restatements


def _has_score(df: pd.DataFrame) -> pd.Series:
    return df["fscore"].notna() | df["mscore"].notna()


def _days_gap(available_as_of: pd.Series, fiscal_period_end: pd.Series) -> pd.Series:
    a = pd.to_datetime(available_as_of, errors="coerce")
    p = pd.to_datetime(fiscal_period_end, errors="coerce")
    return (a - p).dt.days


def print_summary(scores_pit: pd.DataFrame, restatements: pd.DataFrame,
                  as_of_date: str) -> None:
    print("\n" + "=" * 78)
    print("PHASE 2 — POINT-IN-TIME DATE DISCIPLINE")
    print("=" * 78)

    computable = scores_pit[_has_score(scores_pit)].copy()
    stamped = computable["available_as_of"].notna().sum()
    print(f"\nComputable score-rows: {len(computable)} | stamped with available_as_of: "
          f"{stamped} ({stamped/max(len(computable),1):.0%})")

    gap = _days_gap(computable["available_as_of"], computable["fiscal_period_end"]).dropna()
    if not gap.empty:
        print(f"\nPeriod-end -> filing gap (days): min {int(gap.min())}, "
              f"median {int(gap.median())}, max {int(gap.max())} "
              f"(NEVER stamped with the fiscal-period-end date)")
        if (gap <= 0).any():
            print(f"  WARNING: {int((gap<=0).sum())} rows with non-positive gap — investigate")

    # --- Point-in-time view vs naive "all data now" ---
    asof_view = point_in_time_view(computable, as_of_date)
    hidden = computable[computable["available_as_of"] > as_of_date]
    print(f"\nLOOKAHEAD DEMO — the screen as of {as_of_date}:")
    print(f"  Naive 'all data now' view : {len(computable)} computable score-rows")
    print(f"  Honest as-of-{as_of_date} view : {len(asof_view)} rows actually knowable then")
    print(f"  Hidden by point-in-time   : {len(hidden)} rows (would be LOOKAHEAD if used)")

    # Concrete example: a fiscal year whose 10-K was filed AFTER the as-of date.
    near = hidden.sort_values("available_as_of").head(8)
    if not near.empty:
        print(f"\n  Examples of scores NOT yet knowable on {as_of_date} "
              f"(filed later -> excluded):")
        cols = ["ticker", "fiscal_year", "fscore", "mscore",
                "fiscal_period_end", "available_as_of"]
        print(near[cols].to_string(index=False))

    htz = scores_pit[(scores_pit.ticker == "HTZ") & (scores_pit.fiscal_year == 2014)]
    if not htz.empty:
        row = htz.iloc[0]
        print(f"\n  Spotlight — Hertz FY2014: period ends {row['fiscal_period_end']} but its "
              f"10-K\n  wasn't filed until {row['available_as_of']} (restatement delay). "
              f"Knowable as of {as_of_date}? "
              f"{'YES' if row['available_as_of'] <= as_of_date else 'NO'}.")

    # --- Restatement contamination ---
    if not restatements.empty:
        flagged = restatements[restatements["restated"]]
        print(f"\nRESTATEMENT DETECTION: {len(flagged)} (input x fiscal-year) figures were "
              f"later revised\n  vs originally reported (companyfacts value divergence). "
              f"Phase 1 uses the ORIGINAL value, so\n  the stamped score is point-in-time "
              f"correct; this flags where the data was contaminated.")
        if not flagged.empty:
            top = (flagged.assign(rc=flagged["rel_change"])
                   .sort_values("rc", ascending=False)
                   .head(6)[["ticker", "fiscal_year", "logical_input",
                             "originally_reported", "latest_reported", "rel_change"]])
            print("\n  Largest originally-reported vs latest divergences:")
            print(top.to_string(index=False))


def write_readme_note(scores_pit: pd.DataFrame, restatements: pd.DataFrame,
                      as_of_date: str) -> None:
    computable = scores_pit[_has_score(scores_pit)]
    asof_n = len(point_in_time_view(computable, as_of_date))
    n_restated = int(restatements["restated"].sum()) if not restatements.empty else 0
    gap = _days_gap(computable["available_as_of"], computable["fiscal_period_end"]).dropna()
    med_gap = int(gap.median()) if not gap.empty else None

    note = f"""# Accounting Red-Flag Screen

A systematic, point-in-time-disciplined screen for accounting red flags across a fixed
universe of companies, built on SEC EDGAR structured (XBRL) data. It is a **defensible
red-flag SCREEN with honest evaluation — not a return or earnings-miss predictor.**

- **Phase 1 — scoring engine.** Piotroski F-Score and Beneish M-Score from EDGAR
  companyfacts, with disciplined XBRL tag-mapping (missing inputs are flagged, never
  substituted). See `src/config.py`, `src/edgar.py`, `src/extract.py`, `src/scores.py`.
- **Phase 2 — point-in-time date discipline** (this note).

## Phase 2: point-in-time discipline and the lookahead problem

### The problem — lookahead bias
A score computed from a fiscal year's financials was **not knowable on the last day of that
fiscal year.** The 10-K that reports those numbers is filed weeks to months later (here, a
median of ~{med_gap} days after period-end). Any screen that stamps a score with the
**fiscal-period-end date** silently claims knowledge nobody had yet — classic lookahead bias,
which inflates apparent performance and is the single most common way a backtest lies.

### The fix — filing-date stamping + the latest-input rule
Every score is stamped with an **`available_as_of`** date: the date its underlying data would
actually have been public.
- **Source.** The companyfacts `filed` date carried per-fact through Phase 1 (the
  originally-reported filing date, since Phase 1 selects the earliest-filed, fiscal-year-
  matched value), enriched via the EDGAR **submissions API** with the intra-day acceptance
  timestamp (`knowable_next_day` flags filings accepted after the US market close), the
  authoritative form type, and amendment history.
- **Latest-input rule.** A score for fiscal year *Y* consumes data from years *Y*, *Y-1*, and
  *Y-2* (the indices need prior years). It cannot be computed until the **last** of those is
  filed, so `available_as_of = max(filing_date)` over the consumed years — which is year *Y*'s
  annual-report filing date.

`pit.point_in_time_view(df, as_of_date)` then returns the screen **as it would have looked on
any past date** — only the scores a real analyst could have computed by then. For example, as
of **{as_of_date}**, only **{asof_n}** of {len(computable)} computable score-rows were
actually knowable; the rest would be lookahead. (Concretely: Hertz's FY2014 10-K was delayed
by its accounting restatement until 2015-07-16, so Hertz FY2014 simply did not exist for a
screen run in spring 2015 — even though the "all data now" view shows it.)

### The limitation that remains — restatement contamination
companyfacts returns each figure **as it exists now.** When a company restates a prior year,
later filings carry revised values for that period. Phase 1 already uses the
**originally-reported** value (earliest-filed), so the stamped score reflects what was known
at the time. Phase 2 additionally **detects and discloses** contamination: for every
(input × fiscal-year) it compares the originally-reported value against the most-recently
reported value for the same period and flags divergences (`results/restatements.csv`;
{n_restated} figures flagged in this run — e.g. Hertz's FY2012 total assets were reported at
$23.29B, later revised to $23.13B).

**What we cannot fix with free data, stated plainly:**
- If companyfacts did **not retain** the original value (only the restated one survives), our
  earliest-filed value is already contaminated and the divergence check cannot see it.
- Value divergence cannot always distinguish a genuine restatement from a reclassification,
  an XBRL tag change, or an entity spin-off recast (e.g. the CIK that held the 2014-era Hertz
  was renamed Herc and later recast its historicals to the equipment-rental business only).
- Share counts are excluded from divergence detection (they change for stock splits/issuance,
  not restatements).
- Acceptance-time → "knowable next day" uses a conservative UTC cutoff, not exact ET/DST.
- This universe is all 10-K filers; the 20-F/40-F date path is implemented but untested here.

We therefore do **not** claim the data is fully point-in-time clean — only that lookahead from
filing lag is removed and that residual restatement contamination is surfaced, not hidden.

## Outputs
- `results/scores.csv` — Phase 1 scores.
- `results/scores_pit.csv` — scores + `available_as_of`, form, accession, acceptance time,
  `knowable_next_day`, `fiscal_period_end`, `n_restated_inputs`, `restated_inputs`.
- `results/restatements.csv` — originally-reported vs latest value per (input × fiscal-year).
- `results/pit_demo_{as_of_date}.csv` — the as-of screen vs the naive "all data now" view.

## Reproducing
`python src/run_phase1.py` then `python src/run_phase2.py [AS_OF_DATE]`. All EDGAR responses
are cached under `data/`, so runs are reproducible against a fixed snapshot.
"""
    README.write_text(note)


def main(as_of_date: str = DEFAULT_AS_OF) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Phase 1 (reused unchanged).
    long_all, report = collect_long_facts()
    scores = score_all(build_fundamentals(long_all))

    # Phase 2 metadata + stamping.
    filing_index, restatements = gather_pit_metadata(report)
    scores_pit = stamp_available_as_of(scores, long_all, filing_index)

    rest_summary = summarize_restatements(restatements)
    scores_pit = scores_pit.merge(rest_summary, on=["cik", "fiscal_year"], how="left")
    scores_pit["n_restated_inputs"] = scores_pit["n_restated_inputs"].fillna(0).astype(int)
    scores_pit["restated_inputs"] = scores_pit["restated_inputs"].fillna("")

    # Persist.
    scores_pit.to_csv(RESULTS_DIR / "scores_pit.csv", index=False)
    if not restatements.empty:
        restatements.to_csv(RESULTS_DIR / "restatements.csv", index=False)

    computable = scores_pit[_has_score(scores_pit)].copy()
    computable["visible_as_of"] = computable["available_as_of"] <= as_of_date
    demo_cols = ["ticker", "name", "is_known_case", "fiscal_year", "fscore", "mscore",
                 "mscore_flag", "fiscal_period_end", "available_as_of",
                 "available_as_of_form", "knowable_next_day", "visible_as_of",
                 "n_restated_inputs", "restated_inputs"]
    computable[demo_cols].sort_values(["available_as_of", "ticker"]).to_csv(
        RESULTS_DIR / f"pit_demo_{as_of_date}.csv", index=False)

    print_summary(scores_pit, restatements, as_of_date)
    write_readme_note(scores_pit, restatements, as_of_date)
    print(f"\nWrote: results/scores_pit.csv, results/restatements.csv, "
          f"results/pit_demo_{as_of_date}.csv, README.md")


if __name__ == "__main__":
    as_of = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_AS_OF
    # Validate the as-of date format early.
    date.fromisoformat(as_of)
    main(as_of)
