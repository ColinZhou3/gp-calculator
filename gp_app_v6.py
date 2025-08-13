
import io, math, re
import pandas as pd
import streamlit as st

st.set_page_config(page_title="GP Calculator (v6: freight per crate/carton)", layout="wide")
st.title("Gross Profit Calculator — v6")
st.caption("Freight charged per crate/carton/bag by region. Crate handling fee is separate ($1 default).")

REQUIRED_SALES_COLS = ["sku","sales_qty","sales_unit","unit_price"]

with st.sidebar:
    st.header("Settings")
    gst_included = st.toggle("Sales include GST (15%)?", value=True)
    default_crate_fee = st.number_input("Crate handling fee ($/crate)", value=1.0, min_value=0.0, step=0.1)
    default_carton_fee = st.number_input("Carton issue fee ($/carton)", value=0.0, min_value=0.0, step=0.1)
    default_bag_fee = st.number_input("Bag issue fee ($/bag)", value=0.0, min_value=0.0, step=0.1)

    st.markdown("---")
    st.subheader("Upload core files")
    sales_file = st.file_uploader("Sales file (CSV or XLSX)", type=["csv","xlsx"])
    rules_file = st.file_uploader("Product Rules Excel (required)", type=["xlsx"])

    st.markdown("---")
    st.subheader("Upload optional files")
    store_region_file = st.file_uploader("Store -> Region mapping CSV (optional)", type=["csv"])
    region_freight_file = st.file_uploader("Region freight CSV (optional, with basis per_crate/per_carton/per_bag)", type=["csv"])

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
            df = pd.read_csv(upload, sep=None, engine="python", encoding="utf-8-sig")
    except pd.errors.EmptyDataError:
        return None, "Could not read data from the file (EmptyDataError). Check that it contains rows and a header."
    except Exception as e:
        return None, f"Failed to read the sales file: {e}"
    df.columns = [c.strip() for c in df.columns]
    return df, None

def load_rules(xls: bytes):
    x = pd.ExcelFile(xls)
    rules = pd.read_excel(x, "ProductRules")
    fees = pd.read_excel(x, "PackagingFees")
    return rules, fees

# Normalize packaging code to: carton/bag/crate
def normalize_packaging_type(value):
    if pd.isna(value): return ""
    s = str(value).strip().lower()
    if s in ("carton","crate","bag"): return s
    if s.startswith("c") or "cmm" in s or "css" in s: return "carton"
    if s.startswith("bgl") or s == "bag": return "bag"
    return "crate"

def normalize_sales(df_sales, df_rules):
    rules = df_rules.copy()
    rules["packaging_type"] = rules["packaging_type"].apply(normalize_packaging_type)
    merged = df_sales.merge(rules, on="sku", how="left", suffixes=("","_rule"))
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
        if unit in ("carton","ctn","crate","bag"):
            return qty
        if cap > 0:
            return math.ceil(float(row["sales_qty_kg"]) / cap)
        return 0.0
    merged["packages"] = merged.apply(package_count, axis=1)

    # split packages by type (for per_crate vs per_carton freight)
    merged["packages_crate"] = merged.apply(lambda r: r["packages"] if str(r.get("packaging_type","")).lower()=="crate" else 0.0, axis=1)
    merged["packages_carton"] = merged.apply(lambda r: r["packages"] if str(r.get("packaging_type","")).lower()=="carton" else 0.0, axis=1)
    merged["packages_bag"] = merged.apply(lambda r: r["packages"] if str(r.get("packaging_type","")).lower()=="bag" else 0.0, axis=1)

    return merged

def resolve_packaging_fee(row, fees_map: dict) -> float:
    if pd.notna(row.get("packaging_fee_per_unit")):
        return float(row["packaging_fee_per_unit"])
    ptype = str(row.get("packaging_type", "")).lower()
    return fees_map.get(ptype, 0.0)

