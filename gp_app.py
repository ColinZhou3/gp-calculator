
import io, math
import pandas as pd
import streamlit as st

st.set_page_config(page_title="GP Calculator (MVP)", layout="wide")

st.title("Gross Profit Calculator — MVP")
st.caption("Upload Sales + Product Rules. Then input unit cost (per kg) and optional direct costs.")

with st.sidebar:
    st.header("Settings")
    gst_included = st.toggle("Sales include GST (15%)?", value=True)
    default_crate_fee = st.number_input("Default crate issue fee ($/crate)", value=1.0, min_value=0.0, step=0.1)
    default_carton_fee = st.number_input("Default carton fee ($/carton)", value=0.0, min_value=0.0, step=0.1)

    st.markdown("---")
    st.subheader("Upload files")
    sales_file = st.file_uploader("Sales CSV (required)", type=["csv"])
    rules_file = st.file_uploader("Product Rules Excel (required)", type=["xlsx"])

def load_rules(xls: bytes):
    x = pd.ExcelFile(xls)
    df_rules = pd.read_excel(x, "ProductRules")
    df_fees = pd.read_excel(x, "PackagingFees")
    return df_rules, df_fees

def normalize_sales(df: pd.DataFrame, df_rules: pd.DataFrame) -> pd.DataFrame:
    merged = df.merge(df_rules, on="sku", how="left", suffixes=("","_rule"))
    if "value" not in merged.columns or merged["value"].isna().all():
        merged["value"] = merged["sales_qty"] * merged["unit_price"]
    merged["sales_value_exGST"] = merged["value"].astype(float) / (1.15 if gst_included else 1.0)

    def to_kg(row):
        unit = str(row["sales_unit"]).lower()
        qty = float(row["sales_qty"])
        if unit == "kg":
            return qty
        else:
            k = float(row.get("kg_per_sell_unit", 0) or 0)
            return qty * k
    merged["sales_qty_kg"] = merged.apply(to_kg, axis=1)

    def package_count(row):
        unit = str(row["sales_unit"]).lower()
        qty = float(row["sales_qty"])
        cap = float(row.get("packaging_capacity_kg", 0) or 0)
        if unit in ("carton","ctn","crate"):
            return qty
        if cap > 0:
            return math.ceil(float(row["sales_qty_kg"]) / cap)
        return 0.0
    merged["packages"] = merged.apply(package_count, axis=1)
    return merged

def resolve_packaging_fee(row, fees_map: dict) -> float:
    if pd.notna(row.get("packaging_fee_per_unit")):
        return float(row["packaging_fee_per_unit"])
    ptype = str(row.get("packaging_type", "")).lower()
    return fees_map.get(ptype, 0.0)

if not sales_file or not rules_file:
    st.info("Upload Sales CSV and Product Rules Excel to start. You can try with the sample files included in this repo.")
    st.stop()

df_sales = pd.read_csv(sales_file)
df_rules, df_fees = load_rules(rules_file)

fees_map = {str(t).lower(): float(v) for t, v in zip(df_fees["packaging_type"], df_fees["default_fee_per_unit"])}
fees_map["crate"] = default_crate_fee
fees_map["carton"] = default_carton_fee

df = normalize_sales(df_sales, df_rules)

st.subheader("Step 1 — Cost inputs (per SKU)")
sku_list = sorted(df["sku"].unique().tolist())
cost_input = pd.DataFrame({
    "sku": sku_list,
    "unit_cost_per_kg": [0.0]*len(sku_list),
    "freight_total": [0.0]*len(sku_list),
    "labour_total": [0.0]*len(sku_list),
    "operation_total": [0.0]*len(sku_list),
    "other_direct_costs": [0.0]*len(sku_list),
})
cost_input = st.data_editor(cost_input, num_rows="dynamic", use_container_width=True, key="cost_input")

df["packaging_fee_per_unit_final"] = df.apply(lambda r: resolve_packaging_fee(r, fees_map), axis=1)
df["packaging_cost"] = df["packages"] * df["packaging_fee_per_unit_final"]

df_cost_map = cost_input.set_index("sku")
df["unit_cost_per_kg"] = df["sku"].map(df_cost_map["unit_cost_per_kg"]).fillna(0.0)
df["COGS_core"] = df["sales_qty_kg"] * df["unit_cost_per_kg"]

agg = df.groupby(["sku","product_name"], as_index=False).agg({
    "sales_value_exGST":"sum",
    "sales_qty_kg":"sum",
    "packages":"sum",
    "packaging_cost":"sum",
    "COGS_core":"sum"
})

for col in ["freight_total","labour_total","operation_total","other_direct_costs"]:
    agg[col] = agg["sku"].map(df_cost_map[col]).fillna(0.0)

agg["direct_costs"] = agg["packaging_cost"] + agg["freight_total"] + agg["labour_total"] + agg["operation_total"] + agg["other_direct_costs"]
agg["GP"] = agg["sales_value_exGST"] - agg["COGS_core"] - agg["direct_costs"]
agg["GP%"] = agg.apply(lambda r: (r["GP"] / r["sales_value_exGST"])*100 if r["sales_value_exGST"] else 0.0, axis=1)

st.subheader("Step 2 — Summary by SKU")
st.dataframe(agg, use_container_width=True)

def to_excel_bytes(df_rows: pd.DataFrame, df_detail: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df_rows.to_excel(writer, index=False, sheet_name="SummaryBySKU")
        df_detail.to_excel(writer, index=False, sheet_name="Detail")
    return output.getvalue()

xls_bytes = to_excel_bytes(agg, df)
st.download_button(
    label="Download Excel",
    data=xls_bytes,
    file_name="gp_result.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

with st.expander("Detail rows"):
    st.dataframe(df, use_container_width=True)

st.success("Done. Adjust costs above and download again if needed.")
