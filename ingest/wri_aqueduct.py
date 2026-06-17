from __future__ import annotations
"""
ingest/wri_aqueduct.py
-----------------------
Processes the WRI Aqueduct 4.0 dataset into county-level water stress scores.

WRI Aqueduct is published annually at the HUC (watershed) level.
This script maps HUC → county FIPS using a crosswalk and aggregates
the key indicators to county level.

Manual download required:
    URL: https://datasets.wri.org/dataset/aqueduct40
    File: "Aqueduct40_Y2023D07_baseline_monthly_gpkg.zip"  (~500MB GeoPackage)
    Save to: data/raw/wri_aqueduct/

Key WRI indicators used:
    bws_score       : Baseline Water Stress (0–5 scale; 5 = extremely high)
    bws_label       : Text label for bws_score
    drr_score       : Drought Risk (0–5 scale)
    iav_score       : Interannual Variability (0–5 scale)
    sev_score       : Seasonal Variability (0–5 scale)
    gtd_score       : Groundwater Table Decline (0–5 scale)
    rfr_score       : Riverine Flood Risk
    udw_score       : Unimproved/No Drinking Water
    usa_score       : Unimproved/No Sanitation
    cep_score       : Coastal Eutrophication Potential

For opposition risk we focus on: bws, drr, iav, sev, gtd

Outputs: data/processed/wri_aqueduct.csv
"""

import csv
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "wri_aqueduct"
OUT_DIR = ROOT / "data" / "processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# WRI Aqueduct score → normalized 0–100
def normalize_wri(score: float | None, max_val: float = 5.0) -> float | None:
    if score is None:
        return None
    return round(min(100.0, max(0.0, (score / max_val) * 100)), 1)


def load_wri_gpkg(gpkg_path: Path) -> list[dict]:
    """
    Load WRI Aqueduct GeoPackage using fiona + geopandas.
    
    The GeoPackage contains HUC-6 watershed polygons with WRI indicator scores.
    """
    try:
        import geopandas as gpd
        print(f"  Loading WRI GeoPackage: {gpkg_path} ...")
        gdf = gpd.read_file(gpkg_path)
        print(f"  Loaded {len(gdf)} watershed features")

        records = []
        for _, row in gdf.iterrows():
            records.append({
                "aq30_id": row.get("aq30_id", ""),
                "name_0": row.get("name_0", ""),   # Country
                "name_1": row.get("name_1", ""),   # State/Province
                "pfaf_id": row.get("pfaf_id", ""), # HydroBASINS Pfafstetter code
                "bws_score": row.get("bws_score"),
                "bws_label": row.get("bws_label", ""),
                "drr_score": row.get("drr_score"),
                "iav_score": row.get("iav_score"),
                "sev_score": row.get("sev_score"),
                "gtd_score": row.get("gtd_score"),
                "rfr_score": row.get("rfr_score"),
                "geometry": row.get("geometry"),
            })
        return records

    except ImportError:
        print("  geopandas/fiona not installed.")
        print("  Run: pip install geopandas fiona")
        return []
    except Exception as e:
        print(f"  WRI GPKG load error: {e}")
        return []


def load_wri_csv(csv_path: Path) -> list[dict]:
    """
    Load WRI Aqueduct from CSV export (alternative to GeoPackage).
    WRI also publishes a flat CSV at:
    https://datasets.wri.org/dataset/aqueduct40
    File: "Aqueduct40_Y2023D07_baseline_annual.csv"
    """
    if not csv_path.exists():
        print(f"  WRI CSV not found: {csv_path}")
        return []

    records = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(row)
    print(f"  Loaded {len(records)} WRI records from CSV")
    return records


