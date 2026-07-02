# Accounting Red-Flag Screen

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
median of ~50 days after period-end). Any screen that stamps a score with the
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
of **2015-04-01**, only **84** of 306 computable score-rows were
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
480 figures flagged in this run — e.g. Hertz's FY2012 total assets were reported at
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
- `results/pit_demo_2015-04-01.csv` — the as-of screen vs the naive "all data now" view.

## Reproducing
`python src/run_phase1.py` then `python src/run_phase2.py [AS_OF_DATE]`. All EDGAR responses
are cached under `data/`, so runs are reproducible against a fixed snapshot.

## Phase 3: management-tone signal

Phase 3 adds a **management-tone** signal from the 10-K's MD&A (Item 7), stamped with the
SAME point-in-time discipline as the accounting scores. The tone signal for fiscal year Y is
extracted from the very 10-K that reports year Y's financials, so it shares that filing's
`available_as_of` date exactly — no new lookahead.

### Why a finance lexicon (not general sentiment)
Signals use the **Loughran-McDonald** dictionary, built from 10-Ks, *not* a general-purpose
sentiment model. General models misclassify neutral financial vocabulary — "liability",
"cost", "risk", "capital", "tax" — as negative and produce noise on filings. LM assigns
finance-aware categories (Negative, Positive, Uncertainty, Litigious, Strong/Weak Modal).

### Signals (length-normalized proportions of MD&A tokens)
`lm_negative/positive/uncertainty/litigious/weak_modal/strong_modal`, `hedging`
(= uncertainty + weak-modal), `net_tone` (= positive − negative), and `fls_freq`
(forward-looking-cue frequency, a transparent documented heuristic — not a validated
classifier). **The screen inputs are the year-over-year DELTAS** (`d_*`), because a
within-company *change* in tone is far more defensible than a cross-company level comparison
(disclosure styles differ). `tone_shift` flags a marked YoY rise in hedging or drop in
forward-looking frequency (illustrative thresholds, not predictive-tuned).

### Point-in-time and limits
MD&A section extraction is a heuristic (Item 7 → Item 7A/Item 8, longest span). When it fails
the signal is left blank and flagged `mdna_found=False`, never fabricated. In this run MD&A
was extracted for 442 company-years and 48 showed a notable YoY tone shift. An
optional LLM nuance pass (`--llm`, off by default) can augment the lexicon signals but never
replaces them — the lexicon core is the transparent, reproducible screen.

### Outputs
- `results/tone_signals.csv` — tone signals + YoY deltas + `available_as_of`, merge-ready
  with `results/scores_pit.csv` on (cik, fiscal_year).
- `results/tone_examples.csv` — drill-down of notable YoY tone shifts.

## Phase 4: combined screen & honest evaluation

Phase 4 merges the accounting scores (Phases 1-2) and the tone signals (Phase 3) into one
red-flag screen and evaluates it **as a screen** -- *"does it surface the companies it
should?"* -- **not** as a return or earnings-miss predictor.

### Combining, point-in-time
Four independent binary red flags, each on its **own pre-published / Phase-set threshold**
(nothing re-tuned here): weak Piotroski F-Score (<= 2), Beneish M-Score manipulation flag
(> -1.78), a YoY rise in hedging language, and a YoY drop in forward-looking language.
`red_flags` is their equal-weighted count (0-4); a company-year is **flagged when >= 2
independent signals agree** (corroboration, because Beneish alone has a high false-positive
rate). Each flagged row carries a `reasons` drill-down naming exactly which metric and which
tone shift fired. The combined `available_as_of` is the **latest** of the constituent signals'
dates -- here identical, since the tone text comes from the same 10-K as the accounting data --
so merging adds **no lookahead**.

### Does it surface the right companies?
Of **10** seeded known-problem cases, the screen raised a corroborated (>= 2)
flag for **4**, and at least one red flag for **6** -- versus a
control flag rate of **0.9%** (known-case rate 4.0%). The flags appear at
plausible, point-in-time-honest dates (e.g. before the problem became public). Per case:

```
ticker                                          name  years_scored flagged_years(>=2) earliest_flag_knowable  years_with_any_flag
  MDXG                                  MiMedx Group            14               2023             2024-02-28                    4
   KHC                                   Kraft Heinz            11         2018, 2024             2019-06-07                    5
   MAT                                        Mattel            17               2017             2018-02-27                    7
   HTZ Hertz Global Holdings (pre-2016-split entity)            16               2015             2016-02-29                    2
   BHC                   Bausch Health (fka Valeant)            16                  -                      -                    0
  CLDN                                 Celadon Group             5                  -                      -                    0
    GE                              General Electric            16                  -                      -                    0
  NKLA                                        Nikola             7                  -                      -                    0
  SUNE                                     SunEdison             6                  -                      -                    2
   UAA                                  Under Armour            16                  -                      -                    4
```

**This is a partial, honest result -- and that is the point.** The rule was specified on
principle, not tuned; a rule tuned to these few cases would light up all of them. It misses
several known cases, and the misses are informative: BHC/GE frequently have **no M-Score**
(missing gross-margin XBRL inputs, Phase 1), and NKLA/SUNE/CLDN are **delisted with short
histories**, so they have few YoY tone deltas to trip. The misses trace directly to the data
limitations below.

### Limitations free data cannot remove (named directly)
1. **Survivorship bias.** The controls are companies still listed today; delisted/bankrupt
   firms -- exactly what a red-flag screen should catch -- are underrepresented. Even the
   seeded delisted cases are few and had to be CIK-pinned by hand. A credible screen needs the
   full historical cross-section **including delisted names**; free current-ticker data does
   not provide it.
2. **Restatement contamination** (from Phase 2). companyfacts returns data as it exists now;
   originally-reported figures are not always recoverable, so even correct filing-date
   stamping can surface numbers nobody had at the time. Detected and disclosed, not fully
   fixable.
3. **Small, fixed universe (~30).** This is a **demonstration of a defensible method, not a
   statistically powered test.** No precision/recall or predictive-power claim is made.
4. **Overfitting risk -- avoided by construction.** Thresholds are the signals' own published
   values, weights are equal, and the >= 2-flag rule was fixed a priori. The
   imperfect known-case hit rate is the evidence that it was not tuned to the answer.

### What a proper point-in-time backtest would require
A survivorship-free universe including delisted firms; a true as-originally-reported
(point-in-time) fundamentals database; a far larger sample; out-of-sample rule specification;
and an outcome defined **not as returns** but as subsequent restatement / enforcement action,
evaluated against proper base rates. This project deliberately stops at a **defensible,
point-in-time-disciplined screen with its traps named** -- which is the honest contribution.

### Outputs
- `results/screen.csv` -- the full combined screen with flags, `red_flags`, `reasons`, and
  `combined_available_as_of`.
- `results/screen_flagged.csv` -- flagged company-years with drill-down.
- `figures/screen_heatmap.png` -- red-flag counts by company x year (known cases vs controls).
