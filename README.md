# Water–Opposition Pipeline

Internal research tool — THG. Quantifies water supply/demand stress as a causal driver of development opposition across US counties.

---

## What it does

Runs a 9-stage pipeline that ingests water stress and opposition data, builds a **Water Tension Index (WTI)** for every US county, fits an OLS regression against opposition rates (controlling for political lean, water law doctrine, and agricultural water share), and exports residuals + quadrant classifications to a live map.

**Output:** `data/output/water_opp_map.json` → served by the interactive map at GitHub Pages.

---

## Pipeline stages

| # | Module | Output | Notes |
|---|--------|--------|-------|
| 1 | `ingest/opposition.py` | `opposition_by_county.csv` | From `master_opposition.csv` |
| 2 | `ingest/controls.py` | `controls.csv` | Election data, water law, demographics |
| 3 | `ingest/drought_monitor.py` | `drought_monitor.csv` | USDM current + 10yr historical |
| 4 | `ingest/usgs_water.py` | `usgs_water.csv` | Groundwater + streamflow (~15 min) |
| 5 | `ingest/reservoirs.py` | `reservoirs.csv` | USBR + Army Corps NID |
| 6 | `ingest/wri_aqueduct.py` | `wri_aqueduct.csv` | Auto-skipped if no file present |
| 7 | `process/tension.py` | `water_tension.csv` | Builds WTI |
| 8 | `process/analysis.py` | `master_analysis.csv`, `analysis_summary.json` | OLS regression + residuals |
| 9 | `process/export_json.py` | `water_opp_map.json` | Map export |

---

## WRI Aqueduct data (required for full accuracy)

The pipeline auto-detects WRI Aqueduct 4.0 if you drop the CSV or GPKG into `data/raw/wri_aqueduct/`. Without it, Stage 6 is skipped and WTI relies on USDM + USGS only (directional but lower completeness).

Download from: [datasets.wri.org/dataset/aqueduct40](https://datasets.wri.org/dataset/aqueduct40)

---

## Usage

```bash
# Install dependencies
pip install -r requirements.txt

# Full rebuild
python build.py

# Skip slow fetches for a fast run
python build.py --skip-usgs --skip-usbr

# Limit USGS fetch to specific states
python build.py --states TX AZ NV VA

# Re-run analysis + export only (fastest; assumes processed data exists)
python build.py --analyze-only

# Re-export map JSON only
python build.py --export-only
```

---

## Map

Live at: `https://pricephillips.github.io/water-opp-pipeline/`

The map updates automatically every Tuesday after the USDM release via GitHub Actions (`.github/workflows/weekly-pipeline.yml`). You can also trigger it manually from the Actions tab.

**Layers:** Bivariate (WTI × Opposition), Water Tension Index, Opposition Rate, Model Residuals, Supply Deficit, Demand Pressure, Seasonal Pinch.

**Quadrant classifications:**
- **Water-driven** — high WTI + high opposition rate
- **Latent risk** — high WTI + no opposition yet
- **Other drivers** — low WTI + high opposition (political mobilisation, land-use conflict, or coordinated advocacy)
- **Quiet** — low WTI + no opposition

---

## Repo structure

```
water-opp-pipeline/
├── build.py                  # Pipeline orchestrator
├── ingest/                   # Data fetchers
├── process/                  # WTI, regression, export
├── data/
│   ├── raw/                  # Source files (gitignored)
│   │   └── wri_aqueduct/     # Drop WRI CSV/GPKG here
│   ├── processed/            # Intermediate CSVs (gitignored)
│   ├── output/               # Final outputs (committed)
│   └── seed/                 # Static reference data
├── docs/                     # GitHub Pages (map)
├── map/                      # Source map file
└── .github/workflows/        # Weekly auto-refresh
```

---

## Key outputs

| File | Description |
|------|-------------|
| `data/output/master_analysis.csv` | County-level WTI, opposition rates, residuals, controls |
| `data/output/analysis_summary.json` | Regression results, correlations, quadrant counts |
| `data/output/water_opp_map.json` | Map-ready JSON (consumed by `docs/index.html`) |
