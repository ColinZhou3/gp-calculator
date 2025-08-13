
import io, math, re
import pandas as pd
import streamlit as st

st.set_page_config(page_title="GP Calculator (v6 fix3: robust region mapping)", layout="wide")
st.title("Gross Profit Calculator — v6 (fix3)")
st.caption("Fixes KeyError on region. Ensures 'region' exists and robustly maps store->region (CSV/XLSX).")

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
    store_region_file = st.file_uploader("Store -> Region mapping (CSV or XLSX)", type=["csv","xlsx"])
    region_freight_file = st.file_uploader("Region freight CSV (per_crate/per_carton/per_bag)", type=["csv"])

# ---------- helpers ----------
def _normalize_columns(df):
    canon = {c: c.strip() for c in df.columns}
    lower = {c: c.strip().lower() for c in df.columns}
    return canon, lower

def load_sales(upload):
    if upload is None:
        return None, "Please upload a sales file."
    name = upload.name.lower()
    try:
        if name.endswith(".xlsx"):
            df = pd.read_excel(upload)
        else:
            df = pd.read_csv(upload, sep=None, engine="python", encoding="utf-8-sig")
    except Exception as e:
        return None, f"Failed to read the sales file: {e}"

    canon, lower = _normalize_columns(df)

    def pick(*aliases):
        for a in aliases:
            for k, v in lower.items():
                if v == a: return canon[k]
            for k, v in lower.items():
                if a in v: return canon[k]
        return None

    mapping = {}
    mapping["sku"] = pick("sku","item number","item_number","product code","productcode","code","plu")
    mapping["sales_qty"] = pick("sales_qty","quantity","qty","sold qty")
    mapping["sales_unit"] = pick("sales_unit","unit","uom")
    mapping["unit_price"] = pick("unit_price","unit price","price","ex gst price","exgst price")
    mapping["value"] = pick("value","total","exgst total","ex gst total","amount","net amount","incgst total")
    mapping["product_name"] = pick("product_name","description","item name","name")
    mapping["date"] = pick("date","invoice date","trans date","sale date")
    mapping["store_id"] = pick("store_id","shop id","store code","store no")
    mapping["store_name"] = pick("store_name","store","shop name","store description","store desc","store")

    sel = {k: v for k, v in mapping.items() if v in df.columns}
    out = df[list(sel.values())].copy()
    out.columns = list(sel.keys())

    # create region col if not present
    if "region" not in out.columns:
        out["region"] = None

    if "value" not in out.columns and set(["sales_qty","unit_price"]).issubset(out.columns):
        out["value"] = out["sales_qty"] * out["unit_price"]

    if "sku" in out.columns:
        out["sku"] = out["sku"].astype(str).str.strip().str.upper()
    if "store_name" in out.columns:
        out["store_name"] = out["store_name"].astype(str).str.strip().str.lower()
    if "store_id" in out.columns:
        out["store_id"] = out["store_id"].astype(str).str.strip()

    missing = [c for c in REQUIRED_SALES_COLS if c not in out.columns]
    if missing:
        return None, f"Sales file is missing required columns after normalization: {missing}. Found columns: {list(df.columns)}"
    return out, None

def load_rules(xls: bytes):
    x = pd.ExcelFile(xls)
    rules = pd.read_excel(x, "ProductRules")
    try:
        fees = pd.read_excel(x, "PackagingFees")
    except Exception:
        fees = pd.DataFrame({"packaging_type":["crate","carton","bag"], "default_fee_per_unit":[1.0,0.0,0.0]})

    canon, lower = _normalize_columns(rules)

    def pick(*aliases):
        for a in aliases:
            for k, v in lower.items():
                if v == a: return canon[k]
            for k, v in lower.items():
                if a in v: return canon[k]
        return None

    rmap = {}
    rmap["sku"] = pick("sku","item number","product code","code","plu")
    rmap["product_name"] = pick("product_name","name","description")
    rmap["packaging_type"] = pick("packaging_type","packaging","pack type","package","cmm","css","bgl")
    rmap["sell_unit"] = pick("sell_unit","sell unit","sales unit")
    rmap["kg_per_sell_unit"] = pick("kg_per_sell_unit","kg per sell unit","weight per unit","kg per unit")
    rmap["packaging_capacity_kg"] = pick("packaging_capacity_kg","capacity kg","crate capacity","carton capacity")

    sel = {k: v for k, v in rmap.items() if v in rules.columns}
    rules2 = rules[list(sel.values())].copy()
    rules2.columns = list(sel.keys())

    if "sku" not in rules2.columns or "packaging_type" not in rules2.columns:
        st.error("ProductRules must include 'sku' and 'packaging_type'."); st.stop()

    rules2["sku"] = rules2["sku"].astype(str).str.strip().str.upper()
    return rules2, fees