def load_huc_fips_crosswalk(crosswalk_path: Path | None = None) -> dict[str, list[str]]:
    """
    Load HUC-6/HUC-8 → county FIPS crosswalk.

    Source: USGS StreamStats or the EPA's WBD-FIPS crosswalk table.
    Download: https://www.epa.gov/waterdata/waters-geospatial-data-downloads

    Returns: {huc_id: [fips1, fips2, ...]}
    """
    if crosswalk_path and crosswalk_path.exists():
        crosswalk = {}
        with open(crosswalk_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                huc = row.get("huc", row.get("HUC", "")).strip()
                fips = row.get("county_fips", row.get("FIPS", "")).strip().zfill(5)
                if huc and fips:
                    crosswalk.setdefault(huc, []).append(fips)
        return crosswalk

    # If no crosswalk file, return empty — will fall back to state-level seed
    print("  No HUC→FIPS crosswalk found. Place file at data/raw/huc_fips_crosswalk.csv")
    print("  Download from: https://www.epa.gov/waterdata/waters-geospatial-data-downloads")
    return {}


def aggregate_wri_to_county(
    wri_records: list[dict],
    huc_fips: dict[str, list[str]],
) -> dict[str, dict]:
    """
    Map WRI HUC-level records to county FIPS using the crosswalk.
    Where multiple HUCs map to one county, take the maximum stress score.

    Returns: {fips: {wri_baseline_stress, wri_drought_risk, wri_seasonal_variability,
                      wri_gw_decline, wri_interannual_variability}}
    """
    from collections import defaultdict

    county_scores = defaultdict(lambda: {
        "wri_baseline_stress": [],
        "wri_drought_risk": [],
        "wri_seasonal_variability": [],
        "wri_gw_decline": [],
        "wri_interannual_variability": [],
    })

    for rec in wri_records:
        # Try to get pfaf_id or aq30_id for crosswalk lookup
        huc_id = str(rec.get("pfaf_id", rec.get("aq30_id", ""))).strip()
        fips_list = huc_fips.get(huc_id, [])

        if not fips_list:
            # Try state-level fallback via name_1 (state name)
            # This is handled separately in process/score.py seed logic
            continue

        bws = _safe_float(rec.get("bws_score"))
        drr = _safe_float(rec.get("drr_score"))
        sev = _safe_float(rec.get("sev_score"))
        gtd = _safe_float(rec.get("gtd_score"))
        iav = _safe_float(rec.get("iav_score"))

        for fips in fips_list:
            if bws is not None:
                county_scores[fips]["wri_baseline_stress"].append(bws)
            if drr is not None:
                county_scores[fips]["wri_drought_risk"].append(drr)
            if sev is not None:
                county_scores[fips]["wri_seasonal_variability"].append(sev)
            if gtd is not None:
                county_scores[fips]["wri_gw_decline"].append(gtd)
            if iav is not None:
                county_scores[fips]["wri_interannual_variability"].append(iav)

    # Aggregate: use maximum stress for each county (conservative / precautionary)
    results = {}
    for fips, scores in county_scores.items():
        results[fips] = {
            "wri_baseline_stress": normalize_wri(_max_or_none(scores["wri_baseline_stress"])),
            "wri_drought_risk": normalize_wri(_max_or_none(scores["wri_drought_risk"])),
            "wri_seasonal_variability": normalize_wri(_max_or_none(scores["wri_seasonal_variability"])),
            "wri_gw_decline": normalize_wri(_max_or_none(scores["wri_gw_decline"])),
            "wri_interannual_variability": normalize_wri(_max_or_none(scores["wri_interannual_variability"])),
        }

    return results


def _safe_float(val) -> float | None:
    try:
        f = float(val)
        return f if f >= 0 else None
    except (ValueError, TypeError):
        return None


def _max_or_none(lst: list) -> float | None:
    clean = [v for v in lst if v is not None]
    return max(clean) if clean else None


def run():
    print("=== WRI Aqueduct Ingestion ===")

    # Try GeoPackage first, then CSV
    gpkg_files = list(RAW_DIR.glob("*.gpkg")) if RAW_DIR.exists() else []
    csv_files = list(RAW_DIR.glob("*baseline_annual*.csv")) if RAW_DIR.exists() else []

    wri_records = []
    if gpkg_files:
        wri_records = load_wri_gpkg(gpkg_files[0])
    elif csv_files:
        wri_records = load_wri_csv(csv_files[0])
    else:
        print("  No WRI Aqueduct data found in data/raw/wri_aqueduct/")
        print("  Download from: https://datasets.wri.org/dataset/aqueduct40")
        print("  Expected: Aqueduct40_Y2023D07_baseline_annual.csv")
        print("  Skipping WRI — build will use seed defaults for baseline stress.")
        return None

    # Load HUC→FIPS crosswalk
    crosswalk_path = ROOT / "data" / "raw" / "huc_fips_crosswalk.csv"
    huc_fips = load_huc_fips_crosswalk(crosswalk_path if crosswalk_path.exists() else None)

    # Aggregate to county
    county_scores = aggregate_wri_to_county(wri_records, huc_fips)
    print(f"  Mapped {len(county_scores)} counties from WRI data")

    # Save
    fieldnames = [
        "fips",
        "wri_baseline_stress",
        "wri_drought_risk",
        "wri_seasonal_variability",
        "wri_gw_decline",
        "wri_interannual_variability",
    ]
    out_path = OUT_DIR / "wri_aqueduct.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for fips in sorted(county_scores.keys()):
            writer.writerow({"fips": fips, **county_scores[fips]})

    print(f"  Saved → {out_path}")
    print("Done.\n")
    return out_path


if __name__ == "__main__":
    run()
