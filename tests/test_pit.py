"""Point-in-time (Phase 2) tests: available-as-of stamping, as-of filtering, restatement
classification, and after-close detection. Hermetic -- synthetic frames / companyfacts."""
import numpy as np
import pandas as pd
import pytest

from pit import (
    _after_close,
    _classify_change,
    build_filing_index,
    detect_restatements,
    point_in_time_view,
    stamp_available_as_of,
    summarize_restatements,
)


# --------------------------------------------------------------------------- classify
@pytest.mark.parametrize("ov,lv,exp_type,exp_restated", [
    (100, 100.2, "none", False),          # within tolerance
    (100, 105, "restated", True),         # modest revision -> restatement
    (-100, 200, "restated", True),        # sign flip within 10x -> restatement
    (100, 100_000, "scale_or_unit", False),  # 1000x -> unit/scale artifact
    (100, 5, "scale_or_unit", False),     # 20x smaller -> artifact
    (0, 50, "from_zero", False),          # undefined relative change
    (None, 5, "missing", False),
])
def test_classify_change(ov, lv, exp_type, exp_restated):
    _rel, change_type, restated = _classify_change(ov, lv, tol=0.005)
    assert change_type == exp_type
    assert restated is exp_restated


# --------------------------------------------------------------------------- after close
@pytest.mark.parametrize("dt,expected", [
    ("2017-03-15T21:21:49.000Z", True),   # 21:21 UTC -> after US close
    ("2020-02-27T11:37:51.000Z", False),  # pre-market
    ("2020-02-27T20:00:00.000Z", False),  # 20:00 UTC -> before conservative cutoff
    (None, False),
    (float("nan"), False),                # unmatched join -> NaN, must not raise
])
def test_after_close(dt, expected):
    assert _after_close(dt) is expected


# --------------------------------------------------------------------------- as-of view
def test_point_in_time_view_excludes_future_and_nan():
    df = pd.DataFrame({
        "ticker": ["A", "B", "C"],
        "available_as_of": ["2020-02-01", "2021-02-01", np.nan],
    })
    view = point_in_time_view(df, "2020-06-30")
    assert list(view["ticker"]) == ["A"]  # B is future, C not yet knowable (NaN)


# --------------------------------------------------------------------------- filing index
def test_build_filing_index_keeps_only_annual_forms():
    subs = {"filings": {"recent": {
        "accessionNumber": ["a1", "a2", "a3"],
        "form": ["10-K", "10-Q", "20-F"],
        "filingDate": ["2021-02-01", "2021-05-01", "2021-03-01"],
        "acceptanceDateTime": ["2021-02-01T21:00:00.000Z", "x", "2021-03-01T10:00:00.000Z"],
        "reportDate": ["2020-12-31", "2021-03-31", "2020-12-31"],
    }}}
    idx = build_filing_index(subs, cik=1)
    assert set(idx["form"]) == {"10-K", "20-F"}       # 10-Q dropped
    assert set(idx["accession"]) == {"a1", "a3"}


# --------------------------------------------------------------------------- stamping
def _long(rows):
    return pd.DataFrame(rows, columns=["cik", "fiscal_year", "filed", "accn",
                                       "period_end", "form"])


def test_available_as_of_is_latest_input_over_lags():
    # FY2020 score consumes FY2020 (+ FY2019, FY2018). Binding date = the latest.
    long = _long([
        (1, 2018, "2019-02-01", "a18", "2018-12-31", "10-K"),
        (1, 2019, "2020-02-01", "a19", "2019-12-31", "10-K"),
        (1, 2020, "2021-02-01", "a20", "2020-12-31", "10-K"),
    ])
    scores = pd.DataFrame({"cik": [1, 1], "fiscal_year": [2019, 2020], "fscore": [5, 6]})
    fi = build_filing_index({"filings": {"recent": {
        "accessionNumber": ["a19", "a20"], "form": ["10-K", "10-K"],
        "filingDate": ["2020-02-01", "2021-02-01"],
        "acceptanceDateTime": ["2020-02-01T11:00:00.000Z", "2021-02-01T21:00:00.000Z"],
        "reportDate": ["2019-12-31", "2020-12-31"]}}}, cik=1)

    out = stamp_available_as_of(scores, long, fi).set_index("fiscal_year")
    assert out.loc[2020, "available_as_of"] == "2021-02-01"
    assert out.loc[2020, "available_as_of_accession"] == "a20"
    assert out.loc[2020, "fiscal_period_end"] == "2020-12-31"
    assert out.loc[2020, "knowable_next_day"]           # accepted 21:00 UTC
    assert not out.loc[2019, "knowable_next_day"]       # accepted 11:00 UTC


