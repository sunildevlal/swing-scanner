"""Option ideas from free NSE end-of-day F&O data. Imported by scan.py."""
import io, json, math, os, zipfile
import numpy as np, pandas as pd, requests

RF = 0.07            # risk-free rate used for pricing
HOLD_T1, HOLD_SL = 3, 2   # days assumed to reach Target 1 / to hit the stop (used to re-price the option)
URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{}_F_0000.csv.zip"


def cl(x, a=0., b=1.): return float(min(b, max(a, x)))
def ncdf(x): return .5 * (1 + math.erf(x / math.sqrt(2)))


def bs(S, K, T, iv, ot):
    if T <= 0: return max(0., S - K if ot == "CE" else K - S)
    sd = iv * math.sqrt(T); d1 = (math.log(S / K) + (RF + iv * iv / 2) * T) / sd; d2 = d1 - sd; q = math.exp(-RF * T)
    return S * ncdf(d1) - K * q * ncdf(d2) if ot == "CE" else K * q * ncdf(-d2) - S * ncdf(-d1)


def delta(S, K, T, iv, ot):
    d1 = (math.log(S / K) + (RF + iv * iv / 2) * T) / (iv * math.sqrt(T)); return ncdf(d1) if ot == "CE" else ncdf(d1) - 1


def impl_vol(p, S, K, T, ot):
    lo, hi = .02, 3.
    if T <= 0 or p <= bs(S, K, T, lo, ot) or p >= bs(S, K, T, hi, ot): return None
    for _ in range(40):
        m = (lo + hi) / 2
        if bs(S, K, T, m, ot) < p: lo = m
        else: hi = m
    return (lo + hi) / 2


def fetch_fo(day):
    for back in range(5):
        d = day - pd.Timedelta(days=back)
        try:
            r = requests.get(URL.format(d.strftime("%Y%m%d")), headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            if r.ok and r.content[:2] == b"PK":
                z = zipfile.ZipFile(io.BytesIO(r.content)); return d, pd.read_csv(z.open(z.namelist()[0]))
        except Exception as e: print("F&O download", d.date(), e)
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "universe", "fo_bhavcopy.csv")  # manual fallback
    if os.path.exists(p):
        df = pd.read_csv(p); return (pd.Timestamp(df.TradDt.iloc[0]) if "TradDt" in df else day), df
    return None, None


def prep(df):
    df = df[df.FinInstrmTp.isin(["IDO", "STO"])].copy()
    df["exp"] = pd.to_datetime(df.XpryDt); df["K"] = pd.to_numeric(df.StrkPric)
    df["px"] = np.where(df.ClsPric > 0, df.ClsPric, df.LastPric)
    return df


def pick_exp(exps, td, mind):
    for e in exps:
        if (e - td).days >= mind: return e


def stats(sub, spot):
    ce, pe = sub[sub.OptnTp == "CE"], sub[sub.OptnTp == "PE"]; up, dn = ce[ce.K > spot], pe[pe.K < spot]
    ks = np.sort(sub.K.unique())
    loss = [((k - ce.K).clip(lower=0) * ce.OpnIntrst).sum() + ((pe.K - k).clip(lower=0) * pe.OpnIntrst).sum() for k in ks]
    return dict(pcr=round(float(pe.OpnIntrst.sum() / max(ce.OpnIntrst.sum(), 1)), 2),
                cw=float(up.loc[up.OpnIntrst.idxmax(), "K"]) if len(up) else None,
                pw=float(dn.loc[dn.OpnIntrst.idxmax(), "K"]) if len(dn) else None,
                pain=float(ks[int(np.argmin(loss))]), flow=float(pe.ChngInOpnIntrst.sum() - ce.ChngInOpnIntrst.sum()))


def atm_iv(sub, spot, T):
    k0 = sub.K.iloc[int((sub.K - spot).abs().values.argmin())]
    ivs = [impl_vol(float(x.px), spot, float(k0), T, x.OptnTp) for x in sub[sub.K == k0].itertuples()]
    ivs = [v for v in ivs if v]; return float(np.mean(ivs)) if ivs else None


