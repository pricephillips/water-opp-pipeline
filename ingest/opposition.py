from __future__ import annotations
"""
ingest/opposition.py
---------------------
Pulls master_opposition.csv from GitHub and aggregates to county-level
dependent variables.

Key decisions:
    - County FIPS resolved via (county_name, state_abbr) → FIPS lookup
      built from the tonmcg 2024 election dataset.
    - ALL incidents counted (not just data centers) — this is a general
      water tension vs. development opposition project.
    - Water-specific counts isolated via Issue Category containing "water".
    - Severity: 1 = standard, 2 = high-severity (weighted ×2 in score).
    - Rows with national/multi-state scope, no county, or missing state excluded.

Outputs: data/processed/opposition_by_county.csv
"""

import csv
import io
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)

OPPOSITION_URL = (
    "https://raw.githubusercontent.com/pricephillips/data-center-map"
    "/main/master_opposition.csv"
)
ELECTION_URL = (
    "https://raw.githubusercontent.com/tonmcg/US_County_Level_Election_Results_08-24"
    "/master/2024_US_County_Level_Presidential_Results.csv"
)

WATER_KEYWORD = "water"
SKIP_SCOPES   = {"national", "multi-state", "federal", "multi_state"}
SKIP_STATES   = {"US", ""}

# State name → abbreviation
_ABBR: dict[str, str] = {
    "Alabama":"AL","Alaska":"AK","Arizona":"AZ","Arkansas":"AR","California":"CA",
    "Colorado":"CO","Connecticut":"CT","Delaware":"DE","Florida":"FL","Georgia":"GA",
    "Hawaii":"HI","Idaho":"ID","Illinois":"IL","Indiana":"IN","Iowa":"IA","Kansas":"KS",
    "Kentucky":"KY","Louisiana":"LA","Maine":"ME","Maryland":"MD","Massachusetts":"MA",
    "Michigan":"MI","Minnesota":"MN","Mississippi":"MS","Missouri":"MO","Montana":"MT",
    "Nebraska":"NE","Nevada":"NV","New Hampshire":"NH","New Jersey":"NJ","New Mexico":"NM",
    "New York":"NY","North Carolina":"NC","North Dakota":"ND","Ohio":"OH","Oklahoma":"OK",
    "Oregon":"OR","Pennsylvania":"PA","Rhode Island":"RI","South Carolina":"SC",
    "South Dakota":"SD","Tennessee":"TN","Texas":"TX","Utah":"UT","Vermont":"VT",
    "Virginia":"VA","Washington":"WA","West Virginia":"WV","Wisconsin":"WI","Wyoming":"WY",
    "District of Columbia":"DC",
}

def _abbr(name: str) -> str:
    return _ABBR.get(name.strip(), name.strip().upper()[:2] if len(name.strip()) >= 2 else "")


# ── HTTP helper ───────────────────────────────────────────────────────────────

def fetch_csv(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "water-opp-pipeline/2.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        content = r.read().decode("utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(content)))


# ── FIPS lookup ───────────────────────────────────────────────────────────────

def build_fips_lookup(election_rows: list[dict]) -> dict[tuple[str, str], str]:
    """
    Returns {(normalized_county_name, state_abbr): fips}
    Multiple normalization variants are indexed for robust matching.
    """
    lookup: dict[tuple[str, str], str] = {}
    suffixes = (" county", " parish", " borough", " census area",
                " municipality", " city and county", " city")

    for row in election_rows:
        fips  = str(row.get("county_fips", "")).zfill(5)
        name  = row.get("county_name", "").strip().lower()
        state = _abbr(row.get("state_name", ""))
        if not (fips and name and state):
            continue

        lookup[(name, state)] = fips
        for sfx in suffixes:
            if name.endswith(sfx):
                lookup[(name[: -len(sfx)], state)] = fips
                break
    return lookup