def compute_freight(df, df_store_region, df_region_freight):
    if df_region_freight is None:
        return pd.Series([0.0]*len(df), index=df.index)
    # Backfill region by store if needed
    if df_store_region is not None and "region" in df.columns and "store_id" in df.columns:
        mask = df["region"].isna() | (df["region"]=="")
        if mask.any():
            region_map = df_store_region.set_index("store_id")["region"]
            df.loc[mask, "region"] = df.loc[mask, "store_id"].map(region_map)

    # Build rate map: region -> basis -> rate
    rf = df_region_freight.drop_duplicates(["region","basis"]).copy()
    rf["basis"] = rf["basis"].str.lower()
    rate_map = {}
    for _, r in rf.iterrows():
        region = str(r["region"]).upper()
        basis = str(r["basis"]).lower()
        rate = float(r["rate"])
        rate_map.setdefault(region, {})[basis] = rate

    # Compute per row based on packaging type and basis
    out = []
    for _, row in df.iterrows():
        reg = str(row.get("region","")).upper()
        ptype = str(row.get("packaging_type","")).lower()
        region_rates = rate_map.get(reg, {})
        units = 0.0
        rate = 0.0
        if ptype == "crate" and "per_crate" in region_rates:
            units = float(row["packages_crate"])
            rate = region_rates["per_crate"]
        elif ptype == "carton" and "per_carton" in region_rates:
            units = float(row["packages_carton"])
            rate = region_rates["per_carton"]
        elif ptype == "bag" and "per_bag" in region_rates:
            units = float(row["packages_bag"])
            rate = region_rates["per_bag"]
        elif "per_package" in region_rates:
            # fallback, if provided
            units = float(row["packages"])
            rate = region_rates["per_package"]
        elif "per_kg" in region_rates:
            units = float(row["sales_qty_kg"])
            rate = region_rates["per_kg"]
        out.append(units * rate)
    return pd.Series(out, index=df.index)

tab_calc, = st.tabs(["Calculator"])

with tab_calc:
    if not rules_file:
        st.info("Upload Product Rules Excel to start."); st.stop()

    # Load files
    df_sales, err = load_sales(sales_file)
    if err: st.error(err); st.stop()
    x = pd.ExcelFile(rules_file)
    df_rules = pd.read_excel(x, "ProductRules")
    df_fees = pd.read_excel(x, "PackagingFees")

    df_store_region = pd.read_csv(store_region_file) if store_region_file else None
    df_region_freight = pd.read_csv(region_freight_file) if region_freight_file else None

    # Packaging fees defaults
    fees_map = {str(t).lower(): float(v) for t, v in zip(df_fees["packaging_type"], df_fees["default_fee_per_unit"])}
    fees_map.setdefault("crate", default_crate_fee)
    fees_map.setdefault("carton", default_carton_fee)
    fees_map.setdefault("bag", default_bag_fee)

    # Normalize and compute base metrics
    df = normalize_sales(df_sales, df_rules)

    # Inputs per SKU
    st.subheader("Step 1 — Cost inputs (per SKU)")
    sku_list = sorted(df["sku"].unique().tolist())
    cost_input = pd.DataFrame({
        "sku": sku_list,
        "unit_cost_per_kg": [0.0]*len(sku_list),
        "labour_basis": ["per_kg"]*len(sku_list),
        "labour_rate": [0.0]*len(sku_list),
        "operation_total": [0.0]*len(sku_list),
        "other_direct_costs": [0.0]*len(sku_list),
    })
    cost_input = st.data_editor(cost_input,
        column_config={"labour_basis": st.column_config.SelectboxColumn(options=["per_kg","per_package"])},
        use_container_width=True, key="cost_input_v6")

    # Packaging handling/issue fees
    df["packaging_fee_per_unit_final"] = df.apply(lambda r: resolve_packaging_fee(r, fees_map), axis=1)
    df["packaging_cost"] = df["packages"] * df["packaging_fee_per_unit_final"]

    # Labour and COGS
    cm = cost_input.set_index("sku")
    df["unit_cost_per_kg"] = df["sku"].map(cm["unit_cost_per_kg"]).fillna(0.0)
    df["labour_basis"] = df["sku"].map(cm["labour_basis"]).fillna("per_kg")
    df["labour_rate"] = df["sku"].map(cm["labour_rate"]).fillna(0.0)
    df["labour_total"] = df.apply(lambda r: r["labour_rate"] * (r["sales_qty_kg"] if r["labour_basis"]=="per_kg" else r["packages"]), axis=1)
    df["COGS_core"] = df["sales_qty_kg"] * df["unit_cost_per_kg"]

    # Freight per crate/carton/bag
    df["freight_calc"] = compute_freight(df.copy(), df_store_region, df_region_freight)

    # Aggregate results
    agg = df.groupby(["sku","product_name"], as_index=False).agg({
        "sales_value_exGST":"sum",
        "sales_qty_kg":"sum",
        "packages":"sum",
        "packaging_cost":"sum",
        "COGS_core":"sum",
        "freight_calc":"sum",
        "labour_total":"sum",
    })
    agg["operation_total"] = agg["sku"].map(cm["operation_total"]).fillna(0.0)
    agg["other_direct_costs"] = agg["sku"].map(cm["other_direct_costs"]).fillna(0.0)

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
