from __future__ import annotations
"""
ingest/wri_aqueduct.py
-----------------------
Processes the WRI Aqueduct 4.0 dataset into county-level water stress scores.
"""

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "wri_aqueduct"
OUT_DIR = ROOT / "data" / "processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def normalize_wri(score: float | None, max_val: float = 5.0) -> float | None:
    if score is None:
        return None
    return round(min(100.0, max(0.0, (score / max_val) * 100)), 1)


def load_wri_csv(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        print(f" WRI CSV not found: {csv_path}")
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        records = list(csv.DictReader(f))
    print(f" Loaded {len(records)} WRI records from CSV")
    return records


def load_huc_fips_crosswalk(crosswalk_path: Path | None = None) -> dict[str, list[str]]:
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
    print(" No HUC→FIPS crosswalk found. Place file at data/raw/huc_fips_crosswalk.csv")
    return {}


def aggregate_wri_to_county(wri_records: list[dict], huc_fips: dict[str, list[str]]) -> dict[str, dict]:
    from collections import defaultdict
    county_scores = defaultdict(lambda: {"wri_baseline_stress": []})
    for rec in wri_records:
        huc_id = str(rec.get("pfaf_id", rec.get("aq30_id", ""))).strip()
        fips_list = huc_fips.get(huc_id, [])
        if not fips_list:
            continue
        bws = _safe_float(rec.get("bws_score"))
        for fips in fips_list:
            if bws is not None:
                county_scores[fips]["wri_baseline_stress"].append(bws)
    results = {}
    for fips, scores in county_scores.items():
        results[fips] = {"wri_baseline_stress": normalize_wri(_max_or_none(scores["wri_baseline_stress"]))}
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
    csv_files = list(RAW_DIR.glob("*baseline_annual*.csv")) if RAW_DIR.exists() else []
    if not csv_files:
        print(" No WRI Aqueduct data found in data/raw/wri_aqueduct/")
        return None
    wri_records = load_wri_csv(csv_files[0])
    crosswalk_path = ROOT / "data" / "raw" / "huc_fips_crosswalk.csv"
    huc_fips = load_huc_fips_crosswalk(crosswalk_path if crosswalk_path.exists() else None)
    county_scores = aggregate_wri_to_county(wri_records, huc_fips)
    print(f" Mapped {len(county_scores)} counties from WRI data")
    out_path = OUT_DIR / "wri_aqueduct.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["fips", "wri_baseline_stress"], extrasaction="ignore")
        writer.writeheader()
        for fips in sorted(county_scores.keys()):
            writer.writerow({"fips": fips, **county_scores[fips]})
    print(f" Saved → {out_path}")
    return out_path


if __name__ == "__main__":
    run()
