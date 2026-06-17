from __future__ import annotations
import csv, json, math, warnings
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
OUT_DIR = ROOT / "data" / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

VOTE_TO_POP = 1.72
WTI_HIGH = 50.0

_STATE_FIPS = {
    "01":"AL","02":"AK","04":"AZ","05":"AR","06":"CA","08":"CO","09":"CT",
    "10":"DE","12":"FL","13":"GA","15":"HI","16":"ID","17":"IL","18":"IN",
    "19":"IA","20":"KS","21":"KY","22":"LA","23":"ME","24":"MD","25":"MA",
    "26":"MI","27":"MN","28":"MS","29":"MO","30":"MT","31":"NE","32":"NV",
    "33":"NH","34":"NJ","35":"NM","36":"NY","37":"NC","38":"ND","39":"OH",
    "40":"OK","41":"OR","42":"PA","44":"RI","45":"SC","46":"SD","47":"TN",
    "48":"TX","49":"UT","50":"VT","51":"VA","53":"WA","54":"WV","55":"WI","56":"WY",
}

def load_csv(path):
    if not path.exists():
        print(f"  Missing: {path.name}"); return {}
    data = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            fips = str(row.pop("fips","")).zfill(5)
            if fips and len(fips)==5: data[fips] = row
    return data

def _f(val, default=None):
    if val is None or str(val).strip() in ("","None","nan"): return default
    try:
        v = float(val)
        return default if (math.isnan(v) or math.isinf(v)) else v
    except: return default

def pearson_r(x, y):
    n = len(x)
    if n < 3: return 0.0
    mx,my = sum(x)/n, sum(y)/n
    num = sum((x[i]-mx)*(y[i]-my) for i in range(n))
    den = math.sqrt(sum((xi-mx)**2 for xi in x)*sum((yi-my)**2 for yi in y))
    return round(num/den,4) if den>0 else 0.0

def partial_r(x, y, z):
    def res(a,b):
        n=len(a); mb,ma=sum(b)/n,sum(a)/n
        cov=sum((b[i]-mb)*(a[i]-ma) for i in range(n))
        var=sum((bi-mb)**2 for bi in b)
        beta=cov/var if var>0 else 0.0
        return [a[i]-(ma-beta*mb+beta*b[i]) for i in range(n)]
    return pearson_r(res(x,z),res(y,z))

def stdz(v):
    n=len(v); m=sum(v)/n
    s=math.sqrt(sum((x-m)**2 for x in v)/n)
    return [(x-m)/s if s>0 else 0.0 for x in v]

def safe_ols(names, cols, y):
    try:
        import numpy as np
        from scipy import stats
        n = len(y)
        X = np.column_stack([np.ones(n)]+[np.array(c,dtype=np.float64) for c in cols])
        Y = np.array(y, dtype=np.float64)
        ok = np.isfinite(X).all(axis=1)&np.isfinite(Y)
        Xc,Yc = X[ok],Y[ok]
        if len(Yc)<len(names)+5: return None
        for j in range(1,Xc.shape[1]):
            col=Xc[:,j]; m,s=col.mean(),col.std()
            if s>0: Xc[:,j]=np.clip(col,m-5*s,m+5*s)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            coefs,_,_,_ = np.linalg.lstsq(Xc,Yc,rcond=None)
        if not np.all(np.isfinite(coefs)): return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            yhat = Xc@coefs
        resids = Yc-yhat
        ss_res=float(np.sum(resids**2)); ss_tot=float(np.sum((Yc-Yc.mean())**2))
        r2=1-ss_res/ss_tot if ss_tot>0 else 0.0
        cd={n:round(float(coefs[i+1]),4) for i,n in enumerate(names)}
        full_r=[0.0]*n; full_y=[0.0]*n
        idx=np.where(ok)[0]
        for j,i in enumerate(idx): full_r[i]=float(resids[j]); full_y[i]=float(yhat[j])
        return {"coefs":cd,"r_squared":round(r2,4),"residuals":full_r,"y_hat":full_y,"n":int(len(Yc)),"pvalues":{}}
    except Exception as e:
        print(f"  OLS skipped: {e}"); return None

