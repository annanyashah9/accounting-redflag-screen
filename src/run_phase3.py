"""
Phase 3 entry point: management-tone NLP signal, point-in-time stamped.

Run:  python src/run_phase3.py [--since YEAR] [--llm]

Pipeline (reuses Phases 1-2):
  run_phase1.collect_long_facts  -> annual facts (long)                 [Phase 1]
  pit._year_filing_meta          -> per (cik, fy) binding 10-K accession [Phase 2]
  filings_text.get_primary_document/extract_mdna -> Item 7 MD&A text     [Phase 3]
  lexicon + tone                 -> LM tone signals + YoY deltas         [Phase 3]
  pit.stamp_available_as_of      -> same available_as_of as the scores   [Phase 2]

The tone signal for fiscal year Y comes from the SAME 10-K (accession) as year Y's
accounting data, so it shares an identical available_as_of -- no new lookahead. Outputs land
in results/. No combined screen / evaluation (that is Phase 4).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from config import UNIVERSE
from edgar import get_submissions, load_ticker_cik_map, resolve_cik
from filings_text import (
    extract_mdna,
    get_primary_document,
    html_to_text,
    primary_doc_map,
)
from lexicon import load_lm_categories
from pit import _year_filing_meta, build_filing_index, point_in_time_view, stamp_available_as_of
from run_phase1 import collect_long_facts
from tone import add_yoy_deltas, build_tone_panel

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A"}


def _company_by_cik() -> dict[int, dict]:
    ticker_map = load_ticker_cik_map()
    out = {}
    for company in UNIVERSE:
        cik = company.get("cik") or resolve_cik(company["ticker"], ticker_map)
        if cik is not None:
            out[int(cik)] = company
    return out


def gather_mdna_records(year_meta: pd.DataFrame, doc_map: dict[str, str],
                        comp_by_cik: dict[int, dict]) -> list[dict]:
    """For each (cik, fiscal_year) binding 10-K, fetch the primary doc and extract MD&A."""
    records = []
    total = len(year_meta)
    for n, row in enumerate(year_meta.itertuples(), 1):
        cik = int(row.cik)
        company = comp_by_cik.get(cik)
        if company is None or row.year_form not in ANNUAL_FORMS:
            continue
        doc = doc_map.get(row.year_accession)
        mdna = None
        if doc:
            html = get_primary_document(cik, row.year_accession, doc)
            if html:
                mdna = extract_mdna(html_to_text(html))
        if n % 50 == 0:
            print(f"  ... {n}/{total} filings processed")
        records.append({
            "cik": cik, "ticker": company["ticker"], "name": company["name"],
            "is_known_case": company["is_known_case"],
            "fiscal_year": int(row.fiscal_year), "mdna": mdna,
        })
    return records


def _finance_lexicon_proof(categories) -> str:
    neg = categories["Negative"]
    proof = []
    for w in ("LIABILITY", "COST", "CAPITAL", "TAX"):
        proof.append(f"{w.lower()}={'NEG' if w in neg else 'neutral'}")
    for w in ("LOSS", "LITIGATION"):
        proof.append(f"{w.lower()}={'NEG' if w in neg else 'neutral'}")
    return ", ".join(proof)


def print_summary(tone: pd.DataFrame, categories) -> pd.DataFrame:
    print("\n" + "=" * 78)
    print("PHASE 3 — MANAGEMENT-TONE NLP SIGNAL (point-in-time)")
    print("=" * 78)

    n = len(tone)
    found = int(tone["mdna_found"].sum())
    print(f"\nCompany-years: {n} | MD&A extracted: {found} ({found/max(n,1):.0%}) "
          f"| missing flagged (mdna_found=False): {n - found}")

    print("\nFinance-lexicon check (LM keeps neutral finance words OUT of 'negative'):")
    print("  " + _finance_lexicon_proof(categories))

    # PIT consistency vs the accounting scores (shared accession -> identical date).
    spath = RESULTS_DIR / "scores_pit.csv"
    if spath.exists():
        sp = pd.read_csv(spath)[["cik", "fiscal_year", "available_as_of"]]
        merged = tone.merge(sp, on=["cik", "fiscal_year"], how="inner",
                            suffixes=("", "_acct"))
        agree = (merged["available_as_of"] == merged["available_as_of_acct"]).mean()
        print(f"\nPIT consistency: tone available_as_of == accounting available_as_of for "
              f"{agree:.0%} of shared company-years (shared 10-K accession).")

    # Drill-down: notable YoY tone shifts, known cases first.
    shifts = tone[tone["tone_shift"]].copy()
    shifts["_kc"] = ~shifts["is_known_case"]
    examples = shifts.sort_values(["_kc", "d_hedging"], ascending=[True, False]).head(8)
    cols = ["ticker", "fiscal_year", "is_known_case", "hedging", "d_hedging",
            "fls_freq", "d_fls_freq", "available_as_of"]
    if not examples.empty:
        print("\nNotable YoY tone shifts (rising hedging or falling forward-looking freq):")
        print(examples[cols].to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    return examples[cols] if not examples.empty else pd.DataFrame(columns=cols)


def main(since: int | None = None, use_llm: bool = False) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    long_all, _report = collect_long_facts()
    year_meta = _year_filing_meta(long_all)
    if since is not None:
        year_meta = year_meta[year_meta["fiscal_year"] >= since]

    comp_by_cik = _company_by_cik()
    filing_frames, doc_map = [], {}
    for cik in sorted(year_meta["cik"].unique()):
        subs = get_submissions(int(cik))
        if subs is None:
            continue
        filing_frames.append(build_filing_index(subs, int(cik)))
        doc_map.update(primary_doc_map(subs))
    filing_index = pd.concat(filing_frames, ignore_index=True) if filing_frames else pd.DataFrame()

    categories = load_lm_categories()
    print(f"Fetching MD&A for {len(year_meta)} company-years (cached after first run)...")
    records = gather_mdna_records(year_meta, doc_map, comp_by_cik)

    panel = add_yoy_deltas(build_tone_panel(records, categories))
    tone = stamp_available_as_of(panel, long_all, filing_index)

    if use_llm:
        _run_llm_pass(tone, records)  # supplementary; see tone_llm

    # A --since run is a partial panel; write to suffixed files so it can never clobber the
    # canonical full-panel tone_signals.csv that Phase 4 consumes. (Note: the subset's
    # earliest year also loses its YoY deltas, since its prior year isn't in the panel.)
    suffix = f"_since_{since}" if since is not None else ""
    signals_name = f"tone_signals{suffix}.csv"
    examples_name = f"tone_examples{suffix}.csv"

    tone.to_csv(RESULTS_DIR / signals_name, index=False)
    examples = print_summary(tone, categories)
    examples.to_csv(RESULTS_DIR / examples_name, index=False)
    if suffix:
        print(f"\n(Partial --since run: wrote suffixed files, left the full-panel "
              f"tone_signals.csv untouched.)")
    print(f"\nWrote: results/{signals_name}, results/{examples_name}")


def _run_llm_pass(tone: pd.DataFrame, records: list[dict]) -> None:
    """Optional supplementary nuance pass (off by default)."""
    from tone_llm import analyze_tone_llm, llm_available
    if not llm_available():
        print("  --llm requested but ANTHROPIC_API_KEY not set; skipping LLM pass.")
        return
    mdna_by_key = {(r["cik"], r["fiscal_year"]): r["mdna"] for r in records}
    results = []
    for _, row in tone.iterrows():
        text = mdna_by_key.get((row["cik"], row["fiscal_year"]))
        results.append(analyze_tone_llm(text) if text else None)
    tone["llm_overall_tone"] = [r.get("overall_tone") if r else None for r in results]
    tone["llm_evasiveness"] = [r.get("evasiveness") if r else None for r in results]


if __name__ == "__main__":
    args = sys.argv[1:]
    since_val = None
    if "--since" in args:
        since_val = int(args[args.index("--since") + 1])
    main(since=since_val, use_llm=("--llm" in args))
