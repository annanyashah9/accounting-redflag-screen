"""Tag-mapping / extraction tests: the Phase 1 landmine logic.

Builds synthetic companyfacts JSON so we can assert fiscal-year labeling, tag priority,
originally-reported selection, the annual-duration filter, and phantom-year avoidance."""
import pandas as pd

from extract import build_fundamentals, extract_annual_facts

COMPANY = {"ticker": "T", "name": "TestCo", "is_known_case": False}


def _fact(val, end, fy, filed, accn, form="10-K", fp="FY", start=None):
    f = {"val": val, "end": end, "fy": fy, "fp": fp, "form": form, "filed": filed,
         "accn": accn}
    if start is not None:
        f["start"] = start
    return f


def _facts(concepts):
    """concepts: {(taxonomy, tag, unit): [fact, ...]} -> companyfacts-shaped dict."""
    out = {}
    for (tax, tag, unit), facts in concepts.items():
        out.setdefault(tax, {}).setdefault(tag, {"units": {}})["units"].setdefault(unit, [])
        out[tax][tag]["units"][unit].extend(facts)
    return {"facts": out}


def test_fiscal_year_uses_reported_fy_not_end_year():
    # 52/53-week filer: FY2015 ends 2016-01-03. Labeling by end.year would give 2016.
    facts = _facts({
        ("us-gaap", "Assets", "USD"): [_fact(1000, "2016-01-03", 2015, "2016-02-20", "a1")],
    })
    long = extract_annual_facts(facts, COMPANY, cik=1)
    row = long[long.logical_input == "total_assets"].iloc[0]
    assert row["fiscal_year"] == 2015          # from `fy`, not the 2016 end date
    assert row["value"] == 1000


def test_cover_page_instant_does_not_spawn_phantom_year():
    # dei cover-page shares are dated after FYE but tagged fy=2015 -> must map to 2015,
    # not create a phantom 2016 row with only `shares`.
    facts = _facts({
        ("us-gaap", "Assets", "USD"): [_fact(1000, "2015-12-31", 2015, "2016-02-20", "a1")],
        ("dei", "EntityCommonStockSharesOutstanding", "shares"):
            [_fact(500, "2016-02-15", 2015, "2016-02-20", "a1")],
    })
    long = extract_annual_facts(facts, COMPANY, cik=1)
    assert set(long["fiscal_year"]) == {2015}


def test_tag_priority_prefers_higher_priority_revenue_tag():
    # Both the ASC-606 tag and a legacy tag report FY2020; the higher-priority one wins.
    facts = _facts({
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"):
            [_fact(900, "2020-12-31", 2020, "2021-02-10", "a1", start="2020-01-01")],
        ("us-gaap", "SalesRevenueNet", "USD"):
            [_fact(950, "2020-12-31", 2020, "2021-02-10", "a1", start="2020-01-01")],
    })
    long = extract_annual_facts(facts, COMPANY, cik=1)
    rev = long[long.logical_input == "revenue"].iloc[0]
    assert rev["tag_used"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert rev["value"] == 900


def test_originally_reported_wins_over_later_comparative_and_amendment():
    # Same period reported thrice: original 10-K, a restated comparative (fy2021), and a
    # 10-K/A. Phase 1 must keep the earliest-filed original value.
    facts = _facts({
        ("us-gaap", "Assets", "USD"): [
            _fact(1000, "2020-12-31", 2020, "2021-02-10", "orig"),           # original
            _fact(1050, "2020-12-31", 2021, "2022-02-10", "compar"),         # later restatement
            _fact(1010, "2020-12-31", 2020, "2021-05-01", "amend", form="10-K/A"),
        ],
    })
    long = extract_annual_facts(facts, COMPANY, cik=1)
    row = long[(long.logical_input == "total_assets") & (long.fiscal_year == 2020)].iloc[0]
    assert row["value"] == 1000
    assert row["filed"] == "2021-02-10"


def test_quarterly_duration_is_filtered_out():
    # A ~quarter-long duration tagged fp=FY must not be treated as the annual figure.
    facts = _facts({
        ("us-gaap", "Revenues", "USD"): [
            _fact(250, "2020-03-31", 2020, "2021-02-10", "a1", start="2020-01-01"),  # ~90d
            _fact(1000, "2020-12-31", 2020, "2021-02-10", "a1", start="2020-01-01"), # ~365d
        ],
    })
    long = extract_annual_facts(facts, COMPANY, cik=1)
    rev = long[long.logical_input == "revenue"]
    assert list(rev["value"]) == [1000]


def test_missing_concept_is_absent_not_fabricated():
    facts = _facts({
        ("us-gaap", "Assets", "USD"): [_fact(1000, "2020-12-31", 2020, "2021-02-10", "a1")],
    })
    long = extract_annual_facts(facts, COMPANY, cik=1)
    assert "revenue" not in set(long["logical_input"])  # no revenue tag -> simply absent


def test_build_fundamentals_filing_date_is_latest_input():
    facts = _facts({
        ("us-gaap", "Assets", "USD"): [_fact(1000, "2020-12-31", 2020, "2021-02-10", "a1")],
        ("us-gaap", "NetIncomeLoss", "USD"):
            [_fact(100, "2020-12-31", 2020, "2021-02-25", "a1", start="2020-01-01")],
    })
    wide = build_fundamentals(extract_annual_facts(facts, COMPANY, cik=1))
    assert wide.loc[0, "filing_date"] == "2021-02-25"   # max over the year's inputs
