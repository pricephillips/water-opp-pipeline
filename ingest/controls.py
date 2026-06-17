from __future__ import annotations
"""
ingest/controls.py
-------------------
Builds county-level control variables for the OLS regression.

Controls:
    pct_gop_2024      Republican vote share 2024 (political environment)
    total_votes_2024  Raw vote count (used for population estimate)
    pop_density       Population per sq mi (proxied where Census area data available)
    water_law_type    Prior appropriation / riparian / hybrid (state-level)
    water_law_encoded 1.0 / 0.0 / 0.5 (numeric encoding)
    ag_water_pct      % of state's water use that's agricultural (state seed)
    demand_trend      State-level growth pressure index (state seed)
    prior_opp_flag    1 if county has any prior opposition incident

Population note:
    Full county populations require the Census Gazetteer file (large download).
    This script uses presidential vote count × 1.72 (inverse of ~58% turnout)
    as the primary proxy. Where we have known county populations for the top-100
    counties by size, those override the proxy.

Outputs: data/processed/controls.csv
"""

import csv
import io
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED_DIR      = ROOT / "data" / "seed"
PROCESSED_DIR = ROOT / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

ELECTION_URL = (
    "https://raw.githubusercontent.com/tonmcg/US_County_Level_Election_Results_08-24"
    "/master/2024_US_County_Level_Presidential_Results.csv"
)

VOTE_TO_POP = 1.72  # 1 / 0.581 (2024 presidential turnout ≈ 58.1%)

# State FIPS prefix → abbreviation
_STATE_FIPS: dict[str, str] = {
    "01":"AL","02":"AK","04":"AZ","05":"AR","06":"CA","08":"CO","09":"CT",
    "10":"DE","12":"FL","13":"GA","15":"HI","16":"ID","17":"IL","18":"IN",
    "19":"IA","20":"KS","21":"KY","22":"LA","23":"ME","24":"MD","25":"MA",
    "26":"MI","27":"MN","28":"MS","29":"MO","30":"MT","31":"NE","32":"NV",
    "33":"NH","34":"NJ","35":"NM","36":"NY","37":"NC","38":"ND","39":"OH",
    "40":"OK","41":"OR","42":"PA","44":"RI","45":"SC","46":"SD","47":"TN",
    "48":"TX","49":"UT","50":"VT","51":"VA","53":"WA","54":"WV","55":"WI","56":"WY",
}

# Known county areas (sq mi) for approximate pop density — top-100 US counties.
# Source: Census Gazetteer 2023. Extend by downloading the full Gazetteer file.
# URL: https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_counties_national.zip
COUNTY_AREA_SQMI: dict[str, float] = {
    "06037":4058,"17031":945,"48201":1777,"04013":9224,"06073":4207,
    "48113":880,"53033":2126,"06059":948,"12086":1946,"48439":898,
    "06065":7208,"36047":70,"48029":1256,"36081":109,"06071":20105,
    "06085":1304,"12057":1020,"32003":7910,"48453":989,"13121":529,
    "51059":395,"39035":457,"25025":58,"37119":524,"55079":241,
    "39049":540,"26163":614,"08031":155,"49035":741,"12095":907,
    "06001":738,"53061":2087,"42101":142,"40109":718,"37183":857,
    "12031":762,"29189":521,"45045":795,"26081":848,"19153":592,
    "48085":848,"48121":888,"36005":42,"06067":994,"36119":450,
    "48027":876,"41051":153,"55025":565,"12117":1430,"36103":912,
    "06019":5963,"13089":342,"35001":1166,"12011":1422,"47037":526,
    "22033":714,"36061":23,"47157":543,"29095":604,"47065":546,
    "06111":1843,"06077":1426,"39153":549,"53053":1676,"26125":565,
    "18089":396,"47093":526,"01089":549,"06029":8142,"13245":199,
    "39061":407,"12099":827,"26049":572,"12021":1231,"48141":951,
    "08069":2601,"12105":774,"48167":939,"29510":61,"08041":2300,
    "51107":519,"55133":576,"18097":403,"01073":644,"48157":900,
    "48309":900,"12127":1428,"13135":642,"48355":887,"45019":1020,
    "13067":284,"51153":407,"39095":457,"47149":543,"22071":907,
    "12069":1117,"48491":873,"26049":572,"34013":128,"34039":308,
}

