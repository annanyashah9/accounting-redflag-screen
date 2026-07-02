"""
Phase 4 entry point (final): combine the accounting + tone signals into one red-flag screen
and evaluate it honestly.

Run:  python src/run_phase4.py

Reuses the OUTPUTS of the prior phases (their reusable artifacts):
  results/scores_pit.csv   (Phase 2)  -- accounting scores + available_as_of
  results/tone_signals.csv (Phase 3)  -- tone signals + YoY deltas + available_as_of
Produces the combined screen, the drill-down, a figure, and the README evaluation section.
No signal is recomputed here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from evaluate import build_evaluation_markdown, make_heatmap, surface_check
from screen import build_screen

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "results"
FIGURES_DIR = ROOT / "figures"
README = ROOT / "README.md"
PHASE4_MARKER = "## Phase 4: combined screen & honest evaluation"


def _load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    sp, ts = RESULTS_DIR / "scores_pit.csv", RESULTS_DIR / "tone_signals.csv"
    missing = [p.name for p in (sp, ts) if not p.exists()]
    if missing:
        sys.exit(f"Missing {missing}. Run: python src/run_phase2.py && "
                 f"python src/run_phase3.py first.")
    return pd.read_csv(sp), pd.read_csv(ts)


def _write_readme(section: str) -> None:
    existing = README.read_text() if README.exists() else ""
    if PHASE4_MARKER in existing:            # idempotent
        existing = existing[:existing.index(PHASE4_MARKER)].rstrip() + "\n"
    README.write_text(existing.rstrip() + "\n" + section)


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

    print("\nDoes it surface the seeded known accounting-problem cases? (point-in-time)")
    print(surface.to_string(index=False))

    print("\nFlagged company-years with drill-down (headline artifact):")
    cols = ["ticker", "fiscal_year", "is_known_case", "red_flags",
            "combined_available_as_of", "reasons"]
    with pd.option_context("display.max_colwidth", 90, "display.width", 160):
        print(flagged.sort_values(["is_known_case", "combined_available_as_of"],
                                  ascending=[False, True])[cols].to_string(index=False))


def main() -> None:
    scores_pit, tone_signals = _load_inputs()
    screen = build_screen(scores_pit, tone_signals)

    # --- point-in-time integrity check: flagged rows are dated after their period-end ---
    flagged = screen[screen["screen_flagged"]]
    bad = flagged[flagged["combined_available_as_of"] <= flagged["fiscal_period_end"]]
    assert bad.empty, f"lookahead: {len(bad)} flagged rows dated on/before period-end"

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    screen.to_csv(RESULTS_DIR / "screen.csv", index=False)
    flagged.to_csv(RESULTS_DIR / "screen_flagged.csv", index=False)

    surface = surface_check(screen)
    make_heatmap(screen, FIGURES_DIR / "screen_heatmap.png")
    _write_readme(build_evaluation_markdown(screen, surface))

    print_summary(screen, surface)
    print("\nWrote: results/screen.csv, results/screen_flagged.csv, "
          "figures/screen_heatmap.png, README.md")


if __name__ == "__main__":
    main()