def build(sym, side, r, df, td, ivi, is_idx):
    g = df[df.TckrSymb == sym]
    if g.empty: return None
    e = pick_exp(sorted(g.exp.unique()), td, 5 if is_idx else 7)
    if e is None: return None
    sub = g[g.exp == e]; dte = (e - td).days; T = dte / 365; S = float(r["px"]); t1, sl = float(r["t1"]), float(r["stop"])
    ot = "CE" if side == "bull" else "PE"; sg = 1 if side == "bull" else -1; lot = int(sub.NewBrdLotQty.iloc[0]); iv0, ivp = ivi
    mv, mo = (1000, 5000) if is_idx else (20, 100)
    liq = sub[(sub.px > 0) & (sub.TtlTradgVol >= mv) & (sub.OpnIntrst >= mo)]; A = liq[liq.OptnTp == ot]
    val = lambda K, o, s, d, iv: bs(s, K, max(T - d / 365, 1e-4), iv, o)
    c1 = None
    for x in A.itertuples():   # best long strike: at or slightly in the money, delta 0.4+
        if not -.005 <= sg * (S - x.K) / S <= .06: continue
        iv = impl_vol(float(x.px), S, x.K, T, ot)
        if not iv or abs(delta(S, x.K, T, iv, ot)) < .4: continue
        a, b = val(x.K, ot, t1, HOLD_T1, iv) - x.px, val(x.K, ot, sl, HOLD_SL, iv) - x.px; rr = a / max(-b, .15 * x.px)
        if c1 is None or rr > c1["rr"]: c1 = dict(K=float(x.K), p=float(x.px), iv=iv, rr=rr, vol=float(x.TtlTradgVol))
    if not c1: return None

    def struct(name, kind, legs, risk, maxp, be):  # legs: (+1 buy / -1 sell, strike, type, price, iv); values are per share
        net = sum(s * p for s, K, o, p, iv in legs)
        pt = sum(s * val(K, o, t1, HOLD_T1, iv) for s, K, o, p, iv in legs) - net
        ps = max(sum(s * val(K, o, sl, HOLD_SL, iv) for s, K, o, p, iv in legs) - net, -risk)
        return dict(name=name, kind=kind, legs=[f"{'BUY' if s > 0 else 'SELL'} {K:g} {o} @ {p:.2f}" for s, K, o, p, iv in legs], debit=round(net * lot),
                    risk=round(risk * lot), maxp=None if maxp is None else round(maxp * lot), pt=round(pt * lot), ps=round(ps * lot),
                    rr=round(pt / max(-ps, .15 * risk), 2), be=round(be, 2))

    K1, p1 = c1["K"], c1["p"]
    S_ = [struct("Buy Call" if sg > 0 else "Buy Put", "long", [(1, K1, ot, p1, c1["iv"])], p1, None, K1 + sg * p1)]
    far = A[sg * (A.K - K1) > 0]
    if len(far):   # debit spread: sell the strike nearest Target 1
        x2 = far.iloc[int((far.K - t1).abs().values.argmin())]; iv2 = impl_vol(float(x2.px), S, float(x2.K), T, ot); cost = p1 - float(x2.px); w = abs(x2.K - K1)
        if iv2 and 0 < cost < .85 * w:
            S_.append(struct("Bull call spread" if sg > 0 else "Bear put spread", "debit", [(1, K1, ot, p1, c1["iv"]), (-1, float(x2.K), ot, float(x2.px), iv2)], cost, w - cost, K1 + sg * cost))
    o2 = "PE" if sg > 0 else "CE"; B = liq[liq.OptnTp == o2].sort_values("K"); safe = B[B.K <= sl] if sg > 0 else B[B.K >= sl]
    if len(safe) >= 3:   # credit spread: sell beyond the stop-loss, buy two strikes further out
        xs, xb = (safe.iloc[-1], safe.iloc[-3]) if sg > 0 else (safe.iloc[0], safe.iloc[2])
        ivs, ivb = impl_vol(float(xs.px), S, float(xs.K), T, o2), impl_vol(float(xb.px), S, float(xb.K), T, o2); cr = float(xs.px - xb.px); w = abs(xs.K - xb.K)
        if ivs and ivb and cr >= .15 * w:
            S_.append(struct("Bull put spread" if sg > 0 else "Bear call spread", "credit", [(-1, float(xs.K), o2, float(xs.px), ivs), (1, float(xb.K), o2, float(xb.px), ivb)], w - cr, cr, float(xs.K) - sg * cr))

    def mult(s):
        m = .8 if s["kind"] == "credit" else 1.
        if ivp is not None: m *= 1.2 if (ivp < .3 and s["kind"] == "long") or (ivp > .7 and s["kind"] != "long") else 1.
        return m
    bi = max(range(len(S_)), key=lambda i: S_[i]["rr"] * mult(S_[i])); b = S_[bi]
    st = stats(sub, S); warn = []; oi = .5 + .25 * (st["flow"] * sg > 0)
    if sg > 0:
        if st["pw"] is not None and sl * .98 <= st["pw"] < S: oi += .25
        if st["cw"] and S * 1.01 < st["cw"] < t1: oi -= .25; warn.append(f"Highest call OI at {st['cw']:g} sits below Target 1 and may cap the move")
    else:
        if st["cw"] is not None and S < st["cw"] <= sl * 1.02: oi += .25
        if st["pw"] and t1 < st["pw"] < S * .99: oi -= .25; warn.append(f"Highest put OI at {st['pw']:g} sits above Target 1 and may stall the fall")
    ivs_ = .5 if ivp is None else (1 - ivp if b["kind"] == "long" else .75 - ivp * .5 if b["kind"] == "debit" else ivp)
    parts = {"Stock setup": cl(r["score"] / 10), "Reward:risk": cl(b["rr"] / 2.5), "IV (cheap is good)": ivs_, "Liquidity": cl((math.log10(max(c1["vol"], 1)) - 1.3) / 2.2),
             "OI support": cl(oi), "Expiry fit": cl((dte - 3) / 12)}
    wt = [.30, .20, .15, .15, .10, .10]; sc = 1 + 9 * sum(w_ * v for w_, v in zip(wt, parts.values()))
    if b["rr"] < 1: sc -= .8; warn.append(f"Low reward:risk ({b['rr']}) on the premium")
    if ivp is not None and ivp >= .8 and b["kind"] == "long": sc -= .5; warn.append("Premiums are expensive (high IV), a spread may suit better")
    sc = round(cl(sc, 1, 10), 1); pats = [p for p in r["pats"] if not p.startswith("Stage")][:2]
    why = [["g", f"Stock setup: {', '.join(pats)} (stock score {r['score']})"], ["g", f"{b['name']}: {' / '.join(b['legs'])}, expiry {e:%d %b} ({dte} days)"],
           ["g" if b["rr"] >= 1.5 else "w", f"If the stock reaches Target 1 in {HOLD_T1} days: about ₹{b['pt']:,} per lot; if it hits the stop in {HOLD_SL} days: about ₹{b['ps']:,}"],
           ["w", "IV history is still building, so IV percentile is not available yet"] if ivp is None else
           ["g" if ivp < .5 else "w", f"Premium looks {'cheap' if ivp < .3 else 'fair' if ivp < .7 else 'expensive'}: IV is above {ivp * 100:.0f}% of past readings"],
           ["g" if oi >= .5 else "w", f"Put-call ratio {st['pcr']}; highest put OI (support) {st['pw'] or '–'}, highest call OI (resistance) {st['cw'] or '–'}"]] + [["w", w_] for w_ in warn]
    return dict(sym=sym, name=r.get("name", sym), side=side, spot=S, lot=lot, expiry=f"{e:%d %b}", dte=dte, setup=r["score"], entry=r["entry"], stop=sl, t1=t1, t2=r.get("t2"),
                score=sc, label="Strong" if sc >= 8 else "Good" if sc >= 6.5 else "Weak", structs=S_, best=bi, why=why, parts={k: round(v * 10, 1) for k, v in parts.items()})


