"""Reproduce the Super Fit portfolio figures from the supplied synthetic workbook."""

from pathlib import Path
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "Super_Fit_Gym_Synthetic_Dataset.xlsx"
REPORTS = ROOT / "reports"
IMAGES = ROOT / "images"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(tables):
    """Validate selected entity keys, core joins and cash reconciliations."""
    keys = {
        "Branches": "Branch_ID", "Plans": "Plan_ID",
        "Employees": "Employee_ID", "Members": "Member_ID",
        "Leads": "Lead_ID", "Memberships": "Membership_ID",
        "Payment_Schedule": "Installment_ID", "Payments": "Payment_ID",
        "Attendance": "Attendance_ID",
    }
    checks = []
    for table, key in keys.items():
        values = tables[table][key]
        missing = int((values.isna() | values.astype(str).str.strip().eq("")).sum())
        duplicates = int(values.duplicated().sum())
        checks.extend([
            {"check": f"{table}: missing primary keys", "failures": missing},
            {"check": f"{table}: duplicate primary keys", "failures": duplicates},
        ])
    relationships = [
        ("Memberships", "Member_ID", "Members", "Member_ID"),
        ("Memberships", "Branch_ID", "Branches", "Branch_ID"),
        ("Memberships", "Plan_ID", "Plans", "Plan_ID"),
        ("Payments", "Membership_ID", "Memberships", "Membership_ID"),
        ("Attendance", "Member_ID", "Members", "Member_ID"),
        ("Attendance", "Membership_ID", "Memberships", "Membership_ID"),
        ("Payment_Schedule", "Membership_ID", "Memberships", "Membership_ID"),
    ]
    for child, key, parent, parent_key in relationships:
        count = int((~tables[child][key].isin(tables[parent][parent_key])).sum())
        checks.append({"check": f"{child}.{key} -> {parent}.{parent_key}", "failures": count})
    s = tables["Memberships"]
    cash = tables["Payments"].groupby("Membership_ID")["Amount_EGP"].sum()
    # Aggregate transactions first: avoid multiplying payments by contract/visit rows.
    cash = s["Membership_ID"].map(cash).fillna(0)
    checks.append({"check": "Net cash matches signed payments for every contract",
                   "failures": int((abs(cash - s["Net_Cash_Collected_EGP"]) > 0.01).sum())})
    checks.append({"check": "Adjusted value = net cash + outstanding for every contract",
                   "failures": int((abs(s["Adjusted_Value_EGP"] - s["Net_Cash_Collected_EGP"] - s["Outstanding_EGP"]) > 0.01).sum())})
    checks.append({"check": "Overdue between zero and outstanding for every contract",
                   "failures": int(((s["Overdue_EGP"] < 0) | (s["Overdue_EGP"] > s["Outstanding_EGP"])).sum())})
    report = pd.DataFrame(checks)
    report.to_csv(REPORTS / "validation_checks.csv", index=False)
    require(not report["failures"].any(), "Data checks failed; review reports/validation_checks.csv")
    return len(checks)


def bar_chart(series, filename, xlabel, percent=False):
    series = series.sort_values()
    fig, ax = plt.subplots(figsize=(10, 4.6))
    scale = 100 if percent else 1 / 1_000_000
    ax.barh(series.index, series.values * scale,
            color=["#67bdcc"] * (len(series) - 1) + ["#126e9d"])
    for i, value in enumerate(series):
        label = f"{value * 100:.1f}%" if percent else f"EGP {value / 1_000_000:.2f}m"
        ax.text(value * scale + (0.8 if percent else 0.05), i, label, va="center", fontsize=10)
    ax.set_xlim(0, series.max() * scale * 1.25)
    ax.set_xlabel(xlabel)
    ax.grid(axis="x", alpha=0.15)
    ax.set_axisbelow(True)
    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)
    fig.tight_layout()
    fig.savefig(IMAGES / filename, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main():
    REPORTS.mkdir(exist_ok=True)
    IMAGES.mkdir(exist_ok=True)
    tables = pd.read_excel(SOURCE, sheet_name=None)
    checks = validate(tables)
    s, p, leads = tables["Memberships"], tables["Payments"], tables["Leads"]
    require(set(p["Entry_Type"]) <= {"Collection", "Refund"}, "Unexpected payment type")
    require((p.loc[p.Entry_Type == "Refund", "Amount_EGP"] <= 0).all(), "Refund signs changed")
    require((p.loc[p.Entry_Type == "Collection", "Amount_EGP"] >= 0).all(), "Collection signs changed")
    cash = float(p["Amount_EGP"].sum())
    adjusted = float(s["Adjusted_Value_EGP"].sum())
    metrics = {
        "period_start": "2025-10-01", "snapshot_date": "2026-09-30", "currency": "EGP",
        "data_type": "synthetic", "branches": len(tables["Branches"]),
        "members": int(tables["Members"]["Member_ID"].nunique()),
        "memberships": len(s), "leads": len(leads),
        "visits": len(tables["Attendance"]), "payment_transactions": len(p),
        "contract_value_egp": float(s["Contract_Value_EGP"].sum()),
        "adjusted_contract_value_egp": adjusted,
        "collections_egp": float(p.loc[p.Entry_Type == "Collection", "Amount_EGP"].sum()),
        "signed_refunds_egp": float(p.loc[p.Entry_Type == "Refund", "Amount_EGP"].sum()),
        "net_cash_egp": cash,
        "outstanding_egp": float(s["Outstanding_EGP"].sum()),
        "overdue_egp": float(s["Overdue_EGP"].sum()),
        "cash_to_adjusted_value": cash / adjusted if adjusted else None,
        "lead_conversion_rate": float(leads.Stage.eq("Converted").mean()),
        "checks_passed": checks,
    }
    (REPORTS / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    branches = s.groupby("Branch_ID", as_index=False).agg(
        adjusted_value_egp=("Adjusted_Value_EGP", "sum"),
        net_cash_egp=("Net_Cash_Collected_EGP", "sum"),
        overdue_egp=("Overdue_EGP", "sum"), contracts=("Membership_ID", "count"))
    branches = branches.merge(tables["Branches"][["Branch_ID", "Area"]],
                              on="Branch_ID", how="left", validate="one_to_one")
    branches["overdue_to_adjusted_value"] = branches.overdue_egp / branches.adjusted_value_egp
    branches.to_csv(REPORTS / "branch_summary.csv", index=False)
    channels = leads.groupby("Source").agg(
        leads=("Lead_ID", "count"), converted=("Stage", lambda v: v.eq("Converted").sum()))
    channels["conversion_rate"] = channels.converted / channels.leads
    channels.to_csv(REPORTS / "acquisition_summary.csv")
    tables["Data_Dictionary"].to_csv(ROOT / "data" / "data_dictionary.csv", index=False)
    bar_chart(branches.set_index("Area").net_cash_egp, "branch_cash.png", "Net cash collected (EGP millions)")
    bar_chart(channels.conversion_rate, "channel_conversion.png", "Observed lead conversion (%)", percent=True)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
