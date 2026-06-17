from __future__ import annotations
"""
process/tension.py
-------------------
Builds the Water Tension Index (WTI) — the independent variable.

    WTI = (supply_deficit_score + demand_pressure_score + seasonal_pinch_score) / 3

All components normalized 0–100; 100 = maximum stress.
Falls back to state-level seed values when live data is unavailable.

Tiers (used in map + analysis):
    Critical : WTI ≥ 75
    High     : WTI ≥ 55
    Moderate : WTI ≥ 35
    Low      : WTI ≥ 15
    Minimal  : WTI <  15

Outputs: data/processed/water_tension.csv
"""

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
SEED_DIR      = ROOT / "data" / "seed"
OUT_DIR       = ROOT / "data" / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

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

WTI_TIERS = [(75,"Critical"),(55,"High"),(35,"Moderate"),(15,"Low"),(0,"Minimal")]

def wti_tier(score: float) -> str:
    for threshold, label in WTI_TIERS:
        if score >= threshold:
            return label
    return "Minimal"


# ── Loaders ───────────────────────────────────────────────────────────────────

def load_csv(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            fips = str(row.pop("fips", "")).zfill(5)
            if fips and len(fips) == 5:
                data[fips] = row
    return data


def _f(val, default=None) -> float | None:
    if val is None or str(val).strip() in ("", "None", "nan"):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def load_state_ref() -> dict[str, dict]:
    p = SEED_DIR / "state_reference.json"
    if not p.exists():
        print("  ⚠  data/seed/state_reference.json not found — WTI will use hard defaults")
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f).get("states", {})


# ── Component A: Supply Deficit (0–100) ───────────────────────────────────────
# Higher = less supply relative to demand / historical norms

def supply_deficit(wri: dict, usgs: dict, reservoir: dict, drought: dict,
                   seed: dict) -> tuple[float, list[str]]:
    components: list[tuple[float, float]] = []  # (value, weight)
    sources: list[str] = []

    # 1. WRI Aqueduct baseline water stress (best long-run signal, weight 0.35)
    bws = _f(wri.get("wri_baseline_stress"))
    if bws is not None:
        components.append((bws, 0.35)); sources.append("WRI")
    else:
        v = _f(seed.get("supply_score"))
        if v is not None:
            components.append((v, 0.35)); sources.append("SEED_SUPPLY")

    # 2. USGS groundwater level anomaly (weight 0.30)
    gw = _f(usgs.get("usgs_gw_depletion"))
    if gw is not None:
        components.append((gw, 0.30)); sources.append("USGS_GW")

    # 3. USGS streamflow percentile — inverted (low flow = high stress) (weight 0.20)
    sf = _f(usgs.get("usgs_streamflow_pct"))
    if sf is not None:
        components.append((100 - sf, 0.20)); sources.append("USGS_SF")

    # 4. USBR reservoir storage — inverted (weight 0.15)
    res = _f(reservoir.get("usbr_storage_pct"))
    if res is not None:
        components.append((100 - res, 0.15)); sources.append("USBR")

    if not components:
        fallback = _f(seed.get("supply_score"), 30.0)
        return round(fallback, 1), ["SEED_ONLY"]

    total_w = sum(w for _, w in components)
    score   = sum(v * (w / total_w) for v, w in components)
    return round(min(100.0, max(0.0, score)), 1), sources


# ── Component B: Demand Pressure (0–100) ──────────────────────────────────────
# Higher = demand growing faster than supply can accommodate

def demand_pressure(controls: dict, seed: dict) -> tuple[float, list[str]]:
    dp    = _f(seed.get("demand_pressure"))
    trend = _f(seed.get("demand_trend"), 30.0)

    if dp is None:
        return 30.0, ["DEFAULT"]

    # Growth trend amplifies demand pressure slightly
    trend_factor = trend / 100.0
    score = dp * (1 + trend_factor * 0.25)
    return round(min(100.0, score), 1), ["SEED_DEMAND"]


# ── Component C: Seasonal Pinch (0–100) ───────────────────────────────────────
# Captures severity of worst-season supply crunch — distinct from long-run baseline

