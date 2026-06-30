"""
Phase 1 configuration: universe, SEC headers, XBRL tag mappings, score constants.

This module is intentionally declarative. Everything that a maintainer might want to
tweak (which companies, which XBRL tags map to which textbook line item, the Beneish
coefficients) lives here so the data-fetch / extraction / scoring code stays generic.

THE LANDMINE (read this before trusting any score)
---------------------------------------------------
XBRL `companyfacts` concepts do NOT map one-to-one to the textbook line items the
Piotroski and Beneish formulas assume. Tags are renamed across filers and across years
(e.g. the ASC 606 revenue-tag change in 2018), and some textbook items (e.g. total
liabilities) are frequently not tagged at all. CONCEPT_MAP below encodes, for each
LOGICAL input, an ORDERED list of candidate concept tags. The extractor tries them in
order and records which one it actually used. If none is found, the input is left
missing and FLAGGED -- we never silently substitute a wrong tag, because a mis-mapped
tag produces a plausible-but-wrong score.
"""

# ---------------------------------------------------------------------------
# SEC EDGAR access
# ---------------------------------------------------------------------------
# SEC requires a declared User-Agent with a real contact. Edit this if reused.
USER_AGENT = "accounting-redflag-screen annanyashah9@gmail.com"

# SEC fair-access guidance: stay at/under 10 requests/second.
SEC_MAX_REQUESTS_PER_SECOND = 8

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"

# XBRL data is only reliably available for fiscal years ending ~2009 onward (the XBRL
# mandate was phased in 2009-2011). Pre-2009 frauds (Enron 2001, WorldCom 2002) have NO
# companyfacts data and cannot be screened -- every problem case below is post-2009.
MIN_FISCAL_YEAR = 2009

# ---------------------------------------------------------------------------
# Universe (~30): known post-2009 accounting problem cases + non-financial controls.
# `is_known_case` marks names used later (Phase 4) for validation. Beneish excludes
# financials/utilities, so controls are deliberately non-financial.
# Live companies resolve ticker->CIK via company_tickers.json. DELISTED / RENAMED /
# TICKER-REUSED names cannot be resolved that way (the SEC ticker file is current-only),
# so problem cases carry an explicit, permanent `cik` (verified against companyfacts
# entityName). This is itself a documented survivorship/identity trap: resolving the
# problem cases by their old ticker would silently drop NKLA/CLDN and mis-resolve SUNE
# to an unrelated company ("SUNATION ENERGY") that reused the ticker.
# `expect_entity` (substring) is checked at fetch time so identity mismatches are FLAGGED.
# ---------------------------------------------------------------------------
UNIVERSE = [
    # --- Known accounting problem cases (post-2009; CIK-pinned) ---
    {"ticker": "BHC",  "cik": 885590,  "expect_entity": "Bausch",
     "name": "Bausch Health (fka Valeant)", "is_known_case": True,
     "note": "Philidor/revenue-recognition scandal ~2015-2016"},
    {"ticker": "HTZ",  "cik": 1364479, "expect_entity": "HERC",
     "name": "Hertz Global Holdings (pre-2016-split entity)", "is_known_case": True,
     "note": "2014-2015 restatement era lives here; entity renamed Herc Holdings after "
             "the 2016 car/equipment split, so post-2016 figures are Herc, not Hertz"},
    {"ticker": "UAA",  "cik": 1336917, "expect_entity": "UNDER ARMOUR",
     "name": "Under Armour", "is_known_case": True,
     "note": "SEC probe into revenue pull-forward 2015-2017"},
    {"ticker": "GE",   "name": "General Electric", "is_known_case": True,
     "note": "2017-2018 accounting/insurance-reserve concerns; SEC probe"},
    {"ticker": "KHC",  "name": "Kraft Heinz", "is_known_case": True,
     "note": "2019 SEC subpoena, procurement accounting, impairment"},
    {"ticker": "NKLA", "cik": 1731289, "expect_entity": "Nikola",
     "name": "Nikola", "is_known_case": True,
     "note": "2020 fraud allegations / SEC settlement (delisted; not in current ticker file)"},
    {"ticker": "MDXG", "cik": 1376339, "expect_entity": "MIMEDX",
     "name": "MiMedx Group", "is_known_case": True,
     "note": "Channel-stuffing / revenue recognition ~2018"},
    {"ticker": "MAT",  "name": "Mattel", "is_known_case": True,
     "note": "2019 accounting errors / restatement"},
    {"ticker": "SUNE", "cik": 945436,  "expect_entity": "SUNEDISON",
     "name": "SunEdison", "is_known_case": True,
     "note": "2016 collapse (FY2009-2014 data). NOTE: ticker 'SUNE' was later reused by "
             "an unrelated company, so ticker resolution would mis-identify it"},
    {"ticker": "CLDN", "cik": 865941,  "expect_entity": "CELADON",
     "name": "Celadon Group", "is_known_case": True,
     "note": "Trucking accounting fraud ~2016-2017 (delisted; not in current ticker file)"},

    # --- Ordinary controls (non-financial large caps) ---
    {"ticker": "AAPL", "name": "Apple",            "is_known_case": False, "note": ""},
    {"ticker": "MSFT", "name": "Microsoft",        "is_known_case": False, "note": ""},
    {"ticker": "JNJ",  "name": "Johnson & Johnson", "is_known_case": False, "note": ""},
    {"ticker": "PG",   "name": "Procter & Gamble", "is_known_case": False, "note": ""},
    {"ticker": "KO",   "name": "Coca-Cola",        "is_known_case": False, "note": ""},
    {"ticker": "PEP",  "name": "PepsiCo",          "is_known_case": False, "note": ""},
    {"ticker": "WMT",  "name": "Walmart",          "is_known_case": False, "note": ""},
    {"ticker": "COST", "name": "Costco",           "is_known_case": False, "note": ""},
    {"ticker": "HD",   "name": "Home Depot",       "is_known_case": False, "note": ""},
    {"ticker": "MCD",  "name": "McDonald's",       "is_known_case": False, "note": ""},
    {"ticker": "INTC", "name": "Intel",            "is_known_case": False, "note": ""},
    {"ticker": "CSCO", "name": "Cisco",            "is_known_case": False, "note": ""},
    {"ticker": "NKE",  "name": "Nike",             "is_known_case": False, "note": ""},
    {"ticker": "DIS",  "name": "Walt Disney",      "is_known_case": False, "note": ""},
    {"ticker": "CAT",  "name": "Caterpillar",      "is_known_case": False, "note": ""},
    {"ticker": "MMM",  "name": "3M",               "is_known_case": False, "note": ""},
    {"ticker": "TXN",  "name": "Texas Instruments", "is_known_case": False, "note": ""},
    {"ticker": "ORCL", "name": "Oracle",           "is_known_case": False, "note": ""},
    {"ticker": "PFE",  "name": "Pfizer",           "is_known_case": False, "note": ""},
    {"ticker": "MRK",  "name": "Merck",            "is_known_case": False, "note": ""},
]

