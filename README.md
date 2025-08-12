# GP Calculator (Streamlit MVP)

## Files
- gp_app.py — Streamlit app
- sample_sales.csv — sample sales data
- sample_product_rules_with_packaging.xlsx — product rules (with packaging types)
- requirements.txt — Python deps

## Local run
```bash
pip install -r requirements.txt
streamlit run gp_app.py
```

## Deploy on Streamlit Cloud
1. Push these files to a GitHub repo.
2. On share.streamlit.io, click "Create app" and select the repo/branch.
3. Set the main file as `gp_app.py` and deploy.

## Notes
- Toggle GST in the sidebar.
- Enter per-SKU cost inputs in the grid, then download the Excel result.
- Packaging fee defaults come from the `PackagingFees` sheet; SKU-level overrides are supported.
