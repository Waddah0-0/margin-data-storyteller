"""Test fixtures: the messy-data suite, small files with one problem each, built per session.

The first sixteen come from StoryBI's behaviour matrix; the rest cover cases the redesigned
cleaner handles. Files are written byte for byte here (encodings, line endings, BOMs)
rather than committed, so git's line-ending rules can never change them.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
import pytest


def _write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.write_bytes(text.replace("\n", "\r\n").encode(encoding))
    return path


def _rows(*rows: str) -> str:
    return "\n".join(rows) + "\n"


def build(folder: Path) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}

    def add(name: str, text: str, encoding: str = "utf-8") -> None:
        files[name.rsplit(".", 1)[0]] = _write(folder / name, text, encoding)

    # ---- StoryBI's matrix ----
    add(
        "percentages.csv",
        _rows(
            "item,discount,revenue",
            "Alpha,12.5%,1000",
            "Beta,0.125,1500",
            "Gamma,15.0%,2000",
            "Delta,0.08,2500",
        ),
    )
    add(
        "currency_symbols.csv",
        _rows("region,amount,units", 'US,"$1,240.50",10', 'EU,"€ 3.400,00",20', "Unknown,1.234,30"),
    )
    files["utf8_bom"] = folder / "utf8_bom.csv"
    files["utf8_bom"].write_bytes(
        b"\xef\xbb\xbf" + _rows("id,product,price", "1,Widget,19.99", "2,Gadget,29.99").encode()
    )
    add(
        "latin1.csv",
        _rows("id,city,metric", "1,München,100", "2,Zürich,200", "3,São Paulo,300"),
        "latin-1",
    )
    add("windows1252.csv", _rows("id,quote,score", "1,“Smart Quotes”,95", "2,En–Dash,88"), "cp1252")
    add(
        "mixed_dates.csv",
        _rows(
            "order_id,date_str,sales",
            "1,2024-01-15,100",
            "2,31/01/2024,200",
            "3,14/02/2024,150",
            "4,05/03/2024,300",
        ),
    )
    add(
        "ambiguous_dates.csv",
        _rows(
            "order_id,date_str,sales", "1,01/02/2024,120", "2,03/04/2024,220", "3,05/06/2024,320"
        ),
    )
    add("duplicate_headers.csv", _rows("id,Sales,Sales,Profit", "1,100,200,50", "2,150,250,75"))
    add(
        "duplicate_rows.csv",
        _rows("id,category,revenue", "1,A,100", "2,B,200", "2,B,200", "3,C,300"),
    )
    add(
        "all_null_column.csv",
        _rows("id,valid_metric,empty_column", "1,10.5,", "2,20.0,", "3,30.5,"),
    )
    add("single_row.csv", _rows("metric_a,category,status", "5000,SoleEntity,Active"))
    add(
        "no_date_data.csv",
        _rows(
            "department,headcount,budget",
            "Engineering,120,2400000",
            "Sales,85,1700000",
            "Support,45,900000",
            "Marketing,30,600000",
        ),
    )
    add(
        "no_numeric_data.csv",
        _rows(
            "respondent_id,feedback_category,sentiment",
            "R1,UI,Positive",
            "R2,Performance,Negative",
            "R3,UI,Neutral",
            "R4,Pricing,Negative",
            "R5,Support,Positive",
        ),
    )
    header = ["entity_id"] + [f"feature_{i:03d}" for i in range(1, 305)]
    wide = [",".join(header)]
    for r in range(20):
        wide.append(",".join([f"E{r:03d}"] + [str((r * 7 + c * 13) % 97 + c) for c in range(304)]))
    add("wide_dataset.csv", _rows(*wide))
    add(
        "negative_values.csv",
        _rows(
            "quarter,net_income,cash_flow",
            "2023-Q1,120000.0,90000.0",
            "2023-Q2,-45000.0,-20000.0",
            "2023-Q3,-15000.0,5000.0",
            "2023-Q4,85000.0,60000.0",
        ),
    )
    files["excel_merged_headers"] = _workbook(
        folder / "excel_merged_headers.xlsx",
        "Summary",
        [
            ["Financial Report - Confidential", None, None],
            ["Fiscal Year 2024", None, None],
            ["Division", "Q1 Revenue", "Q2 Revenue"],
            ["North America", 100000, 120000],
            ["Europe", 85000, 92000],
            ["Asia Pacific", 64000, 78000],
        ],
    )

    # ---- cases the redesign adds ----
    files["two_row_header"] = _workbook(
        folder / "two_row_header.xlsx",
        "Sales",
        [
            [None, "Revenue", None, "Cost", None],
            ["Region", "Q1", "Q2", "Q1", "Q2"],
            ["North", 100, 110, 60, 65],
            ["South", 90, 95, 50, 52],
            ["East", 120, 130, 70, 72],
        ],
    )
    add(
        "total_row.csv",
        _rows("region,sales,units", "North,100,10", "South,250,25", "East,150,15", "Total,500,50"),
    )
    add("unlabelled_total.csv", _rows("region,sales", "North,100", "South,250", "East,150", ",500"))
    add("zip_codes.csv", _rows("customer,zip,spend", "A,02134,10", "B,10001,20", "C,07030,30"))
    add(
        "arabic_digits.csv",
        _rows(
            "المدينة,المبيعات,التاريخ",
            "الرياض,١٢٣٤,٢٠٢٤-٠١-١٥",
            "جدة,٥٦٧٫٥,٢٠٢٤-٠٢-٢٠",
            "الدمام,٨٩,٢٠٢٤-٠٣-١٠",
        ),
    )
    add(
        "spellings.csv",
        _rows(
            "id,region,sales",
            "1,North,10",
            "2,north,20",
            "3,NORTH ,30",
            "4,South,40",
            "5,South,50",
            "6,East,60",
            "7,North,70",
            "8,South,80",
        ),
    )
    add(
        "markers.csv",
        _rows(
            "id,score,checked_on",
            "1,N/A,2024-01-01",
            "2,#DIV/0!,n/a",
            "3,42,2024-01-03",
            "4,-,2024-01-04",
            "5,7,NA",
        ),
    )
    add(
        "mostly_numbers.csv",
        _rows("id,amount", *[f"{i},{i * 10}" for i in range(1, 9)], "9,unknown", "10,see note"),
    )
    add(
        "semicolon_eu.csv",
        _rows(
            "Produkt;Preis;Datum",
            "Tisch;1.234,50;31.01.2024",
            "Stuhl;89,90;15.02.2024",
            "Lampe;12,00;01.03.2024",
        ),
    )
    add("no_header.csv", _rows("100,2024-01-01,4.5", "200,2024-01-02,3.5", "150,2024-01-03,4.0"))
    add("duplicates_no_id.csv", _rows("product,qty", "Pen,2", "Pen,2", "Book,1"))
    return files


def _workbook(path: Path, sheet: str, rows: list[list[object]]) -> Path:
    workbook = openpyxl.Workbook()
    ws = workbook.active
    assert ws is not None
    ws.title = sheet
    for row in rows:
        ws.append(row)
    workbook.save(path)
    return path


@pytest.fixture(scope="session")
def messy(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return build(tmp_path_factory.mktemp("messy"))


# ---- golden datasets with planted truths ------------------------------------------------
# Ported unchanged from StoryBI (same seeds, so the same data): the facts engine must find
# every planted truth, and must not report the planted non-relationship.


def generate_golden_ecommerce(output_path: Path, seed: int = 42) -> Path:
    """Golden 1: E-commerce Performance.

    Planted truths:
    1. Technology revenue grows by ~+20% between Q1 and Q3.
    2. Furniture revenue declines by ~-15% between Q1 and Q3.
    3. Planted Q3 anomaly: West region sales spike sharply (3x normal) in late August.
    4. Planted 80/20 Pareto distribution: top ~20% of customers generate ~80% of revenue.
    5. Incomplete last month: October 2024 only has 10 days of records (partial month).
    """
    rng = np.random.default_rng(seed)
    n_rows = 2400

    # Date range: 2024-01-01 through 2024-10-10 (October has only 10 days)
    full_dates = pd.date_range("2024-01-01", "2024-09-30", freq="D")
    oct_dates = pd.date_range("2024-10-01", "2024-10-10", freq="D")

    # 2200 rows spread across Jan-Sep, 200 rows across Oct 1-10
    dates_jan_sep = rng.choice(full_dates, size=2200)
    dates_oct = rng.choice(oct_dates, size=200)
    order_dates = np.concatenate([dates_jan_sep, dates_oct])
    rng.shuffle(order_dates)

    # Customers with Pareto distribution (power law weights)
    n_customers = 200
    customer_ids = [f"CUST-{i:04d}" for i in range(1, n_customers + 1)]
    # Pareto power-law alpha ~ 1.15 produces ~80% share for top 20%
    customer_weights = 1.0 / (np.arange(1, n_customers + 1) ** 1.18)
    customer_weights /= customer_weights.sum()
    assigned_customers = rng.choice(customer_ids, size=n_rows, p=customer_weights)

    categories = ["Technology", "Furniture", "Office Supplies"]
    regions = ["North", "South", "East", "West"]

    records = []
    for i in range(n_rows):
        od = pd.Timestamp(order_dates[i])
        cat = rng.choice(categories, p=[0.4, 0.35, 0.25])
        region = rng.choice(regions)

        # Baseline base price
        if cat == "Technology":
            # Growth over year: Q1 base 200 -> Q3 base 245 (+22%)
            quarter = od.quarter
            growth_factor = 1.0 + (quarter - 1) * 0.10
            sales = rng.normal(200 * growth_factor, 25)
        elif cat == "Furniture":
            # Decline over year: Q1 base 180 -> Q3 base 150 (-17%)
            quarter = od.quarter
            decline_factor = 1.0 - (quarter - 1) * 0.08
            sales = rng.normal(180 * decline_factor, 20)
        else:
            sales = rng.normal(80, 15)

        # Planted anomaly: West region in late August 2024 (2024-08-20 to 2024-08-28)
        if region == "West" and pd.Timestamp("2024-08-20") <= od <= pd.Timestamp("2024-08-28"):
            sales *= 3.8

        quantity = int(rng.integers(1, 6))
        profit = round(sales * rng.uniform(0.12, 0.28), 2)
        sales = round(max(10.0, sales), 2)

        records.append(
            {
                "order_id": f"ORD-{i + 1:05d}",
                "order_date": od.strftime("%Y-%m-%d"),
                "customer_id": assigned_customers[i],
                "category": cat,
                "region": region,
                "sales": sales,
                "quantity": quantity,
                "profit": profit,
            }
        )

    df = pd.DataFrame(records).sort_values("order_date").reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    return output_path


def generate_golden_saas(output_path: Path, seed: int = 42) -> Path:
    """Golden 2: SaaS Retention & Simpson's Paradox.

    Planted truths:
    1. Strong negative correlation between discount_rate and retention_months (r < -0.6).
    2. Non-relationship between company_size (headcount) and churn_rate (|r| < 0.10, p > 0.05).
    3. Simpson's paradox: Churn drops within every tier (Small, Medium, Enterprise) over the
       2 cohorts, but overall aggregate churn increases due to a composition shift toward
       Small clients.
    """
    rng = np.random.default_rng(seed)
    n_accounts = 2400

    records = []
    # Two cohorts: Cohort 2023 vs Cohort 2024
    for i in range(n_accounts):
        account_id = f"ACC-{i + 1:05d}"
        cohort = rng.choice(["2023", "2024"])

        # In 2023: 15% Small, 35% Medium, 50% Enterprise
        # In 2024: 65% Small, 20% Medium, 15% Enterprise (composition shift!)
        if cohort == "2023":
            tier = rng.choice(["Small", "Medium", "Enterprise"], p=[0.15, 0.35, 0.50])
        else:
            tier = rng.choice(["Small", "Medium", "Enterprise"], p=[0.65, 0.20, 0.15])

        # Headcount is independent of churn (non-relationship)
        company_size = int(rng.exponential(scale=150) + 10)

        # Discount rate (0.0 to 0.40)
        discount_rate = round(float(rng.uniform(0.0, 0.40)), 3)

        # Retention months negatively correlated with discount rate
        retention_months = round(max(1.0, 36.0 - (discount_rate * 45.0) + rng.normal(0, 3)), 1)

        # Churn probability inside tiers drops from 2023 to 2024:
        # Small: 2023 -> 25%, 2024 -> 18% (drops!)
        # Medium: 2023 -> 15%, 2024 -> 9% (drops!)
        # Enterprise: 2023 -> 8%, 2024 -> 3% (drops!)
        if tier == "Small":
            base_p = 0.25 if cohort == "2023" else 0.18
            mrr = round(float(rng.normal(250, 40)), 2)
        elif tier == "Medium":
            base_p = 0.15 if cohort == "2023" else 0.09
            mrr = round(float(rng.normal(1200, 150)), 2)
        else:
            base_p = 0.08 if cohort == "2023" else 0.03
            mrr = round(float(rng.normal(4500, 500)), 2)

        churned = int(rng.uniform() < base_p)

        records.append(
            {
                "account_id": account_id,
                "cohort": cohort,
                "tier": tier,
                "company_size": company_size,
                "discount_rate": discount_rate,
                "retention_months": retention_months,
                "mrr": mrr,
                "churned": churned,
            }
        )

    df = pd.DataFrame(records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    return output_path


def generate_golden_logistics(output_path: Path, seed: int = 42) -> Path:
    """Golden 3: Operational Logistics & Bottleneck.

    Planted truths:
    1. Carrier 'ApexExpress' has ~3.5x higher defect/damage rate than other carriers.
    2. Seasonal December volume surge (~2.5x normal monthly volume).
    """
    rng = np.random.default_rng(seed)
    n_shipments = 2000

    dates = pd.date_range("2024-01-01", "2024-12-31", freq="D")
    carriers = ["FleetStar", "SwiftCargo", "ApexExpress"]

    records = []
    # Bias dates toward December
    month_weights = np.array([1.0] * 11 + [2.8])
    month_weights /= month_weights.sum()

    for i in range(n_shipments):
        shipment_id = f"SHP-{i + 1:05d}"
        chosen_month = rng.choice(np.arange(1, 13), p=month_weights)
        month_dates = [d for d in dates if d.month == chosen_month]
        ship_date = rng.choice(month_dates)

        carrier = rng.choice(carriers, p=[0.40, 0.35, 0.25])
        weight_kg = round(float(rng.uniform(1.5, 45.0)), 2)
        freight_cost = round(15.0 + weight_kg * rng.uniform(1.2, 2.5), 2)

        # Defect rate: ApexExpress ~ 16%, others ~ 4% (~4x higher)
        defect_p = 0.16 if carrier == "ApexExpress" else 0.04
        is_damaged = int(rng.uniform() < defect_p)
        transit_days = int(rng.integers(1, 7) + (3 if is_damaged else 0))

        records.append(
            {
                "shipment_id": shipment_id,
                "ship_date": pd.Timestamp(ship_date).strftime("%Y-%m-%d"),
                "carrier": carrier,
                "weight_kg": weight_kg,
                "freight_cost": freight_cost,
                "transit_days": transit_days,
                "is_damaged": is_damaged,
            }
        )

    df = pd.DataFrame(records).sort_values("ship_date").reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    return output_path


@pytest.fixture(scope="session")
def golden(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    folder = tmp_path_factory.mktemp("golden")
    return {
        "ecommerce": generate_golden_ecommerce(folder / "golden_ecommerce.csv"),
        "saas": generate_golden_saas(folder / "golden_saas.csv"),
        "logistics": generate_golden_logistics(folder / "golden_logistics.csv"),
    }