# Map packaging code to standard
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

    s = df_sales.copy()
    s["sku"] = s["sku"].astype(str).str.strip().str.upper()

    merged = s.merge(rules, on="sku", how="left", suffixes=("","_rule"))

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

    merged["packages_crate"] = merged.apply(lambda r: r["packages"] if str(r.get("packaging_type","")).lower()=="crate" else 0.0, axis=1)
    merged["packages_carton"] = merged.apply(lambda r: r["packages"] if str(r.get("packaging_type","")).lower()=="carton" else 0.0, axis=1)
    merged["packages_bag"] = merged.apply(lambda r: r["packages"] if str(r.get("packaging_type","")).lower()=="bag" else 0.0, axis=1)
    return merged

# Read store-region CSV/XLSX flexibly and standardize columns
def load_store_region(upload):
    if upload is None:
        return None
    name = upload.name.lower()
    try:
        if name.endswith(".xlsx"):
            df = pd.read_excel(upload)
        else:
            df = pd.read_csv(upload, sep=None, engine="python", encoding="utf-8-sig")
    except Exception as e:
        st.warning(f"Failed to read store->region file: {e}")
        return None

    canon, lower = _normalize_columns(df)
    def pick(*aliases):
        for a in aliases:
            for k, v in lower.items():
                if v == a: return canon[k]
            for k, v in lower.items():
                if a in v: return canon[k]
        return None

    mapping = {}
    mapping["store_id"] = pick("store_id","shop id","store code","store no","id")
    mapping["store_name"] = pick("store_name","store name","store","shop name","store description","store desc")
    mapping["region"] = pick("region","area","zone")

    cols = [c for c in mapping.values() if c]
    if not cols:
        st.warning("Store->Region: No recognizable columns. Expecting store_id/store_name/region.")
        return None

    out = df[cols].copy()
    # rename to canonical names if present
    rename = {}
    if mapping.get("store_id"): rename[mapping["store_id"]] = "store_id"
    if mapping.get("store_name"): rename[mapping["store_name"]] = "store_name"
    if mapping.get("region"): rename[mapping["region"]] = "region"
    out = out.rename(columns=rename)

    if "store_id" in out.columns:
        out["store_id"] = out["store_id"].astype(str).str.strip()
    if "store_name" in out.columns:
        out["store_name"] = out["store_name"].astype(str).str.strip().str.lower()
    if "region" in out.columns:
        out["region"] = out["region"].astype(str).str.strip().str.upper().str.replace(r"[^A-Z]", "", regex=True)
    return out

def resolve_packaging_fee(row, fees_map: dict) -> float:
    if pd.notna(row.get("packaging_fee_per_unit")):
        return float(row["packaging_fee_per_unit"])
    ptype = str(row.get("packaging_type", "")).lower()
    return fees_map.get(ptype, 0.0)

