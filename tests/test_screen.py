"""Phase 4 combined-screen tests. Hermetic -- synthetic accounting + tone frames.

Checks the flag logic at its thresholds, the red-flag count, the combined point-in-time
date (latest-input on merge), the reasons drill-down, and the >=2 corroboration rule."""
import numpy as np
import pandas as pd
import pytest

from config import SCREEN_MIN_FLAGS
from screen import build_screen


def _acct(fy, fscore, mscore, mflag, aao="2021-02-15", pend="2020-12-31"):
    return {"cik": 1, "ticker": "T", "name": "Co", "is_known_case": True,
            "fiscal_year": fy, "fscore": fscore, "mscore": mscore, "mscore_flag": mflag,
            "fiscal_period_end": pend, "available_as_of": aao}


def _tone(fy, hedging_rise, fls_drop, aao="2021-02-15",
          hedging=0.05, d_hedging=0.02, fls_freq=0.02, d_fls_freq=-0.01):
    return {"cik": 1, "fiscal_year": fy, "mdna_found": True, "hedging": hedging,
            "d_hedging": d_hedging, "fls_freq": fls_freq, "d_fls_freq": d_fls_freq,
            "hedging_rise": hedging_rise, "fls_drop": fls_drop, "available_as_of": aao}


def _build(acct_rows, tone_rows):
    return build_screen(pd.DataFrame(acct_rows), pd.DataFrame(tone_rows))


def test_each_flag_fires_on_its_own_threshold():
    s = _build(
        [_acct(2020, fscore=1, mscore=-1.0, mflag=True)],      # weak F + Beneish flag
        [_tone(2020, hedging_rise=True, fls_drop=True)],       # both tone flags
    ).iloc[0]
    assert s["flag_fscore_weak"] and s["flag_mscore_manip"]
    assert s["flag_hedging_rise"] and s["flag_fls_drop"]
    assert s["red_flags"] == 4
    assert s["screen_flagged"]


def test_fscore_flag_boundary():
    # F-Score exactly at PIOTROSKI_WEAK (2) fires; 3 does not.
    at = _build([_acct(2020, 2, -3.0, False)], [_tone(2020, False, False)]).iloc[0]
    above = _build([_acct(2020, 3, -3.0, False)], [_tone(2020, False, False)]).iloc[0]
    assert at["flag_fscore_weak"] and not above["flag_fscore_weak"]


def test_corroboration_threshold():
    one = _build([_acct(2020, 1, -3.0, False)], [_tone(2020, False, False)]).iloc[0]
    two = _build([_acct(2020, 1, -1.0, True)], [_tone(2020, False, False)]).iloc[0]
    assert one["red_flags"] == 1 and not one["screen_flagged"]
    assert two["red_flags"] == SCREEN_MIN_FLAGS and two["screen_flagged"]


def test_combined_available_as_of_is_latest_of_constituents():
    s = _build(
        [_acct(2020, 1, -1.0, True, aao="2021-02-15")],
        [_tone(2020, True, False, aao="2021-05-01")],   # tone dated later
    ).iloc[0]
    assert s["combined_available_as_of"] == "2021-05-01"   # the max


def test_reasons_names_fired_signals_only():
    s = _build(
        [_acct(2020, 1, -1.2, True)],
        [_tone(2020, hedging_rise=True, fls_drop=False)],
    ).iloc[0]
    assert "F-Score=1" in s["reasons"]
    assert "M-Score=-1.20" in s["reasons"]
    assert "hedging" in s["reasons"]
    assert "fwd-looking" not in s["reasons"]     # fls_drop did not fire


def test_nan_scores_do_not_flag():
    s = _build(
        [_acct(2020, np.nan, np.nan, False)],
        [_tone(2020, False, False)],
    ).iloc[0]
    assert s["red_flags"] == 0
    assert not s["screen_flagged"]