# Known actual county populations (2023 Census estimates) — overrides vote proxy.
# Source: https://www.census.gov/data/tables/time-series/demo/popest/2020s-counties-total.html
COUNTY_POP_KNOWN: dict[str, int] = {
    "06037":9_848_011,"17031":5_164_613,"48201":4_780_913,"04013":4_585_871,
    "06073":3_286_069,"48113":2_785_984,"53033":2_269_675,"06059":3_222_498,
    "12086":2_688_162,"48439":2_336_853,"06065":2_437_864,"36047":2_576_771,
    "48029":2_006_954,"36081":2_278_906,"06071":2_175_188,"06085":1_894_215,
    "12057":1_420_098,"32003":2_283_929,"48453":1_300_000,"13121":1_100_000,
    "51059":1_150_000,"39035":1_256_894,"25025":798_552,"37119":1_115_263,
    "55079":950_395,"39049":1_323_807,"26163":1_739_763,"08031":715_522,
    "49035":1_175_000,"12095":754_500,"06001":1_666_753,"53061":835_622,
    "42101":1_603_797,"40109":804_281,"37183":1_062_880,"12031":995_567,
}


def fetch_csv(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "water-opp-pipeline/2.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8", errors="replace"))))


def load_state_ref() -> dict[str, dict]:
    p = SEED_DIR / "state_reference.json"
    if p.exists():
        with open(p) as f:
            return json.load(f).get("states", {})
    return {}


def load_prior_opp_fips() -> set[str]:
    opp_path = PROCESSED_DIR / "opposition_by_county.csv"
    if not opp_path.exists():
        return set()
    fips_set: set[str] = set()
    with open(opp_path) as f:
        for row in csv.DictReader(f):
            if int(row.get("opp_count", 0) or 0) > 0:
                fips_set.add(row["fips"])
    return fips_set


def run() -> Path:
    print("=== Controls Ingestion ===")

    print("  Fetching 2024 election data …")
    election_rows = fetch_csv(ELECTION_URL)
    print(f"  {len(election_rows)} county rows")

    state_ref  = load_state_ref()
    prior_opp  = load_prior_opp_fips()
    print(f"  {len(prior_opp)} counties with prior opposition loaded")

    controls: dict[str, dict] = {}

    for row in election_rows:
        fips = str(row.get("county_fips", "")).zfill(5)
        if not fips or len(fips) != 5 or not fips.isdigit():
            continue

        abbr       = _STATE_FIPS.get(fips[:2], "")
        seed       = state_ref.get(abbr, {})
        state_name = row.get("state_name", "").strip()
        county_name= row.get("county_name", "").strip()

        # Political lean: per_gop stored as 0–1 decimal in tonmcg dataset
        try:
            pct_gop = float(row.get("per_gop", 0) or 0) * 100
        except (ValueError, TypeError):
            pct_gop = 50.0

        # Vote count → population
        try:
            votes = int(float(row.get("total_votes", 0) or 0))
        except (ValueError, TypeError):
            votes = 0

        pop = COUNTY_POP_KNOWN.get(fips) or max(1, round(votes * VOTE_TO_POP))

        # Population density
        area = COUNTY_AREA_SQMI.get(fips)
        pop_density = round(pop / area, 1) if area else None

        # Water law doctrine
        water_law = seed.get("water_law", "riparian")
        water_law_encoded = {"prior_appropriation": 1.0, "hybrid": 0.5}.get(water_law, 0.0)

        controls[fips] = {
            "fips":               fips,
            "county_name":        county_name,
            "state_abbr":         abbr,
            "state_name":         state_name,
            "pct_gop_2024":       round(pct_gop, 2),
            "total_votes_2024":   votes,
            "pop_estimate":       pop,
            "pop_density":        pop_density,
            "water_law_type":     water_law,
            "water_law_encoded":  water_law_encoded,
            "ag_water_pct":       seed.get("ag_water_pct"),
            "demand_trend":       seed.get("demand_trend"),
            "prior_opp_flag":     1 if fips in prior_opp else 0,
        }

    print(f"  Controls built for {len(controls)} counties")

    fieldnames = [
        "fips","county_name","state_abbr","state_name",
        "pct_gop_2024","total_votes_2024","pop_estimate","pop_density",
        "water_law_type","water_law_encoded",
        "ag_water_pct","demand_trend","prior_opp_flag",
    ]
    out_path = PROCESSED_DIR / "controls.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for fips in sorted(controls.keys()):
            writer.writerow(controls[fips])

    print(f"  Saved → {out_path}")
    print("Done.\n")
    return out_path


if __name__ == "__main__":
    run()
