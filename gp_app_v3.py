
import io, math
import pandas as pd
import streamlit as st

st.set_page_config(page_title="GP Calculator (v3 robust loader)", layout="wide")
st.title("Gross Profit Calculator — v3")
st.caption("Robust file loading + region freight + rules check")

REQUIRED_SALES_COLS = ["sku","sales_qty","sales_unit","unit_price"]
OPTIONAL_SALES_COLS = ["value","region","store_id","product_name","date"]

with st.sidebar:
    st.header("Settings")
    gst_included = st.toggle("Sales include GST (15%)?", value=True)
    default_crate_fee = st.number_input("Default crate issue fee ($/crate)", value=1.0, min_value=0.0, step=0.1)
    default_carton_fee = st.number_input("Default carton fee ($/carton)", value=0.0, min_value=0.0, step=0.1)

    st.markdown("---")
    st.subheader("Upload core files")
    sales_file = st.file_uploader("Sales file (CSV or XLSX)", type=["csv","xlsx"])
    rules_file = st.file_uploader("Product Rules Excel (required)", type=["xlsx"])

    st.markdown("---")
    st.subheader("Upload optional files")
    store_region_file = st.file_uploader("Store -> Region mapping CSV (optional)", type=["csv"])
    region_freight_file = st.file_uploader("Region freight CSV (optional)", type=["csv"])

def load_sales(upload):
    if upload is None:
        return None, "Please upload a sales file."
    if upload.size == 0:
        return None, "The uploaded sales file is empty (0 bytes). Please re-upload."
    name = upload.name.lower()
    try:
        if name.endswith(".xlsx"):
            df = pd.read_excel(upload)
        else:
            # auto-detect delimiter, handle utf-8-sig, errors
            df = pd.read_csv(upload, sep=None, engine="python", encoding="utf-8-sig")
    except pd.errors.EmptyDataError:
        return None, "Could not read data from the file (EmptyDataError). Check that it contains rows and a header."
    except Exception as e:
        return None, f"Failed to read the sales file: {e}"
    # normalize col names
    df.columns = [c.strip() for c in df.columns]
    return df, None

def validate_sales_cols(df):
    missing = [c for c in REQUIRED_SALES_COLS if c not in df.columns]
    return missing

def load_rules(xls: bytes):
    try:
        x = pd.ExcelFile(xls)
        rules = pd.read_excel(x, "ProductRules")
        fees = pd.read_excel(x, "PackagingFees")
    except Exception as e:
        st.error(f"Failed to read Product Rules Excel: {e}")
        st.stop()
    return rules, fees

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

def compute_freight(df: pd.DataFrame, df_store_region: pd.DataFrame|None, df_region_freight: pd.DataFrame|None) -> pd.Series:
    if df_region_freight is None:
        return pd.Series([0.0]*len(df), index=df.index)
    if df_store_region is not None and "region" in df.columns and "store_id" in df.columns:
        mask = df["region"].isna() | (df["region"]=="")
        if mask.any():
            region_map = df_store_region.set_index("store_id")["region"]
            df.loc[mask, "region"] = df.loc[mask, "store_id"].map(region_map)
    rates = df_region_freight.drop_duplicates("region").set_index("region").to_dict(orient="index")
    result = []
    for _, row in df.iterrows():
        r = str(row.get("region","")).upper()
        info = rates.get(r, None)
        if not info:
            result.append(0.0); continue
        basis = str(info.get("basis","per_package"))
        rate = float(info.get("rate",0.0))
        if basis == "per_kg":
            result.append(float(row["sales_qty_kg"]) * rate)
        else:
            result.append(float(row["packages"]) * rate)
    return pd.Series(result, index=df.index)

# Tabs: Calculator & Rules Check
tab_calc, tab_check = st.tabs(["Calculator","Rules Check"])

with tab_calc:
    if not rules_file:
        st.info("Upload Product Rules Excel to start.")
        st.stop()

    df_sales, err = load_sales(sales_file)
    if err:
        st.error(err)
        st.stop()

    missing = validate_sales_cols(df_sales)
    if missing:
        st.error("Sales file is missing required columns: " + ", ".join(missing))
        st.caption("Required: " + ", ".join(REQUIRED_SALES_COLS) + " | Optional: " + ", ".join(OPTIONAL_SALES_COLS))
        st.dataframe(df_sales.head(), use_container_width=True)
        st.stop()

    df_rules, df_fees = load_rules(rules_file)
    df_store_region = pd.read_csv(store_region_file) if store_region_file else None
    df_region_freight = pd.read_csv(region_freight_file) if region_freight_file else None

    fees_map = {str(t).lower(): float(v) for t, v in zip(df_fees["packaging_type"], df_fees["default_fee_per_unit"])}
    fees_map["crate"] = default_crate_fee
    fees_map["carton"] = default_carton_fee

    df = normalize_sales(df_sales, df_rules)

    st.subheader("Step 1 — Cost inputs (per SKU)")
    sku_list = sorted(df["sku"].unique().tolist())
    cost_input = pd.DataFrame({
        "sku": sku_list,
        "unit_cost_per_kg": [0.0]*len(sku_list),
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

    df["freight_calc"] = compute_freight(df.copy(), df_store_region, df_region_freight)

    agg = df.groupby(["sku","product_name"], as_index=False).agg({
        "sales_value_exGST":"sum",
        "sales_qty_kg":"sum",
        "packages":"sum",
        "packaging_cost":"sum",
        "COGS_core":"sum",
        "freight_calc":"sum"
    })
    for col in ["labour_total","operation_total","other_direct_costs"]:
        agg[col] = agg["sku"].map(df_cost_map[col]).fillna(0.0)

    agg["direct_costs"] = agg["packaging_cost"] + agg["freight_calc"] + agg["labour_total"] + agg["operation_total"] + agg["other_direct_costs"]
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
    st.download_button("Download Excel", xls_bytes, "gp_result.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    with st.expander("Detail rows"):
        st.dataframe(df, use_container_width=True)

with tab_check:
    st.subheader("Missing Rules Check")
    if not sales_file or not rules_file:
        st.info("Upload Sales and Rules to run checks.")
        st.stop()

    df_sales, err = load_sales(sales_file)
    if err:
        st.error(err)
        st.stop()

    df_rules, _ = load_rules(rules_file)
    REQUIRED_RULE_FIELDS = ["sell_unit","kg_per_sell_unit","packaging_type","packaging_capacity_kg"]

    missing_sku = sorted(set(df_sales["sku"]) - set(df_rules["sku"]))
    if missing_sku:
        st.warning("SKUs in Sales but not in ProductRules:")
        st.dataframe(pd.DataFrame({"sku": missing_sku}), use_container_width=True)

    present = df_rules[df_rules["sku"].isin(df_sales["sku"])].copy()
    def missing_fields(row):
        fields = [f for f in REQUIRED_RULE_FIELDS if pd.isna(row.get(f)) or row.get(f) == ""]
        return ", ".join(fields)
    if not present.empty:
        present["missing_fields"] = present.apply(missing_fields, axis=1)
        need_fix = present[present["missing_fields"] != ""].copy()
        if need_fix.empty:
            st.success("All needed rule fields are present for SKUs in Sales.")
        else:
            st.error("Some SKUs have incomplete rules. Please update the columns listed below in your rules file and re-upload.")
            show_cols = ["sku","product_name"] + REQUIRED_RULE_FIELDS + ["missing_fields"]
            st.dataframe(need_fix[show_cols], use_container_width=True)
