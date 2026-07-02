"""Score-math tests: Piotroski F-Score signals and Beneish M-Score components.

Hermetic -- a hand-built 3-year panel with known values; expected signal values are
hand-derived so a regression in the formulas is caught."""
import numpy as np
import pandas as pd
import pytest

from config import BENEISH_COEF
from scores import beneish_mscore, piotroski_fscore, score_all

KEYS = dict(cik=1, ticker="T", name="TestCo", is_known_case=False)

# Three consecutive fiscal years so FY2020 has full t-1 / t-2 lags.
PANEL = [
    {**KEYS, "fiscal_year": 2018, "total_assets": 1000, "net_income": 100, "cfo": 150,
     "long_term_debt": 200, "current_assets": 400, "current_liabilities": 200,
     "shares": 1000, "revenue": 1000, "cogs": 600, "receivables": 100, "ppe_net": 300,
     "depreciation": 50, "sga": 100, "income_continuing": 100},
    {**KEYS, "fiscal_year": 2019, "total_assets": 1100, "net_income": 120, "cfo": 180,
     "long_term_debt": 180, "current_assets": 500, "current_liabilities": 200,
     "shares": 1000, "revenue": 1100, "cogs": 650, "receivables": 110, "ppe_net": 320,
     "depreciation": 55, "sga": 110, "income_continuing": 120},
    {**KEYS, "fiscal_year": 2020, "total_assets": 1200, "net_income": 150, "cfo": 200,
     "long_term_debt": 150, "current_assets": 600, "current_liabilities": 200,
     "shares": 1000, "revenue": 1300, "cogs": 700, "receivables": 120, "ppe_net": 350,
     "depreciation": 60, "sga": 120, "income_continuing": 150},
]


@pytest.fixture
def wide():
    return pd.DataFrame(PANEL)


def _row(df, fy):
    return df[df.fiscal_year == fy].iloc[0]


def test_piotroski_all_nine_fire(wide):
    f = piotroski_fscore(wide)
    r = _row(f, 2020)
    # This panel was constructed so every signal is positive in FY2020.
    for sig in ["f_roa", "f_cfo", "f_droa", "f_accrual", "f_dleverage",
                "f_dliquidity", "f_neqissue", "f_dmargin", "f_dturnover"]:
        assert r[sig] == 1.0, sig
    assert r["fscore"] == 9.0
    assert r["fscore_available"] == 9
    assert r["fscore_missing_inputs"] == ""


def test_piotroski_earliest_year_has_no_lags(wide):
    # FY2018 cannot compute change/lag signals -> incomplete -> fscore NaN, not fabricated.
    f = piotroski_fscore(wide)
    assert np.isnan(_row(f, 2018)["fscore"])


def test_piotroski_dilution_signal_flips_on_issuance(wide):
    df = wide.copy()
    df.loc[df.fiscal_year == 2020, "shares"] = 1200  # issued stock vs 1000 prior year
    r = _row(piotroski_fscore(df), 2020)
    assert r["f_neqissue"] == 0.0
    assert r["fscore"] == 8.0


def test_missing_gross_margin_flags_not_fabricates(wide):
    df = wide.copy()
    # Remove both cost lines for FY2020 -> gross margin undefined.
    df.loc[df.fiscal_year == 2020, ["cogs"]] = np.nan
    df["gross_profit"] = np.nan
    r = _row(piotroski_fscore(df), 2020)
    assert np.isnan(r["f_dmargin"])
    assert np.isnan(r["fscore"])            # incomplete -> not fabricated
    assert "gross_margin" in r["fscore_missing_inputs"]


def test_gross_profit_fallback_recovers_margin(wide):
    df = wide.copy()
    # No COGS, but GrossProfit reported -> margin computable from GrossProfit/Revenue.
    df["cogs"] = np.nan
    df["gross_profit"] = [400, 450, 600]  # rows are ordered FY2018, 2019, 2020
    r = _row(piotroski_fscore(df), 2020)
    assert not np.isnan(r["f_dmargin"])
    assert "gross_margin" not in r["fscore_missing_inputs"]


def test_beneish_components_match_hand_values(wide):
    m = beneish_mscore(wide)
    r = _row(m, 2020)
    # Hand-computed from the panel (see values above).
    assert r["DSRI"] == pytest.approx((120/1300) / (110/1100), rel=1e-6)
    assert r["SGI"] == pytest.approx(1300/1100, rel=1e-6)
    assert r["TATA"] == pytest.approx((150-200)/1200, rel=1e-6)
    assert r["GMI"] == pytest.approx((450/1100) / (600/1300), rel=1e-6)
    assert r["LVGI"] == pytest.approx(((200+150)/1200) / ((200+180)/1100), rel=1e-6)


def test_beneish_mscore_equals_coefficient_combination(wide):
    r = _row(beneish_mscore(wide), 2020)
    c = BENEISH_COEF
    expected = (c["intercept"]
                + c["DSRI"]*r["DSRI"] + c["GMI"]*r["GMI"] + c["AQI"]*r["AQI"]
                + c["SGI"]*r["SGI"] + c["DEPI"]*r["DEPI"] + c["SGAI"]*r["SGAI"]
                + c["TATA"]*r["TATA"] + c["LVGI"]*r["LVGI"])
    assert r["mscore"] == pytest.approx(expected, rel=1e-9)
    assert bool(r["mscore_flag"]) == (r["mscore"] > -1.78)


def test_score_all_carries_keys_and_both_scores(wide):
    s = score_all(wide)
    assert {"fscore", "mscore", "mscore_flag", "fiscal_year", "ticker"} <= set(s.columns)
    assert len(s) == 3
