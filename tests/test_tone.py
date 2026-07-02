"""Tone-NLP tests (Phase 3). Hermetic -- a tiny in-memory lexicon, synthetic text; no
network, no real LM dictionary."""
import numpy as np
import pandas as pd
import pytest

from config import MDNA_MIN_WORDS
from filings_text import extract_mdna, html_to_text
from lexicon import tokenize
from tone import add_yoy_deltas, build_tone_panel, compute_tone

# Minimal finance lexicon: note "LIABILITY"/"COST" are deliberately NOT negative (the exact
# distinction Loughran-McDonald makes that a general sentiment model gets wrong).
TINY = {
    "Negative": frozenset({"LOSS", "LITIGATION", "DECLINE"}),
    "Positive": frozenset({"GROWTH"}),
    "Uncertainty": frozenset({"MAY", "UNCERTAIN"}),
    "Litigious": frozenset({"LITIGATION"}),
    "Strong_Modal": frozenset({"WILL"}),
    "Weak_Modal": frozenset({"MAY", "COULD"}),
}


def test_tokenize_uppercases_and_splits():
    assert tokenize("Loss and uncertainty.") == ["LOSS", "AND", "UNCERTAINTY"]


def test_compute_tone_counts_and_normalizes():
    text = ("The company reported a loss. Results may be uncertain. "
            "We will pursue growth. Litigation could follow.")
    t = compute_tone(text, TINY)
    assert t["doc_word_count"] == 16
    assert t["lm_negative"] == pytest.approx(2 / 16)   # loss + litigation
    assert t["hedging"] == pytest.approx(4 / 16)       # may, uncertain, may, could
    assert t["net_tone"] == pytest.approx((1 - 2) / 16)


def test_finance_lexicon_excludes_neutral_words():
    # A general sentiment model would call "liability" and "cost" negative; LM does not.
    t = compute_tone("The liability and the cost of capital and tax.", TINY)
    assert t["lm_negative"] == 0.0


def test_fls_frequency_detects_forward_looking_cues():
    assert compute_tone("We expect the outlook to improve going forward.", TINY)["fls_freq"] > 0
    assert compute_tone("The cat sat on the mat.", TINY)["fls_freq"] == 0.0


def test_extract_mdna_finds_body_and_skips_toc():
    body = "revenue increased " * 400
    html = (
        "<html><body>"
        "<div>Table of Contents Item 7. Management's Discussion and Analysis 45 "
        "Item 8. Financial Statements 70</div>"
        "<p>ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION. " + body +
        " ITEM 8. FINANCIAL STATEMENTS</p>"
        "</body></html>"
    )
    section = extract_mdna(html_to_text(html))
    assert section is not None
    assert "revenue increased" in section
    assert len(section.split()) >= MDNA_MIN_WORDS


def test_extract_mdna_returns_none_when_absent():
    assert extract_mdna("This filing has no management discussion section at all.") is None


def _panel_row(cik, fy, hedging, fls, **extra):
    row = {"cik": cik, "ticker": f"T{cik}", "name": "C", "is_known_case": False,
           "fiscal_year": fy, "mdna_found": True, "doc_word_count": 1000,
           "hedging": hedging, "fls_freq": fls,
           "lm_negative": 0.0, "lm_uncertainty": 0.0, "net_tone": 0.0}
    row.update(extra)
    return row


def test_add_yoy_deltas_and_shift_flags():
    panel = pd.DataFrame([
        _panel_row(1, 2018, 0.10, 0.05),
        _panel_row(1, 2019, 0.20, 0.05),   # hedging +100% -> rise flag
        _panel_row(1, 2021, 0.30, 0.05),   # gap (2020 missing) -> delta NaN, no flag
        _panel_row(2, 2018, 0.10, 0.10),
        _panel_row(2, 2019, 0.10, 0.05),   # fls -50% -> drop flag
    ])
    out = add_yoy_deltas(panel).set_index(["cik", "fiscal_year"])
    assert out.loc[(1, 2019), "d_hedging"] == pytest.approx(0.10)
    assert bool(out.loc[(1, 2019), "tone_shift"]) is True
    assert np.isnan(out.loc[(1, 2021), "d_hedging"])       # non-consecutive -> NaN
    assert bool(out.loc[(1, 2021), "tone_shift"]) is False
    assert bool(out.loc[(2, 2019), "fls_drop"]) is True


def test_build_tone_panel_flags_missing_mdna():
    records = [
        {"cik": 1, "ticker": "A", "name": "A", "is_known_case": False,
         "fiscal_year": 2020, "mdna": "loss and growth and litigation"},
        {"cik": 1, "ticker": "A", "name": "A", "is_known_case": False,
         "fiscal_year": 2021, "mdna": None},   # extraction failed -> flagged, not fabricated
    ]
    panel = build_tone_panel(records, TINY).set_index("fiscal_year")
    assert panel.loc[2020, "mdna_found"]
    assert not panel.loc[2021, "mdna_found"]
    assert pd.isna(panel.loc[2021, "hedging"]) or "hedging" not in panel.columns