def quad(wti,opp):
    hi=wti>=WTI_HIGH; has=opp>0
    if hi and has: return "water_driven"
    if hi: return "latent_risk"
    if has: return "other_drivers"
    return "quiet"

def run_analysis():
    print("=== Causal Analysis ===\n")
    tension=load_csv(PROCESSED_DIR/"water_tension.csv")
    opp=load_csv(PROCESSED_DIR/"opposition_by_county.csv")
    controls=load_csv(PROCESSED_DIR/"controls.csv")
    print(f"  Controls:{len(controls)}  WTI:{len(tension)}  Opp:{len(opp)}\n")

    rows=[]
    for fips in sorted(controls.keys()):
        if fips[:2] not in _STATE_FIPS: continue
        c=controls.get(fips,{}); t=tension.get(fips,{}); o=opp.get(fips,{})
        wti=_f(t.get("wti"),25.0)
        opp_n=int(_f(o.get("opp_count"),0)); opp_w=int(_f(o.get("opp_water_count"),0))
        opp_sev=_f(o.get("opp_severity_score"),0.0)
        votes=_f(c.get("total_votes_2024"),0); pop=max(1.0,votes*VOTE_TO_POP)
        rows.append({
            "fips":fips,"county_name":c.get("county_name",""),
            "state_abbr":c.get("state_abbr",""),"state_name":c.get("state_name",""),
            "wti":round(wti,2),"wti_tier":t.get("wti_tier",""),
            "supply_deficit_score":round(_f(t.get("supply_deficit_score"),wti),1),
            "demand_pressure_score":round(_f(t.get("demand_pressure_score"),wti),1),
            "seasonal_pinch_score":round(_f(t.get("seasonal_pinch_score"),wti),1),
            "wti_completeness":round(_f(t.get("wti_completeness"),0.0),3),
            "wti_sources":t.get("wti_sources","SEED"),
            "opp_count":opp_n,"opp_water_count":opp_w,
            "opp_water_pct":round(_f(o.get("opp_water_pct"),0.0),1),
            "opp_rate_per_100k":round((opp_n/pop)*100000,4),
            "opp_water_rate_per_100k":round((opp_w/pop)*100000,4),
            "opp_sev_rate_per_100k":round((opp_sev/pop)*100000,4),
            "pct_gop_2024":round(_f(c.get("pct_gop_2024"),50.0),1),
            "pop_density":round(_f(c.get("pop_density"),100.0),2),
            "water_law_encoded":_f(c.get("water_law_encoded"),0.0),
            "ag_water_pct":_f(c.get("ag_water_pct"),40.0),
            "pop_estimate":round(pop),
        })

    sample=[r for r in rows if r["pop_estimate"]>1000]
    n_opp=sum(1 for r in sample if r["opp_count"]>0)
    n_w=sum(1 for r in sample if r["opp_water_count"]>0)
    print(f"  Sample:{len(sample)}  With opp:{n_opp}  Water opp:{n_w}\n")

    wv=[r["wti"] for r in sample]
    ov=[r["opp_rate_per_100k"] for r in sample]
    wv2=[r["opp_water_rate_per_100k"] for r in sample]
    gv=[r["pct_gop_2024"] for r in sample]
    dv=[r["pop_density"] for r in sample]

    rwo=pearson_r(wv,ov); rww=pearson_r(wv,wv2)
    rgo=pearson_r(gv,ov); rdo=pearson_r(dv,ov)
    rp=partial_r(wv,ov,gv)
    print(f"  r(WTI,opp)={rwo:+.4f}  r(WTI,water_opp)={rww:+.4f}")
    print(f"  r(GOP,opp)={rgo:+.4f}  partial_r(WTI|GOP)={rp:+.4f}\n")

    wz=stdz(wv); oz=stdz(ov); wz2=stdz(wv2); gz=stdz(gv); dz=stdz(dv)
    lv=[r["water_law_encoded"] for r in sample]
    az=stdz([r["ag_water_pct"] for r in sample])
    Xc=[gz,dz,lv,az]; Xn=["pct_gop","pop_density","water_law","ag_water"]
    m1=safe_ols(["wti"],[wz],oz)
    m4=safe_ols(Xn,Xc,oz)
    m2=safe_ols(["wti"]+Xn,[wz]+Xc,oz)
    m3=safe_ols(["wti"]+Xn,[wz]+Xc,wz2)
    dr2=round(m2["r_squared"]-m4["r_squared"],4) if m2 and m4 else None
    if m1: print(f"  M1: beta_WTI={m1['coefs'].get('wti',0):+.4f}  R2={m1['r_squared']:.4f}")
    if m2: print(f"  M2: beta_WTI={m2['coefs'].get('wti',0):+.4f}  R2={m2['r_squared']:.4f}  dR2={dr2}")
    print()

    mr=[0.0]*len(sample); my=[0.0]*len(sample)
    if m2:
        for i,r in enumerate(m2["residuals"]): mr[i]=r if math.isfinite(r) else 0.0
        for i,r in enumerate(m2["y_hat"]): my[i]=r if math.isfinite(r) else 0.0

    quads=defaultdict(int)
    for i,row in enumerate(sample):
        row["m2_residual"]=round(mr[i],4)
        row["m2_predicted"]=round(my[i],4)
        row["residual_quadrant"]=quad(row["wti"],row["opp_count"])
        quads[row["residual_quadrant"]]+=1

    print("  Quadrants:")
    for q in ("water_driven","latent_risk","other_drivers","quiet"):
        print(f"    {q}: {quads[q]} ({quads[q]/len(sample)*100:.1f}%)")

    fields=["fips","county_name","state_abbr","state_name","wti","wti_tier",
        "supply_deficit_score","demand_pressure_score","seasonal_pinch_score",
        "wti_completeness","wti_sources","opp_count","opp_water_count","opp_water_pct",
        "opp_rate_per_100k","opp_water_rate_per_100k","opp_sev_rate_per_100k",
        "pct_gop_2024","pop_density","water_law_encoded","ag_water_pct","pop_estimate",
        "m2_residual","m2_predicted","residual_quadrant"]
    mp=OUT_DIR/"master_analysis.csv"
    with open(mp,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        w.writeheader(); w.writerows(sample)
    print(f"\n  Saved master_analysis.csv ({len(sample)} rows)")

    sp=OUT_DIR/"analysis_summary.json"
    with open(sp,"w",encoding="utf-8") as f:
        json.dump({"generated":date.today().isoformat(),"n_counties":len(sample),
            "n_opp_counties":n_opp,"n_water_opp_counties":n_w,
            "correlations":{"r_wti_opp_rate":rwo,"r_wti_water_opp_rate":rww,
                "r_gop_opp_rate":rgo,"partial_r_wti_opp_controlling_gop":rp},
            "models":{"M1":{"beta_wti":m1["coefs"].get("wti") if m1 else None,"r_squared":m1["r_squared"] if m1 else None},
                "M2":{"beta_wti":m2["coefs"].get("wti") if m2 else None,"r_squared":m2["r_squared"] if m2 else None,"delta_r2":dr2},
                "M3":{"beta_wti":m3["coefs"].get("wti") if m3 else None,"r_squared":m3["r_squared"] if m3 else None}},
            "quadrant_counts":dict(quads),
            "interpretation":{"wti_is_causal":abs(rp)>0.10,"wti_stronger_than_gop":abs(rwo)>abs(rgo)}},f,indent=2)
    print("  Saved analysis_summary.json\nDone.\n")
    return mp,sp

if __name__=="__main__":
    run_analysis()
