from __future__ import annotations
"""
ingest/wri_aqueduct.py
-----------------------
Processes the WRI Aqueduct 4.0 dataset into county-level water stress scores.

Reads from the Aqueduct geodatabase (same source used to build the crosswalk)
so pfaf_id keys are consistent between the WRI data and huc_fips_crosswalk.csv.

Source GDB: data/raw/Aqueduct40_waterrisk_download_Y2023M07D05/GDB/Aq40_Y2023D07M05.gdb
Layer:      baseline_annual
Crosswalk:  data/raw/huc_fips_crosswalk.csv  (columns: pfaf_id, county_fips)

Outputs: data/processed/wri_aqueduct.csv
"""

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)

WRI_GDB = (
    ROOT
    / "data"
    / "raw"
    / "Aqueduct40_waterrisk_download_Y2023M07D05"
    / "GDB"
    / "Aq40_Y2023D07M05.gdb"
)
WRI_LAYER = "baseline_annual"
CROSSWALK_PATH = ROOT / "data" / "raw" / "huc_fips_crosswalk.csv"


def normalize_wri(score: float | None, max_val: float = 5.0) -> float | None:
    if score is None:
        return None
    return round(min(100.0, max(0.0, (score / max_val) * 100)), 1)


def _safe_float(val) -> float | None:
    try:
        f = float(val)
        return f if f >= 0 else None
    except (ValueError, TypeError):
        return None


def _max_or_none(lst: list) -> float | None:
    clean = [v for v in lst if v is not None]
    return max(clean) if clean else None


def _normalize_pfaf(val) -> str:
    """
    Normalize pfaf_id to a plain integer string.
    GDB returns floats (355000.0); crosswalk stores strings ("355000").
    Converting via int strips the decimal so both sides match.
    """
    try:
        return str(int(float(val)))
    except (ValueError, TypeError):
        return ""


def load_wri_gdb(gdb_path: Path) -> list[dict]:
    if not gdb_path.exists():
        print(f"  WRI geodatabase not found: {gdb_path}")
        return []

    try:
        import geopandas as gpd
    except ImportError:
        print("  geopandas is required.  Run: pip3 install geopandas")
        return []

    gdf = gpd.read_file(gdb_path, layer=WRI_LAYER)
    print(f"  Loaded {len(gdf)} records from {WRI_LAYER}")

    keep = ["pfaf_id", "bws_score", "drr_score", "iav_score", "sev_score", "gtd_score"]
    rows = []
    for _, row in gdf[keep].iterrows():
        rows.append({k: row.get(k) for k in keep})
    return rows


def load_huc_fips_crosswalk(crosswalk_path: Path) -> dict[str, list[str]]:
    if not crosswalk_path.exists():
        print(f"  Crosswalk not found: {crosswalk_path}")
        return {}

    crosswalk: dict[str, list[str]] = {}
    with open(crosswalk_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = _normalize_pfaf(row.get("pfaf_id", row.get("huc", "")))
            fips = str(row.get("county_fips", row.get("FIPS", ""))).strip().zfill(5)
            if key and fips:
                crosswalk.setdefault(key, []).append(fips)

    print(f"  Crosswalk loaded: {len(crosswalk)} unique pfaf_id keys")
    return crosswalk


def aggregate_wri_to_county(
    wri_records: list[dict],
    huc_fips: dict[str, list[str]],
) -> dict[str, dict]:
    from collections import defaultdict

    county_scores: dict[str, dict[str, list]] = defaultdict(lambda: {
        "wri_baseline_stress": [],
        "wri_drought_risk": [],
        "wri_seasonal_variability": [],
        "wri_gw_decline": [],
        "wri_interannual_variability": [],
    })

    matched = 0
    for rec in wri_records:
        key = _normalize_pfaf(rec.get("pfaf_id", ""))
        if not key:
            continue

        fips_list = huc_fips.get(key, [])
        if not fips_list:
            continue

        matched += 1
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

    print(f"  WRI records matched to crosswalk: {matched}")

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


def run():
    print("=== WRI Aqueduct Ingestion ===")

    wri_records = load_wri_gdb(WRI_GDB)
    if not wri_records:
        print("  Skipping WRI — build will use seed defaults for baseline stress.")
        return None

    huc_fips = load_huc_fips_crosswalk(CROSSWALK_PATH)
    if not huc_fips:
        print("  Skipping WRI — no crosswalk available.")
        return None

    county_scores = aggregate_wri_to_county(wri_records, huc_fips)
    print(f"  Mapped {len(county_scores)} counties from WRI data")

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

    print(f"  Saved -> {out_path}")
    print("Done.\n")
    return out_path


if __name__ == "__main__":
    run()
