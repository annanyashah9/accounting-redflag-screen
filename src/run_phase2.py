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

Outputs land in results/. The lookahead-bias and restatement story lives in the hand-written
README, not here. No NLP / evaluation (Phases 3-4).
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
    print(f"\nWrote: results/scores_pit.csv, results/restatements.csv, "
          f"results/pit_demo_{as_of_date}.csv")


if __name__ == "__main__":
    as_of = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_AS_OF
    # Validate the as-of date format early.
    date.fromisoformat(as_of)
    main(as_of)
