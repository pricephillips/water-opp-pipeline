from __future__ import annotations
"""
ingest/drought_monitor.py
--------------------------
Pulls county-level drought statistics from the US Drought Monitor (USDM).

Outputs: data/processed/drought_monitor.csv

USDM API documentation: https://droughtmonitor.unl.edu/DmData/DataTables.aspx

The USDM publishes weekly snapshots every Tuesday. This script:
  1. Fetches the most recent Tuesday's county statistics
  2. Fetches the 10-year historical county statistics for drought frequency
  3. Outputs both to processed/

Free API, no key required.
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)

USDM_API = "https://usdmdataservices.unl.edu/api/CountyStatistics"

# USDM drought category → stress score mapping
CATEGORY_SCORE = {
    "None": 0,
    "D0": 20,
    "D1": 40,
    "D2": 60,
    "D3": 80,
    "D4": 100,
}


def last_tuesday() -> date:
    """Return the most recent Tuesday (USDM release day)."""
    today = date.today()
    days_since_tuesday = (today.weekday() - 1) % 7
    return today - timedelta(days=days_since_tuesday)


def fetch_json(url: str, retries: int = 3, delay: float = 2.0) -> list | dict | None:
    """Fetch JSON from URL with retry logic."""
    headers = {
        "User-Agent": "water-risk-pipeline/1.0 (THG research; contact: research@hawthorngroupdc.com)",
        "Accept": "application/json",
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code} on attempt {attempt+1}/{retries}: {url}")
            if e.code == 429:
                time.sleep(delay * 3)
            elif attempt < retries - 1:
                time.sleep(delay)
        except Exception as e:
            print(f"  Error on attempt {attempt+1}/{retries}: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return None


def fetch_current_county_drought(snapshot_date: date | None = None) -> list[dict]:
    """
    Fetch current USDM county statistics for a given snapshot date.

    Returns list of dicts, one per county:
        {
            'MapDate': '20260617',
            'FIPS': '48453',
            'County': 'Travis',
            'State': 'TX',
            'None': 55.2,   # % of county area in each category
            'D0': 22.1,
            'D1': 12.5,
            'D2': 8.0,
            'D3': 2.2,
            'D4': 0.0,
        }
    """
    if snapshot_date is None:
        snapshot_date = last_tuesday()

    start_str = snapshot_date.strftime("%Y-%m-%d")
    end_str = snapshot_date.strftime("%Y-%m-%d")

    url = (
        f"{USDM_API}/GetDroughtSeverityStatisticsByAreaPercent"
        f"?aoi=county&startdate={start_str}&enddate={end_str}&statisticsType=1"
    )
    print(f"Fetching USDM current conditions: {snapshot_date} ...")
    data = fetch_json(url)

    if data is None:
        print("  WARNING: USDM current fetch failed. Returning empty.")
        return []

    print(f"  Got {len(data)} county records.")
    return data


def fetch_historical_county_drought(
    start_year: int = 2015, end_year: int | None = None
) -> list[dict]:
    """
    Fetch annual summary statistics for drought frequency calculation.

    Uses USDM comprehensive statistics endpoint to get % weeks in drought
    per county over the specified period.

    Returns list of dicts aggregated to county level with:
        'FIPS', 'state', 'county', 'pct_weeks_d1plus', 'pct_weeks_d3d4_peak'
    """
    if end_year is None:
        end_year = date.today().year - 1

    # USDM statistics endpoint: county-level annual summaries
    url = (
        f"{USDM_API}/GetDroughtSeverityStatisticsByAreaPercent"
        f"?aoi=county"
        f"&startdate={start_year}-01-01"
        f"&enddate={end_year}-12-31"
        f"&statisticsType=2"  # statisticsType=2 → weekly frequency stats
    )
    print(f"Fetching USDM historical ({start_year}–{end_year}) ...")
    data = fetch_json(url)

    if data is None:
        print("  WARNING: USDM historical fetch failed.")
        return []

    print(f"  Got {len(data)} records.")
    return data


def compute_current_scores(records: list[dict]) -> dict[str, dict]:
    """
    Convert raw USDM percentage records to county-level stress scores.

    Args:
        records: list from fetch_current_county_drought()

    Returns:
        {fips: {usdm_current_score, usdm_pct_d0_plus, usdm_pct_d2_plus,
                usdm_current_category, usdm_as_of}}
    """
    results = {}
    for row in records:
        raw_fips = str(row.get("FIPS", "")).zfill(5)
        if not raw_fips or raw_fips == "00000":
            continue

        # Determine dominant category
        pcts = {
            cat: float(row.get(cat, 0) or 0)
            for cat in ["D4", "D3", "D2", "D1", "D0", "None"]
        }
        dominant = "None"
        for cat in ["D4", "D3", "D2", "D1", "D0"]:
            if pcts.get(cat, 0) > 10:  # >10% of county in this category
                dominant = cat
                break

        pct_d0_plus = sum(pcts.get(c, 0) for c in ["D0", "D1", "D2", "D3", "D4"])
        pct_d2_plus = sum(pcts.get(c, 0) for c in ["D2", "D3", "D4"])

        # Weighted score: each category weighted by % of county area
        weighted = sum(
            CATEGORY_SCORE[cat] * (pcts.get(cat, 0) / 100)
            for cat in CATEGORY_SCORE
            if cat != "None"
        )
        # Also weight the "None" area as 0
        # weighted is already a 0–100 value weighted by area coverage

        results[raw_fips] = {
            "usdm_current_category": dominant,
            "usdm_current_score": round(weighted, 1),
            "usdm_pct_d0_plus": round(pct_d0_plus, 1),
            "usdm_pct_d2_plus": round(pct_d2_plus, 1),
            "usdm_as_of": row.get("MapDate", ""),
        }

    return results


def compute_historical_scores(records: list[dict]) -> dict[str, dict]:
    """
    Aggregate historical records to per-county drought frequency scores.

    Returns:
        {fips: {drought_frequency_score, usdm_peak_drought_d3d4_pct}}
    """
    from collections import defaultdict

    county_weeks = defaultdict(lambda: {"total": 0, "d1plus": 0, "d3d4": 0})

    for row in records:
        raw_fips = str(row.get("FIPS", "")).zfill(5)
        if not raw_fips or raw_fips == "00000":
            continue
        county_weeks[raw_fips]["total"] += 1
        pct_d1plus = sum(float(row.get(c, 0) or 0) for c in ["D1", "D2", "D3", "D4"])
        pct_d3d4 = sum(float(row.get(c, 0) or 0) for c in ["D3", "D4"])
        if pct_d1plus > 50:
            county_weeks[raw_fips]["d1plus"] += 1
        if pct_d3d4 > 25:
            county_weeks[raw_fips]["d3d4"] += 1

    results = {}
    for fips, counts in county_weeks.items():
        total = counts["total"]
        if total == 0:
            continue
        drought_freq = round((counts["d1plus"] / total) * 100, 1)
        d3d4_pct = round((counts["d3d4"] / total) * 100, 1)
        results[fips] = {
            "drought_frequency_score": drought_freq,
            "usdm_peak_drought_d3d4_pct": d3d4_pct,
        }

    return results


def save_drought_csv(current: dict, historical: dict, out_path: Path):
    """Write merged drought scores to CSV."""
    all_fips = set(current.keys()) | set(historical.keys())

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

    rows = []
    for fips in sorted(all_fips):
        row = {"fips": fips}
        row.update(current.get(fips, {}))
        row.update(historical.get(fips, {}))
        rows.append(row)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"  Saved {len(rows)} county rows → {out_path}")


def run():
    print("=== USDM Drought Monitor Ingestion ===")

    snapshot = last_tuesday()
    print(f"Using snapshot date: {snapshot}")

    current_raw = fetch_current_county_drought(snapshot)
    historical_raw = fetch_historical_county_drought(2015)

    current_scores = compute_current_scores(current_raw)
    historical_scores = compute_historical_scores(historical_raw)

    out_path = OUT_DIR / "drought_monitor.csv"
    save_drought_csv(current_scores, historical_scores, out_path)
    print("Done.\n")

    return out_path


if __name__ == "__main__":
    run()
