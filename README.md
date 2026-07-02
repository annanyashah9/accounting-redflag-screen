# Accounting Red-Flag Screen

A systematic screen that flags accounting red flags and management-tone shifts across a small,
fixed universe of companies, built entirely on free SEC EDGAR data. Two things make it worth
looking at: **point-in-time discipline** (every signal is stamped with the date it would
actually have been knowable, so there's no lookahead) and an **honest evaluation** that names
the biases free data can't remove.

It is a **red-flag screen, not a predictor.** The question it answers is *"does this surface
the companies it should?"* — never *"does this predict returns?"* That distinction is the
whole point, and the project is built to be defensible rather than impressive.

---

## The idea

Two well-known, transparent accounting metrics — the **Piotroski F-Score** (fundamental
strength, 0–9) and the **Beneish M-Score** (earnings-manipulation likelihood) — are computed
from XBRL financials, then combined with a **management-tone** signal read from each 10-K's
MD&A using a finance-specific lexicon. A company-year is flagged when several *independent*
signals agree. Every flag drills down to exactly which metric and which sentence-level shift
triggered it.

The universe is ~30 companies: ~20 ordinary large-cap controls plus ~10 companies with
well-documented historical accounting problems (Hertz, Kraft Heinz, Under Armour, Valeant/
Bausch, MiMedx, Nikola, SunEdison, Celadon, Mattel, GE) seeded in so we can ask whether the
screen actually surfaces them.

---

## How it's built (four phases)

Each phase reuses the previous ones unchanged and adds one layer.

**Phase 1 — scoring engine** (`config.py`, `edgar.py`, `extract.py`, `scores.py`)
Pulls annual financials from EDGAR's `companyfacts` API and computes both scores. The hard
part isn't the arithmetic — it's that XBRL tags don't map cleanly to textbook line items.
Tags get renamed across years (the ASC 606 revenue change), vary across filers (J&J tagged
revenue as `SalesRevenueGoodsNet`), and some line items (total liabilities) often aren't
tagged at all. Each input maps to an *ordered list* of candidate tags; if none is found the
input is left blank **and flagged**, never silently substituted — a mis-mapped tag produces a
plausible-but-wrong score, which is worse than a missing one. Fiscal years are keyed off the
reported `fy`, not the period-end date, which avoids an off-by-one for 52/53-week filers whose
year ends in early January.

**Phase 2 — point-in-time discipline** (`pit.py`)
A score built from a fiscal year's financials was *not* knowable on the last day of that year;
the 10-K lands weeks to months later (median ~50 days here). Stamping a score with the
period-end date silently claims knowledge nobody had — the most common way a backtest lies.
So every score gets an `available_as_of` date: the filing date of the last input it needs
(the *latest-input rule*), sourced from the per-fact `filed` field and enriched via the EDGAR
submissions API with the intra-day acceptance time and form type. `point_in_time_view(df,
date)` then reconstructs the screen exactly as it looked on any past date. The stubborn
limitation — restatements — is detected and disclosed rather than hidden (see below).

**Phase 3 — management-tone NLP** (`lexicon.py`, `filings_text.py`, `tone.py`)
Extracts the MD&A (Item 7) from each 10-K and scores its tone with the **Loughran-McDonald**
finance lexicon — deliberately *not* a general sentiment model, which would read neutral words
like "liability", "cost", and "capital" as negative and produce noise. The signals that matter
are the **year-over-year changes** (a rise in hedging language, a drop in forward-looking
statements), because a within-company shift is far more defensible than comparing tone levels
across companies with different disclosure styles. Because the tone for year Y comes from the
same 10-K as year Y's financials, it inherits the identical `available_as_of` — the tone side
introduces no new lookahead.

**Phase 4 — combine and evaluate** (`screen.py`, `evaluate.py`)
Merges everything into one screen. Four independent binary flags — weak F-Score (≤2), Beneish
manipulation flag (M > −1.78), a YoY hedging rise, a YoY forward-looking drop — each on its
*own* pre-published threshold, equal-weighted into a 0–4 count. A company-year is flagged when
**≥2 independent signals corroborate** (Beneish alone has a high false-positive rate). The
combined `available_as_of` is the latest of the constituents. Nothing is fit or tuned.

---

## Point-in-time, concretely

The clearest illustration is Hertz. Its FY2014 10-K was delayed by an accounting restatement
until **2015-07-16**, so on 2015-04-01 the screen simply *doesn't contain* a Hertz FY2014 row —
even though a naive "all data now" view shows it. Across the panel, only 84 of 306 computable
scores were actually knowable as of 2015-04-01; the rest would be lookahead.

**Restatement contamination** is the limitation point-in-time stamping alone can't fix.
`companyfacts` returns each figure as it exists *now*, so a restated prior year may have
overwritten what was originally reported. Phase 1 already selects the earliest-filed
(originally-reported) value, and Phase 2 additionally compares it against the latest-reported
value for the same period and flags the divergence — e.g. Hertz's FY2012 total assets were
first reported at $23.29B and later revised to $23.13B. What free data still can't guarantee:
if `companyfacts` never retained the original, the earliest value is already contaminated and
the check can't see it. We don't claim the data is fully clean — only that filing-lag
lookahead is removed and residual contamination is surfaced, not buried.

---

## Does it surface the right companies?

Qualitative, by design — no precision/recall on a return model. Of the 10 seeded problem
cases, the screen raises a corroborated (≥2) flag for **four** — MiMedx, Kraft Heinz, Mattel,
and Hertz — each at a plausible, point-in-time-honest date (Hertz FY2015 knowable 2016-02-29,
Mattel FY2017 knowable 2018-02-27, before its 2019 restatement disclosure). The control flag
rate is ~0.9%.

**It misses the other six, and that's the honest result.** A rule tuned to these few cases
would light up all ten; this one wasn't, so it doesn't. The misses are also informative:
Valeant/Bausch and GE frequently have no M-Score at all (missing gross-margin XBRL inputs),
and Nikola, SunEdison, and Celadon are delisted with short histories, leaving too few
year-over-year tone deltas to trip. The gaps trace straight back to the data limitations.

### Limitations free data can't remove

- **Survivorship bias.** The controls are companies still listed today; the delisted and
  bankrupt firms — exactly what a red-flag screen should catch — are underrepresented, and even
  the seeded delisted names had to be pinned by CIK by hand. A credible screen needs the full
  historical cross-section, including companies that no longer exist.
- **Restatement contamination.** As above — data isn't always truly as-originally-reported.
- **Tiny universe (~30).** This is a demonstration of a defensible *method*, not a
  statistically powered test. No predictive claim is made.
- **Overfitting — avoided by construction.** Published thresholds, equal weights, a ≥2-flag
  rule fixed a priori. The imperfect hit rate is the evidence that it wasn't tuned to the
  answer.

### What a proper point-in-time backtest would need

A survivorship-free universe including delisted firms; a true as-originally-reported (point-in-
time) fundamentals database; a far larger sample; out-of-sample rule specification; and an
outcome defined *not* as returns but as subsequent restatement or enforcement action, judged
against proper base rates. This project deliberately stops short of that — at a defensible,
point-in-time-disciplined screen with its traps named.

---

## Repository layout

```
src/
  config.py        universe, User-Agent, XBRL tag map, score constants, tone lexicon config
  edgar.py         EDGAR fetch + on-disk caching (companyfacts, submissions, ticker map)
  extract.py       XBRL tag-mapping -> tidy annual facts (the Phase 1 landmine)
  scores.py        Piotroski F-Score + Beneish M-Score (vectorized)
  pit.py           available_as_of stamping, restatement detection, point_in_time_view
  lexicon.py       Loughran-McDonald dictionary loader + tokenizer
  filings_text.py  10-K primary-doc fetch + MD&A (Item 7) extraction
  tone.py          LM tone signals + YoY deltas
  tone_llm.py      optional LLM nuance pass (off by default)
  screen.py        combine accounting + tone into the flagged screen
  evaluate.py      surface check, heatmap figure, evaluation writeup
  run_phase{1..4}.py  one runnable entry point per phase
tests/             hermetic pytest suite (no network)
data/              cached EDGAR responses + lexicon (data/filings/ is gitignored, ~1.5GB)
results/           output tables (CSV) + results/EVALUATION.md
figures/           screen_heatmap.png
```

## Running it

```bash
python src/run_phase1.py                 # scores            -> results/scores.csv
python src/run_phase2.py [AS_OF_DATE]    # point-in-time     -> results/scores_pit.csv, restatements.csv
python src/run_phase3.py [--since YEAR]  # tone signals      -> results/tone_signals.csv
python src/run_phase4.py                 # combined screen   -> results/screen.csv, figures/, EVALUATION.md
```

Set your contact string in `config.py` (`USER_AGENT`) — SEC requires a real one. Every EDGAR
response is cached under `data/`, so after the first run everything is reproducible offline
against a fixed snapshot. Phase 4 reads the Phase 2/3 output CSVs, so run the phases in order.

## Testing

```bash
python -m pytest        # 53 hermetic tests, no network
```

Tests cover the score math, the XBRL tag-mapping edge cases (fiscal-year labeling, tag
priority, originally-reported selection), the point-in-time and restatement logic, the tone
signals, and the combination rule.

## Notes & attribution

- **Score scope.** Piotroski (2000) was designed for high book-to-market value firms; Beneish
  (1999) for 1982–1992 manufacturers and carries a high false-positive rate. Both are applied
  outside their original scope here, and that's stated in the output rather than glossed over.
- **Data.** SEC EDGAR (`data.sec.gov`), free, no key, real User-Agent required.
- **Lexicon.** Loughran-McDonald Master Dictionary (Loughran & McDonald, *J. Finance*, 2011),
  free for academic use.
