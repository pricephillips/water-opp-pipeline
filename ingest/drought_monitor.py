from __future__ import annotations

import csv
from pathlib import Path
from datetime import date, timedelta

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "data" / "processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)

INPUT_CSV = RAW_DIR / "usdm_county_latest.csv"


def last_thursday(d: date | None = None) -> date:
    d = d or date.today()
    days_back = (d.weekday() - 3) % 7
    return d - timedelta(days=days_back)


def _pick(row: dict, *names: str, default=""):
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return default


def _norm_fips(value) -> str:
    s = str(value).strip()
    if not s or s.lower() == "none":
        return ""
    if s.endswith(".0"):
        s = s[:-2]
    if s.isdigit():
        return s.zfill(5)
    try:
        return str(int(float(s))).zfill(5)
    except Exception:
        return ""


def _to_float(value, default=0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def load_usdm_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Missing input file: {path}")

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Loaded {len(rows)} raw USDM rows from {path}")
    return rows


def compute_current_scores(rows: list[dict]) -> dict[str, dict]:
    results = {}
    for row in rows:
        fips = _norm_fips(_pick(row, "FIPS", "fips", "CountyFIPS", "county_fips"))
        if not fips:
            continue

        d0 = _to_float(_pick(row, "D0", "d0"))
        d1 = _to_float(_pick(row, "D1", "d1"))
        d2 = _to_float(_pick(row, "D2", "d2"))
        d3 = _to_float(_pick(row, "D3", "d3"))
        d4 = _to_float(_pick(row, "D4", "d4"))
        none = _to_float(_pick(row, "None", "none"))

        pcts = {"D4": d4, "D3": d3, "D2": d2, "D1": d1, "D0": d0, "None": none}

        dominant = "None"
        for cat in ["D4", "D3", "D2", "D1", "D0"]:
            if pcts.get(cat, 0) > 10:
                dominant = cat
                break

        weighted = d0 * 0.2 + d1 * 0.4 + d2 * 0.6 + d3 * 0.8 + d4 * 1.0
        pct_d0_plus = d0 + d1 + d2 + d3 + d4
        pct_d2_plus = d2 + d3 + d4

        results[fips] = {
            "usdm_current_category": dominant,
            "usdm_current_score": round(weighted, 1),
            "usdm_pct_d0_plus": round(pct_d0_plus, 1),
            "usdm_pct_d2_plus": round(pct_d2_plus, 1),
            "usdm_as_of": _pick(row, "MapDate", "map_date", "date", "week", default=""),
        }

    return results


def save_drought_csv(current: dict, out_path: Path):
    fieldnames = [
        "fips",
        "usdm_current_category",
        "usdm_current_score",
        "usdm_pct_d0_plus",
        "usdm_pct_d2_plus",
        "drought_frequency_score",
        "usdm_peak_drought_d3d4_pct",
        "usdm_as_of",
    ]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for fips in sorted(current):
            row = {
                "fips": fips,
                "usdm_current_category": current[fips].get("usdm_current_category", ""),
                "usdm_current_score": current[fips].get("usdm_current_score", ""),
                "usdm_pct_d0_plus": current[fips].get("usdm_pct_d0_plus", ""),
                "usdm_pct_d2_plus": current[fips].get("usdm_pct_d2_plus", ""),
                "drought_frequency_score": "",
                "usdm_peak_drought_d3d4_pct": "",
                "usdm_as_of": current[fips].get("usdm_as_of", ""),
            }
            writer.writerow(row)

    print(f"Saved {len(current)} county rows → {out_path}")


def run():
    print("=== USDM Drought Monitor Ingestion ===")
    print(f"Using local file: {INPUT_CSV}")
    print(f"Snapshot date: {last_thursday()}")

    rows = load_usdm_csv(INPUT_CSV)
    current_scores = compute_current_scores(rows)

    out_path = OUT_DIR / "drought_monitor.csv"
    save_drought_csv(current_scores, out_path)
    print("Done.\n")
    return out_path


if __name__ == "__main__":
    run()