# ---------------------------------------------------------------------------
# CONCEPT_MAP: logical input -> ordered candidate XBRL concept tags.
# The extractor tries each in order and records the tag actually used.
# `taxonomy` is the companyfacts top-level key the tag lives under.
# `kind` is "duration" (income/cash-flow flow item -> needs a full fiscal year) or
# "instant" (balance-sheet stock item -> a period-end snapshot).
# ---------------------------------------------------------------------------
CONCEPT_MAP = {
    # ---- Income statement (flow) ----
    "net_income": {
        "kind": "duration", "taxonomy": "us-gaap",
        "tags": ["NetIncomeLoss", "ProfitLoss"],
    },
    "income_continuing": {  # Beneish TATA numerator; NetIncomeLoss/ProfitLoss are
        # documented proxies (some filers, e.g. Caterpillar, tag only ProfitLoss).
        "kind": "duration", "taxonomy": "us-gaap",
        "tags": ["IncomeLossFromContinuingOperations", "NetIncomeLoss", "ProfitLoss"],
    },
    "revenue": {
        "kind": "duration", "taxonomy": "us-gaap",
        # ASC 606 (2018) renamed the primary revenue tag; older filings use a variety of
        # tags (e.g. JNJ used SalesRevenueGoodsNet through ~2017). We try the post-606
        # tags first, then the legacy ones. NOTE: switching tags across years (or
        # including- vs excluding-assessed-tax) can introduce small YoY discontinuities
        # in ratio-based signals -- input_coverage.csv records the tag used per year.
        "tags": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                 "RevenueFromContractWithCustomerIncludingAssessedTax",
                 "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet",
                 "SalesRevenueServicesNet"],
    },
    "cogs": {
        "kind": "duration", "taxonomy": "us-gaap",
        "tags": ["CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfRevenue"],
    },
    "gross_profit": {
        # Non-fabricating fallback for gross margin: where a firm reports GrossProfit
        # but no clean COGS line (common for services/diversified filers), gross margin
        # = GrossProfit / Revenue. Firms reporting NEITHER (e.g. McDonald's, Disney --
        # only CostsAndExpenses) keep an undefined, FLAGGED gross margin.
        "kind": "duration", "taxonomy": "us-gaap", "tags": ["GrossProfit"],
    },
    "sga": {
        "kind": "duration", "taxonomy": "us-gaap",
        "tags": ["SellingGeneralAndAdministrativeExpense",
                 "GeneralAndAdministrativeExpense"],
    },
    "depreciation": {
        "kind": "duration", "taxonomy": "us-gaap",
        # NOTE: cash-flow D&A includes amortization; IS depreciation is often buried in
        # COGS and not separately tagged. This is a known Beneish-from-XBRL imprecision.
        "tags": ["DepreciationDepletionAndAmortization", "Depreciation",
                 "DepreciationAmortizationAndAccretionNet"],
    },
    # ---- Cash flow (flow) ----
    "cfo": {
        "kind": "duration", "taxonomy": "us-gaap",
        "tags": ["NetCashProvidedByUsedInOperatingActivities",
                 "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    },
    # ---- Balance sheet (instant) ----
    "total_assets": {
        "kind": "instant", "taxonomy": "us-gaap", "tags": ["Assets"],
    },
    "current_assets": {
        "kind": "instant", "taxonomy": "us-gaap", "tags": ["AssetsCurrent"],
    },
    "current_liabilities": {
        "kind": "instant", "taxonomy": "us-gaap", "tags": ["LiabilitiesCurrent"],
    },
    "long_term_debt": {
        "kind": "instant", "taxonomy": "us-gaap",
        "tags": ["LongTermDebtNoncurrent", "LongTermDebt",
                 "LongTermDebtAndCapitalLeaseObligations"],
    },
    "receivables": {
        "kind": "instant", "taxonomy": "us-gaap",
        # Some filers (e.g. PepsiCo) tag the combined accounts/notes/loans line.
        "tags": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent",
                 "AccountsNotesAndLoansReceivableNetCurrent"],
    },
    "ppe_net": {
        "kind": "instant", "taxonomy": "us-gaap",
        # Post-ASC 842, some filers (KHC, INTC) fold finance-lease ROU assets into a
        # combined net-PP&E tag. Slightly broader than pure PP&E -- documented imprecision.
        "tags": ["PropertyPlantAndEquipmentNet",
                 "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"],
    },
    "shares": {
        # Weighted-average basic is the most comparable "did they issue stock?" measure;
        # fall back to point-in-time share counts (dei / common stock).
        "kind": "duration", "taxonomy": "us-gaap",
        "tags": ["WeightedAverageNumberOfSharesOutstandingBasic"],
        "fallbacks": [
            {"taxonomy": "dei", "kind": "instant",
             "tags": ["EntityCommonStockSharesOutstanding"]},
            {"taxonomy": "us-gaap", "kind": "instant",
             "tags": ["CommonStockSharesOutstanding"]},
        ],
    },
}