def test_latest_input_rule_binds_on_a_later_prior_year_filing():
    # Edge: if FY2019's data were only filed AFTER FY2020's, the FY2020 score can't exist
    # until that later date. max() over lags must reflect it.
    long = _long([
        (1, 2019, "2021-03-01", "a19late", "2019-12-31", "10-K"),
        (1, 2020, "2021-02-01", "a20", "2020-12-31", "10-K"),
    ])
    scores = pd.DataFrame({"cik": [1], "fiscal_year": [2020], "fscore": [6]})
    out = stamp_available_as_of(scores, long, pd.DataFrame(
        columns=["accession", "acceptance_datetime"]))
    assert out.loc[0, "available_as_of"] == "2021-03-01"
    assert out.loc[0, "available_as_of_accession"] == "a19late"


# --------------------------------------------------------------------------- restatement
def _facts(concepts):
    out = {}
    for (tax, tag, unit), facts in concepts.items():
        out.setdefault(tax, {}).setdefault(tag, {"units": {}})["units"][unit] = facts
    return {"facts": out}


def _fact(val, end, fy, filed, accn, start=None):
    f = {"val": val, "end": end, "fy": fy, "fp": "FY", "form": "10-K", "filed": filed,
         "accn": accn}
    if start is not None:
        f["start"] = start
    return f


def test_detect_restatements_flags_divergence_uses_original():
    company = {"ticker": "T", "name": "TestCo", "is_known_case": True}
    facts = _facts({("us-gaap", "Assets", "USD"): [
        _fact(1000, "2020-12-31", 2020, "2021-02-10", "orig"),      # originally reported
        _fact(1200, "2020-12-31", 2021, "2022-02-10", "compar"),    # later revision (+20%)
    ]})
    r = detect_restatements(facts, company, cik=1)
    row = r[(r.logical_input == "total_assets") & (r.fiscal_year == 2020)].iloc[0]
    assert row["originally_reported"] == 1000     # Phase 1's point-in-time value
    assert row["latest_reported"] == 1200
    assert row["restated"]
    assert row["change_type"] == "restated"


def test_detect_restatements_skips_shares():
    company = {"ticker": "T", "name": "TestCo", "is_known_case": False}
    facts = _facts({
        # A stock split 7x-adjusts shares across filings -- must NOT be flagged.
        ("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic", "shares"): [
            _fact(1_000_000, "2020-12-31", 2020, "2021-02-10", "orig", start="2020-01-01"),
            _fact(7_000_000, "2020-12-31", 2021, "2022-02-10", "split", start="2020-01-01"),
        ],
        # A non-skipped concept so the result frame is non-empty.
        ("us-gaap", "Assets", "USD"): [
            _fact(1000, "2020-12-31", 2020, "2021-02-10", "orig"),
        ],
    })
    r = detect_restatements(facts, company, cik=1)
    assert "total_assets" in set(r.logical_input)
    assert "shares" not in set(r.logical_input)   # excluded (stock splits, not restatements)


def test_summarize_restatements_aggregates_per_year():
    rest = pd.DataFrame({
        "cik": [1, 1, 1],
        "fiscal_year": [2020, 2020, 2019],
        "logical_input": ["revenue", "cogs", "cfo"],
        "restated": [True, True, False],
    })
    s = summarize_restatements(rest).set_index("fiscal_year")
    assert s.loc[2020, "n_restated_inputs"] == 2
    assert set(s.loc[2020, "restated_inputs"].split(",")) == {"revenue", "cogs"}
    assert 2019 not in s.index    # no restated inputs that year
