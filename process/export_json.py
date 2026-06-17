from __future__ import annotations
"""
process/export_json.py
-----------------------
Converts master_analysis.csv → water_opp_map.json for the Leaflet map.

Bivariate color grid (3×3) — matches the HTML legend exactly:
  columns = WTI band (0=low < 35, 1=mid 35–65, 2=high ≥ 65)
  rows    = opposition rate band (0=none, 1=low 0–0.5/100k, 2=high ≥ 0.5/100k)

  Grid (col=WTI, row=opp), read bottom-left to top-right in legend:
                Low WTI    Mid WTI    High WTI
  High opp  :  #6b46c1    #9b2c2c    #c53030
  Mid opp   :  #553c9a    #744210    #c05621
  Low/no opp:  #1e2433    #1e4d7a    #1a365d

These colors are defined in the HTML legend grid and are the canonical reference.
"""

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "output"

# ── Bivariate color map — (wti_band, opp_band) → hex color ───────────────────
# wti_band : 0 = WTI < 35,  1 = 35–65,  2 = ≥ 65
# opp_band : 0 = 0/100k,    1 = 0–0.5,  2 = ≥ 0.5
BIVARIATE_COLORS: dict[tuple[int, int], str] = {
    (0, 0): "#1e2433",  # quiet:         low tension + no opp
    (1, 0): "#1e4d7a",  # latent-mid:    mid tension + no opp
    (2, 0): "#1a365d",  # latent-high:   HIGH tension + no opp  ← "latent_risk"
    (0, 1): "#553c9a",  # other-low:     low tension + some opp
    (1, 1): "#744210",  # mixed:         mid tension + some opp
    (2, 1): "#c05621",  # water-mid:     high tension + some opp
    (0, 2): "#6b46c1",  # other-high:    low tension + HIGH opp  ← "other_drivers"
    (1, 2): "#9b2c2c",  # mixed-high:    mid tension + high opp
    (2, 2): "#c53030",  # water-driven:  HIGH tension + HIGH opp ← "water_driven"
}

# WTI band thresholds
WTI_MID  = 35.0
WTI_HIGH = 65.0

# Opposition rate band thresholds (per 100k)
OPP_LOW  = 0.0    # > this = low band
OPP_HIGH = 0.50   # ≥ this = high band

# Fields included in the map JSON
# (compact subset of master_analysis.csv; large enough for all map views)
MAP_FIELDS = [
    "fips", "county_name", "state_abbr", "state_name",
    # IV
    "wti", "wti_tier",
    "supply_deficit_score", "demand_pressure_score", "seasonal_pinch_score",
    "wti_completeness", "wti_sources",
    # DV
    "opp_count", "opp_water_count", "opp_water_pct",
    "opp_rate_per_100k", "opp_water_rate_per_100k",
    # Controls (for info panel)
    "pct_gop_2024", "pop_density", "water_law_encoded", "ag_water_pct",
    "pop_estimate",
    # Analysis outputs
    "m2_residual", "residual_quadrant",
    # Computed by this script (not in master_analysis.csv)
    # wti_band, opp_band, bivariate_color, bivariate_cell — added below
]


def _coerce(val: str):
    """Cast CSV strings to appropriate Python scalars."""
    if val is None or str(val).strip() in ("", "None", "nan"):
        return None
    try:
        f = float(val)
        # Return int for values that are exact integers and not FIPS-like
        if f == int(f) and abs(f) < 1e9:
            s = str(val)
            if "." not in s:
                return int(f)
        return round(f, 6)
    except ValueError:
        return val


def wti_band(wti: float | None) -> int:
    if wti is None:
        return 1
    if wti >= WTI_HIGH:
        return 2
    if wti >= WTI_MID:
        return 1
    return 0


def opp_band(rate: float | None) -> int:
    if rate is None or rate <= 0:
        return 0
    if rate >= OPP_HIGH:
        return 2
    return 1


def build_json():
    csv_path = OUT_DIR / "master_analysis.csv"
    if not csv_path.exists():
        print(f"master_analysis.csv not found — run: python build.py --analyze-only")
        return

    lookup: dict[str, dict] = {}

    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            fips = row.get("fips", "").zfill(5)
            if not fips or len(fips) != 5:
                continue

            entry: dict = {}

            # Copy MAP_FIELDS that exist in master_analysis.csv
            for field in MAP_FIELDS:
                if field in row:
                    entry[field] = _coerce(row[field])

            # Compute bivariate bands and color
            wb = wti_band(_coerce(row.get("wti", "")))
            ob = opp_band(_coerce(row.get("opp_rate_per_100k", "")))
            entry["wti_band"]       = wb
            entry["opp_band"]       = ob
            entry["bivariate_color"]= BIVARIATE_COLORS[(wb, ob)]
            entry["bivariate_cell"] = wb * 3 + ob  # 0–8

            # Ensure FIPS is stored as zero-padded string, not int
            entry["fips"] = fips

            lookup[fips] = entry

    # Write
    out_path = OUT_DIR / "water_opp_map.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(lookup, f, separators=(",", ":"))

    size_kb = out_path.stat().st_size / 1024
    print(f"  Wrote water_opp_map.json  ({size_kb:.0f} KB, {len(lookup)} counties)")

    # Sanity check — all 9 bivariate colors should appear
    from collections import Counter
    color_counts = Counter(v["bivariate_color"] for v in lookup.values())
    missing = set(BIVARIATE_COLORS.values()) - set(color_counts.keys())
    if missing:
        print(f"  ⚠  Bivariate colors not represented: {missing}")
        print(f"     (expected with seed-only data; will populate after WRI download)")

    # Quadrant summary
    quad_counts = Counter(v.get("residual_quadrant", "?") for v in lookup.values())
    print(f"\n  Quadrant counts:")
    for q in ("water_driven", "latent_risk", "other_drivers", "quiet"):
        print(f"    {q:<18}: {quad_counts.get(q, 0):>5}")

    print("Done.\n")


if __name__ == "__main__":
    build_json()