# Logical inputs each score depends on (used to compute per-row availability flags).
# Gross margin is tracked separately (see scores.py) since it can come from either COGS
# or GrossProfit; the "gross_margin" availability token is reported instead of raw cogs.
PIOTROSKI_INPUTS = [
    "net_income", "total_assets", "cfo", "long_term_debt", "current_assets",
    "current_liabilities", "shares", "revenue",
]
BENEISH_INPUTS = [
    "receivables", "revenue", "current_assets", "ppe_net", "total_assets",
    "depreciation", "sga", "income_continuing", "cfo", "current_liabilities",
    "long_term_debt",
]

# ---------------------------------------------------------------------------
# Beneish M-Score: 8-variable model coefficients and flag threshold.
# Manipulation index from Beneish (Financial Analysts Journal, 1999).
# ---------------------------------------------------------------------------
BENEISH_COEF = {
    "intercept": -4.84,
    "DSRI": 0.920, "GMI": 0.528, "AQI": 0.404, "SGI": 0.892,
    "DEPI": 0.115, "SGAI": -0.172, "TATA": 4.679, "LVGI": -0.327,
}
# M > threshold flags a "manipulation-like" financial profile (NOT a fraud conviction).
BENEISH_FLAG_THRESHOLD = -1.78

# Piotroski: total ranges 0-9; conventionally >=8 is "strong", <=2 is "weak".
PIOTROSKI_STRONG = 8
PIOTROSKI_WEAK = 2

# ---------------------------------------------------------------------------
# Design-scope limitations -- stated honestly in output headers. These scores were
# built for specific universes/eras; applying them broadly is out-of-design use.
# ---------------------------------------------------------------------------
SCOPE_NOTES = {
    "piotroski": (
        "Piotroski F-Score (Piotroski, J. Accounting Research, 2000): designed to "
        "separate winners from losers among HIGH book-to-market (value), often small / "
        "financially-distressed firms. Binary aggregation discards magnitude. Not "
        "validated on growth/glamour or mega-cap names; broad application is "
        "out-of-design use."
    ),
    "beneish": (
        "Beneish M-Score (Beneish, Financial Analysts Journal, 1999): probit model "
        "estimated on 1982-1992 MANUFACTURING firms (74 manipulators). EXCLUDES "
        "financials, utilities, and REITs. Known HIGH false-positive rate; M flags a "
        "'manipulation-like' financial profile, not fraud. Era-specific coefficients; "
        "ASC 606 revenue and depreciation tagging add noise."
    ),
}