def compute_freight(df, df_store_region, df_region_freight):
    # always ensure region exists
    if "region" not in df.columns:
        df["region"] = None

    # populate region from store mapping if provided
    if df_store_region is not None and not df_store_region.empty:
        s = df_store_region.copy()

        # by store_id
        if "store_id" in df.columns and "store_id" in s.columns:
            mask = df["region"].isna() | (df["region"]=="")
            if mask.any():
                region_map = s.dropna(subset=["store_id","region"]).set_index("store_id")["region"]
                df.loc[mask, "region"] = df.loc[mask, "store_id"].map(region_map)

        # by store_name
        if "store_name" in df.columns and "store_name" in s.columns:
            mask2 = df["region"].isna() | (df["region"]=="")
            if mask2.any():
                region_map2 = s.dropna(subset=["store_name","region"]).set_index("store_name")["region"]
                df.loc[mask2, "region"] = df.loc[mask2, "store_name"].map(region_map2)

    if df_region_freight is None or df_region_freight.empty:
        return pd.Series([0.0]*len(df), index=df.index)

    rf = df_region_freight.drop_duplicates(["region","basis"]).copy()
    rf["region"] = rf["region"].astype(str).str.upper()
    rf["basis"] = rf["basis"].astype(str).str.lower()
    rate_map = {}
    for _, r in rf.iterrows():
        region = str(r["region"]).upper()
        basis = str(r["basis"]).lower()
        rate = float(r.get("rate", 0.0) or 0.0)
        rate_map.setdefault(region, {})[basis] = rate

    out = []
    for _, row in df.iterrows():
        reg = str(row.get("region","") or "").upper()
        ptype = str(row.get("packaging_type","") or "").lower()
        region_rates = rate_map.get(reg, {})
        units = 0.0
        rate = 0.0
        if ptype == "crate" and "per_crate" in region_rates:
            units = float(row["packages_crate"]); rate = region_rates["per_crate"]
        elif ptype == "carton" and "per_carton" in region_rates:
            units = float(row["packages_carton"]); rate = region_rates["per_carton"]
        elif ptype == "bag" and "per_bag" in region_rates:
            units = float(row["packages_bag"]); rate = region_rates["per_bag"]
        elif "per_package" in region_rates:
            units = float(row["packages"]); rate = region_rates["per_package"]
        elif "per_kg" in region_rates:
            units = float(row["sales_qty_kg"]); rate = region_rates["per_kg"]
        out.append(units * rate)
    return pd.Series(out, index=df.index)

# ---------- UI ----------
tab_calc, = st.tabs(["Calculator"])

with tab_calc:
    if not rules_file:
        st.info("Upload Product Rules Excel to start."); st.stop()

    df_sales, err = load_sales(sales_file)
    if err:
        st.error(err); st.stop()

    df_rules, df_fees = load_rules(rules_file)
    df_store_region = load_store_region(store_region_file)
    df_region_freight = pd.read_csv(region_freight_file) if region_freight_file else None

    fees_map = {str(t).lower(): float(v) for t, v in zip(df_fees["packaging_type"], df_fees["default_fee_per_unit"])}
    fees_map.setdefault("crate", default_crate_fee)
    fees_map.setdefault("carton", default_carton_fee)
    fees_map.setdefault("bag", default_bag_fee)

    df = normalize_sales(df_sales, df_rules)

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
        use_container_width=True, key="cost_input_v6fix3")

    df["packaging_fee_per_unit_final"] = df.apply(lambda r: resolve_packaging_fee(r, fees_map), axis=1)
    df["packaging_cost"] = df["packages"] * df["packaging_fee_per_unit_final"]

    cm = cost_input.set_index("sku")
    df["unit_cost_per_kg"] = df["sku"].map(cm["unit_cost_per_kg"]).fillna(0.0)
    df["labour_basis"] = df["sku"].map(cm["labour_basis"]).fillna("per_kg")
    df["labour_rate"] = df["sku"].map(cm["labour_rate"]).fillna(0.0)
    df["labour_total"] = df.apply(lambda r: r["labour_rate"] * (r["sales_qty_kg"] if r["labour_basis"]=="per_kg" else r["packages"]), axis=1)
    df["COGS_core"] = df["sales_qty_kg"] * df["unit_cost_per_kg"]

    df["freight_calc"] = compute_freight(df.copy(), df_store_region, df_region_freight)

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
