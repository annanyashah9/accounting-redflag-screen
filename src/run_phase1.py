"""
Phase 1 entry point: fetch -> extract -> score -> save tables -> print summary.

Run:  python src/run_phase1.py   (uses cached data/ on re-runs; offline & reproducible)

This wires together the modular layers and does no analysis of its own:
  edgar.py    -> companyfacts JSON (cached to data/)
  extract.py  -> annual facts (long) -> fundamentals (wide), with tag-mapping flags
  scores.py   -> Piotroski F-Score + Beneish M-Score
Outputs land in results/. Phase 2/3/4 are intentionally NOT implemented here.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from config import CONCEPT_MAP, SCOPE_NOTES, UNIVERSE
from edgar import get_companyfacts, load_ticker_cik_map, resolve_cik
from extract import build_fundamentals, extract_annual_facts
from scores import score_all

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


def collect_long_facts() -> tuple[pd.DataFrame, list[dict]]:
    """Resolve CIKs, fetch companyfacts, and extract annual facts for the universe.

    Returns the combined LONG fact table and a per-company fetch report.
    """
    ticker_map = load_ticker_cik_map()
    frames: list[pd.DataFrame] = []
    report: list[dict] = []

    for company in UNIVERSE:
        ticker = company["ticker"]
        # Explicit CIK (permanent, collision-proof) wins; else resolve the live ticker.
        cik = company.get("cik") or resolve_cik(ticker, ticker_map)
        if cik is None:
            report.append({"ticker": ticker, "name": company["name"], "cik": None,
                           "entity_name": "", "identity_ok": False,
                           "status": "no CIK (not in SEC ticker list)"})
            continue

        facts = get_companyfacts(cik)
        if facts is None:
            report.append({"ticker": ticker, "name": company["name"], "cik": cik,
                           "entity_name": "", "identity_ok": False,
                           "status": "no companyfacts (404)"})
            continue

        # Identity check: flag when the fetched entity doesn't match what we expect
        # (guards against ticker reuse / wrong-CIK), rather than trusting it silently.
        entity = facts.get("entityName", "") or ""
        expect = company.get("expect_entity")
        identity_ok = (expect is None) or (expect.lower() in entity.lower())

        long_df = extract_annual_facts(facts, company, cik)
        n_years = long_df["fiscal_year"].nunique() if not long_df.empty else 0
        status = f"ok ({n_years} fiscal years)"
        if not identity_ok:
            status = f"IDENTITY MISMATCH: got '{entity}', expected ~'{expect}'"
        report.append({"ticker": ticker, "name": company["name"], "cik": cik,
                       "entity_name": entity, "identity_ok": identity_ok,
                       "status": status})
        if not long_df.empty and identity_ok:
            frames.append(long_df)

    long_all = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return long_all, report


def build_coverage(long_all: pd.DataFrame) -> pd.DataFrame:
    """Auditable tag-mapping table: one row per company-year, one column per logical
    input showing the EXACT tag used (blank = input unavailable -> score input flagged)."""
    if long_all.empty:
        return pd.DataFrame()
    cov = long_all.pivot_table(
        index=["ticker", "name", "cik", "fiscal_year"],
        columns="logical_input", values="tag_used", aggfunc="first",
    ).reset_index()
    cov.columns.name = None
    # Stable column order; ensure every logical input appears even if never found.
    for logical_input in CONCEPT_MAP:
        if logical_input not in cov.columns:
            cov[logical_input] = pd.NA
    ordered = ["ticker", "name", "cik", "fiscal_year"] + list(CONCEPT_MAP)
    return cov[ordered].sort_values(["ticker", "fiscal_year"]).reset_index(drop=True)


def print_summary(report: list[dict], wide: pd.DataFrame, scores: pd.DataFrame) -> None:
    print("\n" + "=" * 78)
    print("PHASE 1 — ACCOUNTING RED-FLAG SCREEN (scoring engine)")
    print("=" * 78)
    print("\nThis is a RED-FLAG SCREEN, not a returns/earnings predictor. Scores are\n"
          "computed under documented design-scope limits:\n")
    print("  * " + SCOPE_NOTES["piotroski"])
    print("  * " + SCOPE_NOTES["beneish"])

    rep = pd.DataFrame(report)
    n_ok = rep["status"].str.startswith("ok").sum() if not rep.empty else 0
    print(f"\nUniverse: {len(report)} companies | with companyfacts data: {n_ok}")
    problem = rep[~rep["status"].str.startswith("ok")]
    if not problem.empty:
        print("\nCompanies with NO usable data (flagged, not silently dropped):")
        for _, r in problem.iterrows():
            print(f"  - {r['ticker']:<6} {r['name']:<28} {r['status']}")

    if scores.empty:
        print("\nNo scores computed.")
        return

    print(f"\nScored panel: {len(scores)} company-year rows, "
          f"{scores['fiscal_year'].min()}-{scores['fiscal_year'].max()}.")
    print(f"Complete F-Scores: {scores['fscore'].notna().sum()} | "
          f"Complete M-Scores: {scores['mscore'].notna().sum()}")

    # Latest fiscal year per company, sorted by F-Score then M-Score.
    latest = (scores.sort_values("fiscal_year")
              .groupby("ticker", as_index=False).tail(1))
    cols = ["ticker", "fiscal_year", "is_known_case", "fscore", "mscore", "mscore_flag"]
    view = latest[cols].sort_values(["fscore", "mscore"], na_position="last")
    with pd.option_context("display.max_rows", None, "display.width", 120):
        print("\nLatest-year scores (low F-Score / high M-Score = more flagged):")
        print(view.to_string(index=False))

    flagged = latest[latest["mscore_flag"] == True]  # noqa: E712
    if not flagged.empty:
        print(f"\nBeneish manipulation-flag (M > -1.78) in latest year "
              f"[HIGH false-positive rate — screen, not verdict]:")
        print("  " + ", ".join(flagged["ticker"].tolist()))


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    long_all, report = collect_long_facts()
    wide = build_fundamentals(long_all)
    scores = score_all(wide)
    coverage = build_coverage(long_all)

    if not long_all.empty:
        long_all.to_csv(RESULTS_DIR / "fundamentals_long.csv", index=False)
    if not coverage.empty:
        coverage.to_csv(RESULTS_DIR / "input_coverage.csv", index=False)
    if not scores.empty:
        scores.to_csv(RESULTS_DIR / "scores.csv", index=False)

    print_summary(report, wide, scores)
    print(f"\nWrote: results/fundamentals_long.csv, results/input_coverage.csv, "
          f"results/scores.csv")


if __name__ == "__main__":
    main()