def resolve_fips(county: str, state: str,
                 lookup: dict[tuple[str, str], str]) -> str | None:
    if not county or not state:
        return None
    state  = state.strip().upper()
    county = county.strip().lower()

    # Direct
    if (county, state) in lookup:
        return lookup[(county, state)]

    # Strip suffix
    for sfx in (" county", " parish", " borough", " census area",
                " city and county", " municipality"):
        s = county.replace(sfx, "").strip()
        if (s, state) in lookup:
            return lookup[(s, state)]
        if (s + " county", state) in lookup:
            return lookup[(s + " county", state)]

    # Saint / St. normalisation
    alt = county.replace("st.", "saint").replace("ste.", "sainte")
    if (alt, state) in lookup:
        return lookup[(alt, state)]

    return None


# ── Aggregation ───────────────────────────────────────────────────────────────

def aggregate(opp_rows: list[dict],
              fips_lookup: dict[tuple[str, str], str]) -> dict[str, dict]:
    county: dict[str, dict] = defaultdict(lambda: {
        "opp_count": 0,
        "opp_water_count": 0,
        "opp_severity_score": 0.0,
        "opp_sev2_count": 0,
        "dates": [],
    })

    matched = unmatched = skipped = 0

    for row in opp_rows:
        scope  = row.get("Scope", "").strip().lower()
        state  = row.get("State", "").strip()
        county_name = row.get("County", "").strip()

        if scope in SKIP_SCOPES or state in SKIP_STATES:
            skipped += 1
            continue

        fips = resolve_fips(county_name, state, fips_lookup)
        if not fips:
            unmatched += 1
            continue

        matched += 1

        try:
            sev = max(1, min(2, int(float(row.get("Severity", "1") or 1))))
        except (ValueError, TypeError):
            sev = 1

        issue_cats = {c.strip().lower() for c in row.get("Issue Category", "").split(";")}
        water_flag = WATER_KEYWORD in issue_cats

        county[fips]["opp_count"]          += 1
        county[fips]["opp_severity_score"] += sev
        if sev == 2:
            county[fips]["opp_sev2_count"] += 1
        if water_flag:
            county[fips]["opp_water_count"] += 1

        date_str = row.get("Date", "").strip()
        if date_str:
            county[fips]["dates"].append(date_str)

    print(f"  Matched: {matched}  |  Unmatched: {unmatched}  |  Skipped: {skipped}")

    results: dict[str, dict] = {}
    for fips, d in county.items():
        count   = d["opp_count"]
        water   = d["opp_water_count"]
        dates   = sorted(d["dates"])
        results[fips] = {
            "opp_count":           count,
            "opp_water_count":     water,
            "opp_water_pct":       round(water / count * 100, 1) if count > 0 else 0.0,
            "opp_severity_score":  round(d["opp_severity_score"], 1),
            "opp_sev2_count":      d["opp_sev2_count"],
            "earliest_incident":   dates[0]  if dates else "",
            "latest_incident":     dates[-1] if dates else "",
        }
    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> Path:
    print("=== Opposition Data Ingestion ===")

    print("  Fetching master_opposition.csv …")
    opp_rows = fetch_csv(OPPOSITION_URL)
    print(f"  {len(opp_rows)} rows loaded")

    print("  Fetching county FIPS lookup …")
    election_rows = fetch_csv(ELECTION_URL)
    fips_lookup   = build_fips_lookup(election_rows)
    print(f"  {len(fips_lookup)} name→FIPS mappings built")

    print("  Aggregating …")
    county_opp = aggregate(opp_rows, fips_lookup)

    water_counties = sum(1 for d in county_opp.values() if d["opp_water_count"] > 0)
    print(f"  {len(county_opp)} counties with ≥1 incident  "
          f"({water_counties} with water-flagged incidents)")

    fieldnames = [
        "fips",
        "opp_count", "opp_water_count", "opp_water_pct",
        "opp_severity_score", "opp_sev2_count",
        "earliest_incident", "latest_incident",
    ]
    out_path = OUT_DIR / "opposition_by_county.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for fips in sorted(county_opp.keys()):
            writer.writerow({"fips": fips, **county_opp[fips]})

    print(f"  Saved → {out_path}")
    print("Done.\n")
    return out_path


if __name__ == "__main__":
    run()