def seasonal_pinch(drought: dict, wri: dict, seed: dict) -> tuple[float, list[str]]:
    components: list[tuple[float, float]] = []
    sources: list[str] = []

    # 1. WRI seasonal variability score (weight 0.40)
    sev = _f(wri.get("wri_seasonal_variability"))
    if sev is not None:
        components.append((sev, 0.40)); sources.append("WRI_SEV")

    # 2. USDM historical drought frequency (weight 0.35)
    df = _f(drought.get("drought_frequency_score"))
    if df is not None:
        components.append((df, 0.35)); sources.append("USDM_HIST")

    # 3. USDM current drought score (weight 0.25)
    curr = _f(drought.get("usdm_current_score"))
    if curr is not None:
        components.append((curr, 0.25)); sources.append("USDM_CURRENT")

    if not components:
        fallback = _f(seed.get("supply_score"), 30.0) * 0.6
        return round(fallback, 1), ["SEED_SEASONAL"]

    total_w = sum(w for _, w in components)
    score   = sum(v * (w / total_w) for v, w in components)
    return round(min(100.0, max(0.0, score)), 1), sources


# ── Master assembly ───────────────────────────────────────────────────────────

def build_water_tension() -> Path:
    print("=== Building Water Tension Index ===\n")

    drought    = load_csv(PROCESSED_DIR / "drought_monitor.csv")
    usgs       = load_csv(PROCESSED_DIR / "usgs_water.csv")
    reservoirs = load_csv(PROCESSED_DIR / "reservoirs.csv")
    wri        = load_csv(PROCESSED_DIR / "wri_aqueduct.csv")
    controls   = load_csv(PROCESSED_DIR / "controls.csv")

    src_counts = dict(drought=len(drought), usgs=len(usgs),
                      reservoirs=len(reservoirs), wri=len(wri), controls=len(controls))
    print(f"  Loaded: {src_counts}")

    state_ref = load_state_ref()

    all_fips = set(controls.keys())
    for src in (drought, usgs, reservoirs, wri):
        all_fips.update(src.keys())
    all_fips = {
        f for f in all_fips
        if len(f) == 5 and f.isdigit() and f[:2] in _STATE_FIPS
    }
    print(f"  Computing WTI for {len(all_fips)} counties …\n")

    rows: list[dict] = []
    for fips in sorted(all_fips):
        abbr = _STATE_FIPS.get(fips[:2], "")
        seed = state_ref.get(abbr, {})

        d_row = drought.get(fips, {})
        u_row = usgs.get(fips, {})
        r_row = reservoirs.get(fips, {})
        w_row = wri.get(fips, {})
        c_row = controls.get(fips, {})

        supply, src_s = supply_deficit(w_row, u_row, r_row, d_row, seed)
        demand, src_d = demand_pressure(c_row, seed)
        seasonal, src_p = seasonal_pinch(d_row, w_row, seed)

        wti = round((supply + demand + seasonal) / 3.0, 2)
        wti = max(0.0, min(100.0, wti))

        all_sources = src_s + src_d + src_p
        live = sum(
            1 for s in all_sources
            if not any(tag in s for tag in ("SEED", "DEFAULT", "ONLY"))
        )
        completeness = round(live / len(all_sources), 3) if all_sources else 0.0

        rows.append({
            "fips":                  fips,
            "supply_deficit_score":  supply,
            "demand_pressure_score": demand,
            "seasonal_pinch_score":  seasonal,
            "wti":                   wti,
            "wti_tier":              wti_tier(wti),
            "wti_sources":           ",".join(sorted(set(all_sources))),
            "wti_completeness":      completeness,
        })

    # Write
    out_path = PROCESSED_DIR / "water_tension.csv"
    fieldnames = list(rows[0].keys()) if rows else []
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  Wrote {len(rows)} counties → {out_path}")

    # Distribution
    from collections import Counter
    tiers = Counter(r["wti_tier"] for r in rows)
    print("\n  WTI distribution:")
    for label in ("Critical","High","Moderate","Low","Minimal"):
        pct = tiers[label] / len(rows) * 100
        print(f"    {label:<10}: {tiers[label]:>5}  ({pct:.1f}%)")

    live_pct = sum(1 for r in rows if r["wti_completeness"] > 0) / len(rows) * 100
    print(f"\n  Counties with any live data: {live_pct:.0f}%")
    print("Done.\n")

    return out_path


if __name__ == "__main__":
    build_water_tension()
