#!/usr/bin/env python3
"""
build.py — Water–Opposition Causal Analysis Pipeline

Stages:
    1  ingest/opposition.py     → data/processed/opposition_by_county.csv
    2  ingest/controls.py       → data/processed/controls.csv
    3  ingest/drought_monitor.py→ data/processed/drought_monitor.csv
    4  ingest/usgs_water.py     → data/processed/usgs_water.csv        (slow; skip with --skip-usgs)
    5  ingest/reservoirs.py     → data/processed/reservoirs.csv        (skip with --skip-usbr)
    6  ingest/wri_aqueduct.py   → data/processed/wri_aqueduct.csv      (auto-skipped if no data file)
    7  process/tension.py       → data/processed/water_tension.csv
    8  process/analysis.py      → data/output/master_analysis.csv
                                   data/output/analysis_summary.json
    9  process/export_json.py   → data/output/water_opp_map.json

Usage:
    python build.py                        # full rebuild
    python build.py --skip-usgs            # skip slow USGS fetch
    python build.py --skip-usgs --skip-usbr # fastest useful run
    python build.py --states TX AZ NV VA  # limit USGS to these states
    python build.py --analyze-only         # re-run stages 7-9 only
    python build.py --export-only          # re-run stage 9 only
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# ── Helpers ───────────────────────────────────────────────────────────────────

def stage(n: int, total: int, name: str, fn, *args, **kwargs):
    bar = f"[{n}/{total}]"
    print(f"\n{'─'*60}")
    print(f"  {bar}  {name}")
    print(f"{'─'*60}")
    t0 = time.time()
    try:
        result = fn(*args, **kwargs)
        elapsed = time.time() - t0
        print(f"  ✓  {name} completed in {elapsed:.1f}s")
        return result
    except Exception as e:
        import traceback
        print(f"  ✗  {name} FAILED: {e}")
        traceback.print_exc()
        return None

def skip(n: int, total: int, name: str, reason: str):
    print(f"\n  [{n}/{total}]  {name}  ←  skipped ({reason})")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Water–Opposition pipeline")
    p.add_argument("--analyze-only", action="store_true",
                   help="Skip all ingest; run tension + analysis + export only")
    p.add_argument("--export-only",  action="store_true",
                   help="Re-run export_json only (fastest; assumes master_analysis.csv exists)")
    p.add_argument("--skip-usgs",    action="store_true",
                   help="Skip USGS groundwater/streamflow fetch (~15 min national run)")
    p.add_argument("--skip-usbr",    action="store_true",
                   help="Skip USBR reservoir fetch")
    p.add_argument("--states",       nargs="*",
                   help="Limit USGS fetch to these state abbreviations, e.g. TX AZ NV VA")
    args = p.parse_args()

    TOTAL = 9
    print("Water–Opposition Causal Analysis Pipeline")
    print(f"Root: {ROOT}")
    print()

    # ── Ingest ────────────────────────────────────────────────────────────────
    if not (args.analyze_only or args.export_only):

        from ingest.opposition import run as run_opp
        stage(1, TOTAL, "Opposition data (IV from master_opposition.csv)", run_opp)

        from ingest.controls import run as run_controls
        stage(2, TOTAL, "Controls (election data, water law, demographics)", run_controls)

        from ingest.drought_monitor import run as run_drought
        stage(3, TOTAL, "USDM Drought Monitor (current + 10yr historical)", run_drought)

        if not args.skip_usgs:
            from ingest.usgs_water import run as run_usgs
            stage(4, TOTAL, "USGS groundwater + streamflow", run_usgs,
                  states=args.states or None)
        else:
            skip(4, TOTAL, "USGS water data", "--skip-usgs")

        if not args.skip_usbr:
            from ingest.reservoirs import run as run_res
            stage(5, TOTAL, "USBR reservoir storage + Army Corps NID", run_res)
        else:
            skip(5, TOTAL, "USBR reservoirs + NID", "--skip-usbr")

        wri_dir = ROOT / "data" / "raw" / "wri_aqueduct"
        wri_files = list(wri_dir.glob("*.csv")) + list(wri_dir.glob("*.gpkg")) \
                    if wri_dir.exists() else []
        if wri_files:
            from ingest.wri_aqueduct import run as run_wri
            stage(6, TOTAL, f"WRI Aqueduct ({wri_files[0].name})", run_wri)
        else:
            skip(6, TOTAL, "WRI Aqueduct", "no CSV/GPKG found in data/raw/wri_aqueduct/ — "
                 "download from datasets.wri.org/dataset/aqueduct40")

    # ── Process ───────────────────────────────────────────────────────────────
    if not args.export_only:
        from process.tension import build_water_tension
        stage(7, TOTAL, "Water Tension Index (IV)", build_water_tension)

        from process.analysis import run_analysis
        stage(8, TOTAL, "Causal analysis (OLS regression + residuals)", run_analysis)

    from process.export_json import build_json
    stage(9, TOTAL, "Map JSON export", build_json)

    # ── Summary ───────────────────────────────────────────────────────────────
    import json
    summary_path = ROOT / "data" / "output" / "analysis_summary.json"
    if summary_path.exists():
        with open(summary_path) as f:
            s = json.load(f)

        c   = s.get("correlations", {})
        m2  = s.get("models", {}).get("M2_full", {})
        interp = s.get("interpretation", {})
        quads  = s.get("quadrant_counts", {})

        print(f"\n{'='*60}")
        print("  RESULTS")
        print(f"{'='*60}")
        print(f"  Counties analyzed:         {s.get('n_counties', '—')}")
        print(f"  With opposition:           {s.get('n_opp_counties', '—')}")
        print(f"  With water opposition:     {s.get('n_water_opp_counties', '—')}")
        print()
        print(f"  r(WTI, opp_rate)         = {c.get('r_wti_opp_rate', '—'):.4f}")
        print(f"  r(WTI, water_opp_rate)   = {c.get('r_wti_water_opp_rate', '—'):.4f}")
        print(f"  partial r(WTI | GOP)     = {c.get('partial_r_wti_opp_controlling_gop', '—'):.4f}")
        print()
        print(f"  β_WTI (full model)       = {m2.get('beta_wti', '—')}")
        print(f"  R² full model            = {m2.get('r_squared', '—')}")
        print(f"  ΔR² from water tension   = {m2.get('delta_r2_from_controls', '—')}")
        print(f"  β p-value (WTI)          = {m2.get('beta_wti_pvalue', '—')}")
        print()
        print(f"  Quadrant breakdown:")
        for q in ("water_driven", "latent_risk", "other_drivers", "quiet"):
            print(f"    {q:<18}: {quads.get(q, 0):>5} counties")
        print()
        print(f"  Water tension causal?      {interp.get('wti_is_causal', '—')}")
        print(f"  WTI > GOP as predictor?    {interp.get('wti_stronger_than_gop', '—')}")
        print()
        note = interp.get("note", "")
        if note:
            import textwrap
            for line in textwrap.wrap(f"  Note: {note}", 60):
                print(line)

    print(f"\n{'='*60}")
    print(f"  Outputs → {ROOT / 'data' / 'output'}")
    out_dir = ROOT / "data" / "output"
    for f in sorted(out_dir.glob("*")):
        print(f"    {f.name:<35} {f.stat().st_size/1024:>7.1f} KB")
    print()


if __name__ == "__main__":
    main()
