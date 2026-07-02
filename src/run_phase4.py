"""
Phase 4 entry point (final): combine the accounting + tone signals into one red-flag screen
and evaluate it honestly.

Run:  python src/run_phase4.py

Reuses the OUTPUTS of the prior phases (their reusable artifacts):
  results/scores_pit.csv   (Phase 2)  -- accounting scores + available_as_of
  results/tone_signals.csv (Phase 3)  -- tone signals + YoY deltas + available_as_of
Produces the combined screen, the drill-down, a figure, and results/EVALUATION.md (the
auto-generated companion to the hand-written README evaluation).
No signal is recomputed here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from edgar import get_submissions
from evaluate import build_evaluation_markdown, make_heatmap, surface_check
from filing_behavior import late_filing_flags
from screen import build_screen

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
FIGURES_DIR = ROOT / "figures"
# The narrative evaluation lives in the hand-written README; this file is the auto-generated
# companion with the current run's exact numbers.
EVALUATION_MD = RESULTS_DIR / "EVALUATION.md"


def _load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    sp, ts = RESULTS_DIR / "scores_pit.csv", RESULTS_DIR / "tone_signals.csv"
    missing = [p.name for p in (sp, ts) if not p.exists()]
    if missing:
        sys.exit(f"Missing {missing}. Run: python src/run_phase2.py && "
                 f"python src/run_phase3.py first.")
    return pd.read_csv(sp), pd.read_csv(ts)


def print_summary(screen: pd.DataFrame, surface: pd.DataFrame) -> None:
    print("\n" + "=" * 78)
    print("PHASE 4 — COMBINED RED-FLAG SCREEN & HONEST EVALUATION")
    print("=" * 78)
    print("\nA point-in-time-disciplined SCREEN, evaluated as 'does it surface the right\n"
          "companies?' -- NOT a return/earnings-miss predictor.\n")

    flagged = screen[screen["screen_flagged"]]
    print(f"Company-years: {len(screen)} | flagged (>=2 independent red flags): "
          f"{len(flagged)}")
    print("red_flags distribution: "
          + ", ".join(f"{k}:{v}" for k, v in
                      screen['red_flags'].value_counts().sort_index().items()))

    # Integrity check on the late-filing signal: is it discriminating (known >> control)?
    if "flag_late_filing" in screen.columns:
        kc, ctrl = screen[screen.is_known_case], screen[~screen.is_known_case]
        print(f"late-filing flag rate: known cases {kc['flag_late_filing'].mean():.1%} "
              f"vs controls {ctrl['flag_late_filing'].mean():.1%} | "
              f"control screen-flag rate {ctrl['screen_flagged'].mean():.1%}")

    if "screen_status" in screen.columns:
        order = ["flagged", "watch", "clear", "insufficient_data"]
        counts = screen["screen_status"].value_counts()
        print("\nscreen_status (so 'not flagged' isn't misread as 'clean'): "
              + ", ".join(f"{k}:{int(counts.get(k, 0))}" for k in order))
        print("  -> a company is only 'clear' if we could actually compute its scores; "
              "GE etc. read 'insufficient_data', not 'clear'.")

    print("\nDoes it surface the seeded known accounting-problem cases? (point-in-time)")
    print(surface.to_string(index=False))

    print("\nFlagged company-years with drill-down (headline artifact):")
    cols = ["ticker", "fiscal_year", "is_known_case", "red_flags",
            "combined_available_as_of", "reasons"]
    with pd.option_context("display.max_colwidth", 90, "display.width", 160):
        print(flagged.sort_values(["is_known_case", "combined_available_as_of"],
                                  ascending=[False, True])[cols].to_string(index=False))


def _load_late_filing(scores_pit: pd.DataFrame) -> pd.DataFrame:
    """Late-filing flags per fiscal period from cached submissions (no re-fetch)."""
    frames = []
    for cik in sorted(scores_pit["cik"].unique()):
        subs = get_submissions(int(cik))
        if subs is not None:
            frames.append(late_filing_flags(subs, int(cik)))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> None:
    scores_pit, tone_signals = _load_inputs()
    late_filing = _load_late_filing(scores_pit)
    screen = build_screen(scores_pit, tone_signals, late_filing)

    # --- point-in-time integrity check: flagged rows are dated after their period-end ---
    flagged = screen[screen["screen_flagged"]]
    bad = flagged[flagged["combined_available_as_of"] <= flagged["fiscal_period_end"]]
    assert bad.empty, f"lookahead: {len(bad)} flagged rows dated on/before period-end"

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    screen.to_csv(RESULTS_DIR / "screen.csv", index=False)
    flagged.to_csv(RESULTS_DIR / "screen_flagged.csv", index=False)

    surface = surface_check(screen)
    make_heatmap(screen, FIGURES_DIR / "screen_heatmap.png")
    EVALUATION_MD.write_text(build_evaluation_markdown(screen, surface))

    print_summary(screen, surface)
    print("\nWrote: results/screen.csv, results/screen_flagged.csv, "
          "results/EVALUATION.md, figures/screen_heatmap.png")


if __name__ == "__main__":
    main()
