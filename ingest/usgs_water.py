from __future__ import annotations
"""
ingest/usgs_water.py
---------------------
Pulls groundwater level anomalies and streamflow percentiles from the
USGS Water Services REST API.

Outputs: data/processed/usgs_water.csv

USGS API docs: https://waterservices.usgs.gov/rest/
- Site service: /nwis/site/
- Groundwater levels: /nwis/gwlevels/
- Instantaneous values (streamflow): /nwis/iv/
- Statistics: /nwis/stat/

Both endpoints are free; no API key required.

NOTE: USGS data is by monitoring site, not by county. This script:
1. Fetches all active groundwater sites nationally
2. For each site, gets recent water level vs. long-term median
3. Aggregates site-level anomalies to county FIPS by simple averaging
4. Separately fetches streamflow percentiles by FIPS-linked HUC
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)

USGS_BASE = "https://waterservices.usgs.gov/nwis"

def fetch_rdb(url: str, retries: int = 3) -> list[dict] | None:
    """
    Fetch USGS RDB (tab-delimited with # comments) format.
    Returns list of dicts, one per data row.
    """
    headers = {
        "User-Agent": "water-risk-pipeline/1.0 (pricephillips@example.com)",
        "Accept": "text/plain",
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as response:
                raw = response.read().decode("utf-8", errors="replace")
                # Parse RDB: skip # comment lines, then tab-delimited
                lines = [l for l in raw.split("\n") if not l.startswith("#") and l.strip()]
                if len(lines) < 2:
                    return []
                headers_row = lines[0].split("\t")
                # Second line is data type descriptors — skip
                data_rows = lines[2:]
                results = []
                for line in data_rows:
                    if not line.strip():
                        continue
                    parts = line.split("\t")
                    row = {headers_row[i]: parts[i] if i < len(parts) else "" for i in range(len(headers_row))}
                    results.append(row)
                return results
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code} on attempt {attempt+1}/{retries}: {url[:80]}")
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
        except Exception as e:
            print(f"  Error attempt {attempt+1}/{retries}: {e}")
            if attempt < retries - 1:
                time.sleep(2)
    print("  WARNING: RDB Fetch failed completely. Returning empty list.")
    return []

def fetch_json(url: str, retries: int = 3) -> dict | None:
    """Fetch USGS JSON response."""
    headers = {"User-Agent": "water-risk-pipeline/1.0 (pricephillips@example.com)", "Accept": "application/json"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code} attempt {attempt+1}: {url[:80]}")
            if attempt < retries - 1:
                time.sleep(2)
        except Exception as e:
            print(f"  Error attempt {attempt+1}: {e}")
            if attempt < retries - 1:
                time.sleep(2)
    print("  WARNING: JSON Fetch failed completely. Returning empty dict.")
    return {}

def fetch_active_groundwater_sites(state_abbr: str) -> list[dict]:
    """
    Get active groundwater monitoring sites for a state.
    Returns list of site metadata including county_cd and site_no.
    """
    url = (
        f"{USGS_BASE}/site/?format=rdb"
        f"&stateCd={state_abbr}"
        f"&siteType=GW"
        f"&siteStatus=active"
        f"&hasDataTypeCd=gw"
        f"&outputDataTypeCd=gw"
    )
    rows = fetch_rdb(url)
    if rows is None or len(rows) == 0:
        return []
    return rows

def fetch_groundwater_stats(state_abbr: str) -> list[dict]:
    """
    Fetch groundwater level statistics (percentile-based anomaly) for a state.

    Uses the USGS statistics service which provides median and percentile values.
    parameterCd=72019 = depth to water level below land surface
    """
    url = (
        f"{USGS_BASE}/stat/?format=rdb"
        f"&stateCd={state_abbr}"
        f"&siteType=GW"
        f"&parameterCd=72019"  # depth to water level
        f"&statReportType=annual"
        f"&statYearType=water"
    )
    rows = fetch_rdb(url)
    return rows or []

def fetch_groundwater_current(site_nos: list[str], chunk_size: int = 100) -> list[dict]:
    """
    Fetch most recent groundwater level measurements for a list of sites.
    Processes in chunks to avoid URL length limits.
    """
    all_results = []
    for i in range(0, len(site_nos), chunk_size):
        chunk = site_nos[i:i+chunk_size]
        sites_param = ",".join(chunk)
        url = (
            f"{USGS_BASE}/gwlevels/?format=json"
            f"&sites={sites_param}"
            f"&period=P365D"  # last year of data
            f"&parameterCd=72019"
        )
        data = fetch_json(url)
        if data:
            ts_list = data.get("value", {}).get("timeSeries", [])
            all_results.extend(ts_list)
        time.sleep(0.5)  # rate limiting courtesy

    return all_results

def fetch_streamflow_percentiles_by_state(state_abbr: str) -> list[dict]:
    """
    Fetch current streamflow as percentile of historical record.
    parameterCd=00060 = discharge (cubic feet/second)
    Uses statistics service to get current vs. historical percentile.
    """
    url = (
        f"{USGS_BASE}/stat/?format=rdb"
        f"&stateCd={state_abbr}"
        f"&siteType=ST"
        f"&parameterCd=00060"
        f"&statReportType=daily"
        f"&statYearType=water"
    )
    rows = fetch_rdb(url)
    return rows or []

def aggregate_gw_to_county(gw_records: list[dict]) -> dict[str, dict]:
    """
    Aggregate groundwater level anomalies from site-level to county FIPS.

    Anomaly scoring:
    - site has depth_to_water data + historical percentile
    - if current level is below 25th percentile → stressed
    - aggregate site-level stress to county median

    Returns: {fips: {'usgs_gw_depletion': 0–100, 'usgs_gw_site_count': int}}
    """
    county_data = defaultdict(list)

    for record in gw_records:
        # USGS JSON time series records
        site_info = record.get("sourceInfo", {})
        site_props = {p["name"]: p["value"] for p in site_info.get("siteProperty", [])}

        county_cd = site_props.get("countyCd", "")
        state_cd = site_props.get("stateCd", "")
        if not county_cd or not state_cd:
            continue

        fips = f"{state_cd.zfill(2)}{county_cd.zfill(3)}"

        # Get most recent value
        values = record.get("values", [{}])[0].get("value", [])
        if not values:
            continue

        most_recent = values[-1]

        if not isinstance(most_recent, dict):
            continue

        qualifier = most_recent.get("qualifiers", [])

        # USGS qualifiers for groundwater: P = approved, A = provisional
        # The key is whether we have a percentile comparison
        # Without real-time percentile service, use qualitative flags:
        # qualifier 'Ice' = frozen, skip; '>>>' = above datum
        val = most_recent.get("value", "")
        try:
            depth = float(val)
        except (ValueError, TypeError):
            continue

        county_data[fips].append(depth)

    # Normalize depth aggregates to anomaly score (placeholder — full implementation
    # requires the historical median per site, fetched separately)
    results = {}
    for fips, depths in county_data.items():
        if depths:
            results[fips] = {
                "usgs_gw_site_count": len(depths),
                "usgs_gw_mean_depth": round(sum(depths) / len(depths), 1),
                # Full depletion score requires historical comparison; set null for now
                "usgs_gw_depletion": None,
            }

    return results

def aggregate_streamflow_to_county(sf_records: list[dict]) -> dict[str, dict]:
    """
    Aggregate streamflow percentile records to county FIPS.

    USGS statistics RDB fields include: site_no, county_cd, state_cd,
    month_nu, day_nu, mean_va, p50_va (median), p25_va, p75_va

    Maps current streamflow vs. median to an inverted stress score:
    - At or above median → low stress
    - Below 25th percentile → high stress

    Returns: {fips: {'usgs_streamflow_pct': 0–100 (higher = more stressed)}}
    """
    results = {}
    # This requires joining with real-time IV data; the stats endpoint gives
    # historical context. Full implementation cross-joins current IV with
    # historical stats. Returning structure for build.py to populate.
    for row in sf_records:
        site_no = row.get("site_no", "")
        county_cd = row.get("county_cd", "")
        state_cd = row.get("state_cd", "")
        if not county_cd or not state_cd:
            continue
        fips = f"{state_cd.zfill(2)}{county_cd.zfill(3)}"
        if fips not in results:
            results[fips] = {"usgs_sf_site_count": 0}
        results[fips]["usgs_sf_site_count"] += 1

    return results

def run(states: list[str] | None = None):
    """
    Run USGS ingestion for specified states, or all states if None.

    For full national run (~50 states), budget 10–20 minutes of runtime
    and ensure respectful rate limiting is active.
    """
    print("=== USGS Water Data Ingestion ===")

    if states is None:
        states = [
            "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA",
            "HI","ID","IL","IN","IA","KS","KY","LA","ME","MD",
            "MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
            "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC",
            "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY",
        ]

    all_gw: dict[str, dict] = {}
    all_sf: dict[str, dict] = {}

    for state in states:
        print(f"  Processing {state} ...")

        # Groundwater sites
        gw_sites = fetch_active_groundwater_sites(state)
        if gw_sites:
            site_nos = [s.get("site_no", "") for s in gw_sites if s.get("site_no")]
            site_nos = [s for s in site_nos if s][:200]  # cap at 200 per state

            if site_nos:
                gw_ts = fetch_groundwater_current(site_nos, chunk_size=50)
                county_gw = aggregate_gw_to_county(gw_ts)
                all_gw.update(county_gw)
                print(f"    GW: {len(gw_ts)} sites → {len(county_gw)} counties")

        # Streamflow
        sf_records = fetch_streamflow_percentiles_by_state(state)
        if sf_records:
            county_sf = aggregate_streamflow_to_county(sf_records)
            all_sf.update(county_sf)
            print(f"    SF: {len(sf_records)} records → {len(county_sf)} counties")

        time.sleep(1.0)  # rate limiting

    # Merge and save
    all_fips = set(all_gw.keys()) | set(all_sf.keys())
    fieldnames = [
        "fips",
        "usgs_gw_site_count",
        "usgs_gw_mean_depth",
        "usgs_gw_depletion",
        "usgs_sf_site_count",
        "usgs_streamflow_pct",
    ]

    out_path = OUT_DIR / "usgs_water.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for fips in sorted(all_fips):
            row = {"fips": fips}
            row.update(all_gw.get(fips, {}))
            row.update(all_sf.get(fips, {}))
            writer.writerow(row)

    print(f"  Saved {len(all_fips)} counties → {out_path}")
    print("Done.\n")
    return out_path

if __name__ == "__main__":
    # Run on a subset of high-priority states first for testing
    test_states = sys.argv[1:] if len(sys.argv) > 1 else ["TX", "AZ", "NV", "CA", "VA", "GA"]
    run(test_states)