def run(cands, idx, day, hist_path="docs/ivhist.json"):
    """cands: (symbol, 'bull'|'bear', scan row). idx: {'NIFTY': info, ...}. Returns (summary for the website, ideas)."""
    td, df = fetch_fo(day)
    if df is None: return dict(ok=False, msg="NSE F&O data could not be downloaded today, so option ideas are unavailable."), []
    df = prep(df)
    try: hist = json.load(open(hist_path))
    except Exception: hist = {}
    key, ivi = td.strftime("%Y-%m-%d"), {}
    for sym, g in df.groupby("TckrSymb"):
        e = pick_exp(sorted(g.exp.unique()), td, 5 if sym in idx else 7)
        if e is None: continue
        iv = atm_iv(g[g.exp == e], float(g.UndrlygPric.iloc[0]), (e - td).days / 365)
        if not iv: continue
        h = hist.setdefault(sym, {}); h[key] = round(iv, 4)
        for k in sorted(h)[:-250]: del h[k]
        prev = np.array([v for k, v in h.items() if k != key]); ivi[sym] = (iv, float((prev < iv).mean()) if len(prev) >= 20 else None)
    os.makedirs(os.path.dirname(hist_path) or ".", exist_ok=True); json.dump(hist, open(hist_path, "w"), separators=(",", ":"))
    index = {}
    for sym, info in idx.items():
        g = df[df.TckrSymb == sym]; e = pick_exp(sorted(g.exp.unique()), td, 5) if len(g) else None
        if e is None: continue
        st = stats(g[g.exp == e], info["spot"]); iv, ivp = ivi.get(sym, (None, None))
        index[sym] = dict(info, pcr=st["pcr"], pain=st["pain"], cw=st["cw"], pw=st["pw"], expiry=f"{e:%d %b}",
                          iv=None if iv is None else round(iv * 100, 1), ivp=None if ivp is None else round(ivp * 100))
    ideas = []
    for sym, side, r in cands:
        try: b = build(sym, side, r, df, td, ivi.get(sym, (None, None)), sym in idx)
        except Exception as ex: print("option idea", sym, ex); b = None
        if b: ideas.append(b)
    ideas.sort(key=lambda i: -i["score"])
    return dict(ok=True, date=td.strftime("%d %b %Y"), index=index, ideas=ideas), ideas
