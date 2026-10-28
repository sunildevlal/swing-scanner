"""NSE swing scanner. Run after market close from the repo root: python scanner/scan.py
Writes docs/data.json, which the website reads."""
import io, json, os, re, datetime as dt
import numpy as np, pandas as pd, requests, yfinance as yf

BASE = "https://nsearchives.nseindia.com/content/indices/"
LISTS = {"n100": "ind_nifty100list.csv", "n200": "ind_nifty200list.csv", "n500": "ind_nifty500list.csv",
         "mid": "ind_niftymidcap150list.csv", "small": "ind_niftysmallcap250list.csv"}
MIN_TURNOVER = 5e7  # average daily traded value in Rs; below this a stock is skipped (hard to exit)
W = {"Pattern": .25, "Trend": .20, "Rel. strength": .20, "Volume": .15, "Reward:risk": .15, "Market/Sector": .05}
# Per timeframe: ma=(fast EMA, mid SMA, slow SMA), yr=bars in a year, nb=breakout lookback, base=base length, bt=max base range,
# vcp=VCP window, vt=VCP last-contraction limit, fib=swing window, pg=min rally, pole/fl=flag pole/flag bars,
# cup/cd=cup window/max depth, db=double-bottom window, sw=support lookback, rl=resistance lookback, ext=extension limit
P = {
    "D": dict(ma=(20, 50, 200), yr=252, nb=20, base=15, bt=.12, vcp=60, vt=.09, fib=60, pg=.15, pole=10, fl=7, cup=120, cd=.35, db=80, sw=40, rl=250, ext=.12),
    "W": dict(ma=(10, 30, 40), yr=52, nb=10, base=10, bt=.18, vcp=40, vt=.14, fib=40, pg=.20, pole=6, fl=4, cup=60, cd=.40, db=50, sw=26, rl=104, ext=.20),
    "M": dict(ma=(5, 10, 20), yr=12, nb=6, base=6, bt=.30, vcp=24, vt=.22, fib=24, pg=.30, pole=4, fl=3, cup=36, cd=.50, db=30, sw=12, rl=60, ext=.35)}
UNIT = {"D": "day", "W": "week", "M": "month"}


def cl(x, a=0., b=1.): return float(min(b, max(a, x)))
def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def rsi(c, n=14):
    d = c.diff(); g = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    ls = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + g / ls.replace(0, np.nan))


def atr(df, n=14):
    pc = df.c.shift(); tr = pd.concat([df.h - df.l, (df.h - pc).abs(), (df.l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def supertrend(df, n=10, m=3):
    a = atr(df, n).values; h, l, c = df.h.values, df.l.values, df.c.values
    mid = (h + l) / 2; ub, lb = mid + m * a, mid - m * a
    fu, fl, d = ub.copy(), lb.copy(), np.ones(len(c))
    for i in range(1, len(c)):
        fu[i] = ub[i] if ub[i] < fu[i - 1] or c[i - 1] > fu[i - 1] else fu[i - 1]
        fl[i] = lb[i] if lb[i] > fl[i - 1] or c[i - 1] < fl[i - 1] else fl[i - 1]
        d[i] = 1 if c[i] > fu[i - 1] else -1 if c[i] < fl[i - 1] else d[i - 1]
    return d


def pivots(x, w, high=True):
    out = []
    for i in range(w, len(x) - w):
        seg = x[i - w:i + w + 1]
        if x[i] == (seg.max() if high else seg.min()): out.append(i)
    return out


def analyze(df, tf, ctx):
    p = P[tf]; f, mm, s = p["ma"]; u = UNIT[tf]
    if len(df) < max(s + 25, p["cup"] + 5): return None
    o, h, l, c, v = (df[k].values.astype(float) for k in "ohlcv")
    cs = df.c; px = c[-1]; n = len(c); yr = p["yr"]; vr = ctx["vr"]
    ef = ema(cs, f).values; sm = cs.rolling(mm).mean().values; sl = cs.rolling(s).mean().values
    a = atr(df).values[-1]; r = rsi(cs).values; hh = h[-yr:].max()
    tr = sum([px > ef[-1], px > sm[-1], px > sl[-1], sl[-1] > sl[-6], sm[-1] > sl[-1]]) / 5
    up = bool(px > sm[-1] and sl[-1] > sl[-6]); stage2 = bool(px > sm[-1] > sl[-1] and sl[-1] > sl[-6])
    strong = bool(sm[-1] > sl[-1] and sl[-1] > sl[-6] and px > sl[-1])
    pats = {}

    def add(name, q, g, txt): pats[name] = (cl(q), g, txt)

    # ---- Breakouts
    nb = p["nb"]; prior = h[-nb - 1:-1].max()
    if px > prior and px > c[-2]:
        add(f"Breakout ({nb}-{u} high)", .35 + .4 * (vr - 1), "Breakout", f"Closed above the {nb}-{u} high (₹{prior:.2f}) on {vr:.1f}x average volume")
    if px >= h[-yr:-1].max(): add("52-week high breakout", .9, "Breakout", f"New 52-week high, above ₹{h[-yr:-1].max():.2f}")
    elif px >= .97 * hh: add("Near 52-week high", .55, "Breakout", f"Only {(1 - px / hh) * 100:.1f}% below the 52-week high")
    bl = p["base"]; bh, bw_ = h[-bl - 1:-1], l[-bl - 1:-1]; rg = (bh.max() - bw_.min()) / bw_.min()
    if rg < p["bt"] and px > bh.max():
        add("Base breakout", .5 + (p["bt"] - rg) / p["bt"] * .3 + cl((vr - 1) * .1, 0, .2), "Breakout", f"Broke out of a tight {bl}-{u} base ({rg * 100:.0f}% range)")
    w = p["vcp"] // 2; hs = h[-w:]; top = hs.max(); slope = np.polyfit(np.arange(w), l[-w:], 1)[0] * w / l[-w:].mean()
    if (hs >= top * .985).sum() >= 2 and slope > .03 and top * .96 <= px <= top * 1.03:
        add("Ascending triangle", .6 + cl(slope * 2, 0, .3), "Pattern", f"Flat resistance near ₹{top:.2f} with rising lows")
    fl, pl = p["fl"], p["pole"]; bc = c[-fl - 1]; pole = bc / c[-fl - 1 - pl] - 1
    if pole >= p["pg"]:
        lo, hi = l[-fl:].min(), h[-fl:].max()
        if (hi - lo) / px < pole * .5 and (bc - lo) / (bc - c[-fl - 1 - pl]) < .5 and px >= hi * .97:
            add("Bull flag", .6 + cl(pole, 0, .4) * .5, "Pattern", f"Sharp {pole * 100:.0f}% rally followed by a tight {fl}-{u} flag")

    # ---- Pullbacks and retracements
    if strong and 36 <= r[-1] <= 62 and h[-p["fib"]:].max() > px * 1.03:
        for nm, lv in ((f"{f} EMA", ef[-1]), (f"{mm} MA", sm[-1])):
            if lv * .985 <= px <= lv * 1.025:
                add(f"Pullback to {nm}", .5 + .2 * (px > c[-2]) + .15 * (r[-1] > r[-2]), "Pullback", f"Pulled back to the {nm} inside an uptrend, RSI {r[-1]:.0f}")
    k = p["fib"]; hk, lk = h[-k:], l[-k:]; hi = int(np.argmax(hk))
    if hi >= 5:
        lo = int(np.argmin(lk[:hi + 1])); swg = hk[hi] - lk[lo]
        if swg / lk[lo] >= p["pg"] and hi < k - 2:
            ret = (hk[hi] - px) / swg
            if .33 <= ret <= .67:
                add("Fibonacci retracement", .5 + .2 * (px > c[-2]) + .15 * (.45 <= ret <= .65) + .1 * (sl[-1] > sl[-6]), "Pullback",
                    f"Retraced {ret * 100:.0f}% of the ₹{lk[lo]:.0f}→₹{hk[hi]:.0f} rally and holding the 38–62% zone")
    off = max(0, n - 60)
    for j in pivots(h[off:-3], 4, True)[::-1]:
        i = j + off
        if n - i > 45: break
        L = h[i]
        if i < n - 8 and c[i + 1:].max() > L * 1.01 and L * .99 <= px <= L * 1.04 and l[i + 4:].min() >= L * .96:
            add("Breakout retest", .6 + .2 * (px > c[-2]), "Pullback", f"Retesting the broken resistance near ₹{L:.2f}"); break

    # ---- Classic patterns: cup and handle, double bottom, VCP
    L_ = p["cup"]; ch, cw = h[-L_:], l[-L_:]
    li = int(np.argmax(ch[:int(L_ * .4)])); bi = li + int(np.argmin(cw[li:L_ - 3])); ri = bi + int(np.argmax(ch[bi:]))
    depth = (ch[li] - cw[bi]) / ch[li]; hl = cw[ri:].min(); pb = (ch[ri] - hl) / ch[ri]
    if (.12 <= depth <= p["cd"] and bi - li >= L_ * .2 and ri - bi >= (bi - li) * .5 and .9 * ch[li] <= ch[ri] <= 1.05 * ch[li]
            and 2 <= L_ - 1 - ri <= L_ * .25 and hl >= cw[bi] + depth * ch[li] * .5 and pb <= depth * .5 and px >= ch[ri] * .93):
        add("Cup and handle", .5 + .25 * (1 - abs(ch[ri] / ch[li] - 1) / .1) + .25 * (1 - pb / (depth * .5)), "Pattern",
            f"Cup {depth * 100:.0f}% deep, handle pullback {pb * 100:.0f}%, rim near ₹{ch[ri]:.2f}")
    d = p["db"]; seg = l[-d:]; pv = pivots(seg, 3, False)
    if len(pv) >= 2:
        i1, i2 = pv[-2], pv[-1]; neck = h[-d:][i1:i2 + 1].max(); lowv = max(seg[i1], seg[i2]); df_ = abs(seg[i2] / seg[i1] - 1)
        if i2 - i1 >= d // 6 and df_ <= .04 and neck / lowv - 1 >= .08 and neck * .97 <= px <= neck * 1.08:
            add("Double bottom", .5 + .3 * (1 - df_ / .04) + .2 * (px > neck), "Pattern", f"Two lows near ₹{lowv:.2f}, neckline at ₹{neck:.2f}")
    V = p["vcp"]; q4 = V // 4; rngs = []
    for j in range(4):
        e = -V + (j + 1) * q4 if j < 3 else None
        a_, b_ = h[-V + j * q4:e], l[-V + j * q4:e]; rngs.append((a_.max() - b_.min()) / a_.max())
    dec = sum(rngs[j + 1] < rngs[j] for j in range(3))
    if dec >= 2 and rngs[3] < p["vt"] and rngs[3] < rngs[2] and px > sl[-1] and px >= .9 * h[-V:].max():
        vdry = v[-q4:].mean() < v[-V:-q4].mean()
        add("VCP", .5 + .1 * dec + .15 * (1 - rngs[3] / p["vt"]) + .1 * vdry, "Pattern",
            "Volatility contraction " + " → ".join(f"{x * 100:.0f}%" for x in rngs) + (", volume drying up" if vdry else ""))

    # ---- Momentum
    if up and r[-1] > r[-2] and 38 <= r[-6:-1].min() <= 50:
        add("RSI turn-up", .5 + .1 * (r[-1] > 50), "Momentum", f"RSI turning up from {r[-6:-1].min():.0f} in an uptrend")
    macd = ema(cs, 12) - ema(cs, 26); mv, sv = macd.values, ema(macd, 9).values
    if mv[-1] > sv[-1] and (mv[-2] <= sv[-2] or mv[-3] <= sv[-3]):
        add("MACD crossover", .45 + .15 * (mv[-1] > 0) + .1 * up, "Momentum", "MACD crossed above its signal line" + (" above zero" if mv[-1] > 0 else ""))
    st = supertrend(df)
    if st[-1] == 1 and (st[-2] == -1 or st[-3] == -1): add("Supertrend buy", .5 + .15 * up, "Momentum", "Supertrend (10,3) just flipped to buy")
    ma20 = cs.rolling(20).mean(); bw = (4 * cs.rolling(20).std() / ma20).values; kk = min(120, n - 20)
    if bw[-6:-1].min() <= np.nanpercentile(bw[-kk:], 15) and px > ma20.values[-1] and px > c[-2]:
        add("Bollinger squeeze", .5 + .15 * up, "Momentum", "Tight volatility squeeze releasing upward")

    # ---- Candles at support, inside bar / NR7
    body = abs(c[-1] - o[-1]); rg1 = h[-1] - l[-1]
    ns = (l[-1] <= sm[-1] * 1.02 and px >= sm[-1] * .98) or abs(px / ef[-1] - 1) < .03
    if ns and px > sl[-1]:
        if c[-2] < o[-2] and c[-1] > o[-1] and c[-1] >= o[-2] and o[-1] <= c[-2]:
            add("Bullish engulfing", .5 + .2 * (vr > 1.2), "Candle", "Bullish engulfing candle at support")
        elif body > 0 and min(o[-1], c[-1]) - l[-1] >= 2 * body and h[-1] - max(o[-1], c[-1]) <= .5 * body:
            add("Hammer", .5 + .2 * (vr > 1.2), "Candle", "Hammer candle at support")
    if up and px >= .92 * hh:
        ins = h[-1] <= h[-2] and l[-1] >= l[-2]; nr = rg1 <= (h[-7:] - l[-7:]).min()
        if ins or nr: add("Inside bar / NR7", .45 + .1 * (ins and nr), "Candle", "Compression candle near highs, a breakout setup")

    if not pats: return None
    if stage2: pats["Stage 2 uptrend"] = (.3, "Trend", f"Above the {mm} and {s} averages with the {s} average rising (Stage 2)")

    # ---- Trade plan: stop below support, targets at next resistance
    sw = p["sw"]; lw = l[-sw:]
    sup = [lw[i] for i in pivots(lw, 3, False) if lw[i] < px * .99]
    stop = max(max(sup) * .99 if sup else px - 2 * a, px - 3 * a)
    if stop >= px * .99: stop = px - 1.5 * a
    risk = px - stop
    hr = h[-p["rl"]:]
    lv = sorted(x for x in set([hr[i] for i in pivots(hr, 3, True)] + [hr.max()]) if x > px * 1.02)
    res = []
    for x in lv:
        if res and x <= res[-1][0] * 1.015: res[-1][1] += 1
        else: res.append([x, 1])
    tg = [(round(float(x), 2), f"resistance, {k_} touch{'es' if k_ > 1 else ''}") for x, k_ in res[:2]]
    if len(tg) < 2:
        b2 = p["base"] * 2; ht = h[-b2:].max() - l[-b2:].min()
        for m_ in (1, 1.618):
            x = px + ht * m_
            if len(tg) < 2 and (not tg or x > tg[-1][0] * 1.02): tg.append((round(float(x), 2), "measured move (no resistance above)"))
    if not tg or risk <= 0: return None
    rr = (tg[0][0] - px) / risk

    # ---- Score 1-10
    qs = {k_: v_ for k_, v_ in pats.items() if v_[1] != "Trend"}
    pq = max(v_[0] for v_ in qs.values()); groups = {v_[1] for v_ in qs.values()}
    parts = {"Pattern": pq, "Trend": tr, "Rel. strength": ctx["rs"], "Volume": cl((vr - .7) / 1.3), "Reward:risk": cl(rr / 3), "Market/Sector": ctx["ms"]}
    sc = 1 + 9 * sum(W[k_] * parts[k_] for k_ in W) + min(.75, .25 * (len(groups) - 1)); warn = []
    ext = px / ef[-1] - 1
    if ext > p["ext"]: sc -= .6; warn.append(f"Extended: {ext * 100:.0f}% above the {f} EMA, a small pullback is possible")
    if rr < 1.5: sc -= .7 if rr >= 1 else 1.2; warn.append(f"Low reward:risk ({rr:.1f}), nearest resistance is close")
    if r[-1] > 75: sc -= .3; warn.append(f"Overbought, RSI {r[-1]:.0f}")
    if tg[0][0] / px - 1 < .04: warn.append(f"Nearest resistance is only {(tg[0][0] / px - 1) * 100:.1f}% away")
    if px < sl[-1]: warn.append(f"Trading below the {s} average (long-term trend not up)")
    if vr < 1 and "Breakout" in groups: warn.append(f"Breakout volume is below average ({vr:.1f}x)")
    sc = round(cl(sc, 1, 10), 1)
    order = sorted(pats.items(), key=lambda kv: -kv[1][0])
    why = [["g", t] for _, (_, _, t) in order]
    why.append(["g" if ctx["rs"] >= .6 else "w", f"Relative strength: ahead of {ctx['rs'] * 100:.0f}% of scanned stocks"])
    why.append(["g" if vr >= 1.2 else "w", f"Volume today is {vr:.1f}x the 20-day average"])
    why += [["w", t] for t in warn]
    return dict(px=round(float(px), 2), score=sc, label="Strong" if sc >= 8 else "Good" if sc >= 6.5 else "Weak",
                pats=[k_ for k_, _ in order], entry=round(float(px), 2), stop=round(float(stop), 2),
                t1=tg[0][0], t1src=tg[0][1], t2=tg[1][0] if len(tg) > 1 else None, t2src=tg[1][1] if len(tg) > 1 else "",
                rr=round(float(rr), 2), why=why, parts={k_: round(float(x) * 10, 1) for k_, x in parts.items()})


# Bearish scan = the bullish scan run on mirrored prices (k/price), then mapped back and re-worded.
SWAP = {"above": "below", "below": "above", "Breakout": "Breakdown", "breakout": "breakdown", "Broke out": "Broke down", "Breakdown": "Breakout",
        "Stage 2 uptrend": "Stage 4 downtrend", "(Stage 2)": "(Stage 4)", "Cup and handle": "Inverted cup and handle", "Double bottom": "Double top",
        "Ascending triangle": "Descending triangle", "Bull flag": "Bear flag", "Bullish": "Bearish", "Hammer": "Shooting star", "Pullback to": "Rally to",
        "Pulled back": "Rallied", "an uptrend": "a downtrend", "uptrend": "downtrend", "52-week high": "52-week low", "high": "low", "low": "high", "highs": "lows", "lows": "highs",
        "rising lows": "falling highs", "rally": "drop", "rising": "falling", "RSI turn-up": "RSI turn-down", "turning up": "turning down", "upward": "downward",
        "resistance": "support", "support": "resistance", "Overbought": "Oversold", "buy": "sell", "ahead of": "weaker than", "not up": "not down",
        "a small pullback": "a small bounce", "Breakout volume is below average": "Breakdown volume is below average", "Retesting the broken resistance": "Retesting the broken support"}
SWAP_RE = re.compile("|".join(re.escape(k) for k in sorted(SWAP, key=len, reverse=True)))


def analyze_bear(df, tf, ctx):
    k = float(df.c.iloc[-1]) ** 2
    inv = pd.DataFrame({"o": k / df.o, "h": k / df.l, "l": k / df.h, "c": k / df.c, "v": df.v})
    r = analyze(inv, tf, ctx)
    if not r: return None
    tx = lambda s: SWAP_RE.sub(lambda m: SWAP[m.group()], re.sub(r"₹([\d.]+)", lambda m: f"₹{k / float(m.group(1)):.2f}", s))
    px, stop, t1, t2 = r["px"], round(k / r["stop"], 2), round(k / r["t1"], 2), (round(k / r["t2"], 2) if r["t2"] else None)
    r.update(entry=px, stop=stop, t1=t1, t2=t2, rr=round((px - t1) / max(stop - px, 1e-9), 2), pats=[tx(p) for p in r["pats"]],
             t1src=tx(r["t1src"]), t2src=tx(r["t2src"]), why=[[a, tx(b)] for a, b in r["why"]])
    return r


def resample(d, tf):
    if tf == "D": return d
    return d.resample("W-FRI" if tf == "W" else "ME").agg({"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}).dropna()


def universe():
    meta = {}
    for key, fn in LISTS.items():
        try:
            txt = requests.get(BASE + fn, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
            df = pd.read_csv(io.StringIO(txt)); df["Symbol"]
        except Exception:  # NSE sometimes blocks cloud IPs: use a CSV saved in scanner/universe/
            df = pd.read_csv(os.path.join(os.path.dirname(__file__), "universe", fn))
        for _, row in df.iterrows():
            m = meta.setdefault(row["Symbol"], {"name": row["Company Name"], "sector": row["Industry"], "uni": []})
            m["uni"].append(key)
    return meta


def load(tickers):
    out, ren = {}, {"Open": "o", "High": "h", "Low": "l", "Close": "c", "Volume": "v"}
    for i in range(0, len(tickers), 100):
        b = tickers[i:i + 100]
        d = yf.download(b, period="10y", auto_adjust=True, group_by="ticker", progress=False, threads=True)
        for t in b:
            try:
                x = (d[t] if isinstance(d.columns, pd.MultiIndex) else d)[list(ren)].dropna().rename(columns=ren)
                if len(x): out[t] = x
            except Exception: pass
    return out


def read_list(fn):
    try: return [x.strip() for x in open(fn, encoding="utf-8") if x.strip() and not x.startswith("#")]
    except FileNotFoundError: return []


def chart_data(d, tf):
    f, mm, s = P[tf]["ma"]; x = resample(d, tf)
    x = x.assign(ef=ema(x.c, f), sm=x.c.rolling(mm).mean(), sl=x.c.rolling(s).mean()).tail(120)
    return [[i.strftime("%Y-%m-%d")] + [None if pd.isna(y) else round(float(y), 2) for y in row]
            for i, row in zip(x.index, x[["o", "h", "l", "c", "v", "ef", "sm", "sl"]].values)]


def tg(text):
    for i in range(0, len(text), 4000):
        requests.post(f"https://api.telegram.org/bot{os.environ['TG_TOKEN']}/sendMessage", timeout=30,
                      data={"chat_id": os.environ["TG_CHAT"], "text": text[i:i + 4000], "disable_web_page_preview": True}).raise_for_status()


def fmt(rs):
    return "\n".join(f"{i}. {r['sym']}  {r['score']} ({r['label']})\n   {', '.join([p for p in r['pats'] if p != 'Stage 2 uptrend'][:3])}\n"
                     f"   Entry {r['entry']} | SL {r['stop']} | T1 {r['t1']} | T2 {r['t2'] or '-'} | R:R {r['rr']}" for i, r in enumerate(rs, 1))


def alerts(out, rows, bear, watch, journal, day, ideas):
    """Telegram: top 10 new setups (bullish and bearish) scoring 7+, option ideas, watchlist setups, journal stop/target hits."""
    if not (os.environ.get("TG_TOKEN") and os.environ.get("TG_CHAT")): return
    try: st = json.load(open("docs/sent.json"))
    except Exception: st = {}
    m = out["market"]; nm = {"D": "Daily", "W": "Weekly", "M": "Monthly"}
    msgs = [f"NSE Swing Scanner, data of {out['last_bar']}\nMarket: {m['mood']}. Nifty is {'above' if m['a50'] else 'below'} its 50 DMA and "
            f"{'above' if m['a200'] else 'below'} its 200 DMA; {m['b50']}% of stocks are above their 50 DMA."
            + ("\nCaution: weak market, breakouts fail more often." if m["mood"] == "Weak" else "")]
    nx = day + pd.offsets.BDay(1)
    due = {"D": True, "W": nx.isocalendar()[:2] != day.isocalendar()[:2], "M": nx.month != day.month}  # weekly/monthly on last trading day
    new_any = False
    for pre, src, lab in (("", rows, ""), ("b", bear, "Bearish ")):
        for tf in "DWM":
            if not due[tf]: continue
            good = [r for r in src[tf] if r["score"] >= 7]; seen = set(st.get(pre + tf, []))
            new = [r for r in good if r["sym"] not in seen][:10]
            if new: msgs.append(f"{lab}{nm[tf]} setups scoring 7+ (new, top {len(new)}):\n" + fmt(new)); new_any = True
            st[pre + tf] = [r["sym"] for r in good]
            if pre: continue
            wl = [r for r in rows[tf] if r["sym"] in watch]; ws = set(st.get("w" + tf, []))
            if [r for r in wl if r["sym"] not in ws]: msgs.append(f"Watchlist: new {nm[tf].lower()} setup:\n" + fmt([r for r in wl if r["sym"] not in ws]))
            st["w" + tf] = [r["sym"] for r in wl]
    oi = [i for i in ideas if i["score"] >= 7]; ok = lambda i: f"{i['sym']}:{i['side']}:{i['expiry']}"; seen = set(st.get("o", []))
    on = [i for i in oi if ok(i) not in seen][:5]
    if on:
        new_any = True
        msgs.append("Option ideas scoring 7+ (new):\n" + "\n".join(
            f"{n}. {i['sym']} {i['structs'][i['best']]['name']}, expiry {i['expiry']}, score {i['score']}\n   {' / '.join(i['structs'][i['best']]['legs'])}\n"
            f"   Max loss per lot ₹{i['structs'][i['best']]['risk']:,}; at Target 1 about ₹{i['structs'][i['best']]['pt']:,}" for n, i in enumerate(on, 1)))
    st["o"] = [ok(i) for i in oi]
    if not new_any: msgs.insert(1, "No new setups scoring 7+ today.")
    jl = st.get("j", []); txt = {"STOP": "hit its stop-loss", "T1": "reached Target 1", "T2": "reached Target 2"}
    for line in journal:
        p = [x.strip() for x in line.split(",")]
        try: sym, buy, stop, t1 = p[0].upper(), float(p[1]), float(p[2]), float(p[3]); t2 = float(p[4]) if len(p) > 4 and p[4] else None
        except Exception: continue
        b = len(p) > 5 and p[5].lower() == "bear"; px = out["px"].get(sym)
        if px is None: continue
        ev = ("STOP" if (px >= stop if b else px <= stop) else "T2" if t2 and (px <= t2 if b else px >= t2) else "T1" if (px <= t1 if b else px >= t1) else None)
        if ev and f"{sym}:{buy}:{ev}" not in jl:
            msgs.append(f"Journal: {sym} {txt[ev]}. Price {px}, entry {buy} ({(px / buy - 1) * 100 * (-1 if b else 1):+.1f}% in your favour)."); jl.append(f"{sym}:{buy}:{ev}")
    st["j"] = jl
    o, _, rp = os.environ.get("GITHUB_REPOSITORY", "").partition("/")
    tg("\n\n".join(msgs) + (f"\n\nhttps://{o}.github.io/{rp}/" if rp else ""))
    json.dump(st, open("docs/sent.json", "w"))


def main():
    meta = universe(); data = load([s + ".NS" for s in meta]); nf = load(["^NSEI"])["^NSEI"]
    ret = lambda d, k: d.c.iloc[-1] / d.c.iloc[-k - 1] - 1 if len(d) > k else np.nan
    rs = pd.Series({t: .4 * ret(d, 63) + .3 * ret(d, 126) + .3 * ret(d, 252) for t, d in data.items()}).rank(pct=True)
    sdf = pd.DataFrame({"sec": [meta[t[:-3]]["sector"] for t in rs.index], "rs": rs.values})
    sec_rank = sdf.groupby("sec").rs.mean().rank(pct=True)
    n = nf.c; a50, a200 = bool(n.iloc[-1] > n.rolling(50).mean().iloc[-1]), bool(n.iloc[-1] > n.rolling(200).mean().iloc[-1])
    b50 = float(np.mean([d.c.iloc[-1] > d.c.rolling(50).mean().iloc[-1] for d in data.values() if len(d) > 60]) * 100)
    b200 = float(np.mean([d.c.iloc[-1] > d.c.rolling(200).mean().iloc[-1] for d in data.values() if len(d) > 210]) * 100)
    health = (a50 + a200 + (b50 > 50)) / 3
    rows = {"D": [], "W": [], "M": []}; bear = {"D": [], "W": [], "M": []}
    for t, d in data.items():
        sym = t[:-3]; m = meta[sym]
        if len(d) < 260 or np.isnan(rs.get(t, np.nan)): continue
        if (d.v.iloc[-20:] * d.c.iloc[-20:]).mean() < MIN_TURNOVER: continue
        va = d.v.iloc[-21:-1].mean(); vr = float(d.v.iloc[-1] / va) if va > 0 else 1.
        sec = float(sec_rank.get(m["sector"], .5))
        ctx = dict(rs=float(rs[t]), vr=vr, ms=.5 * sec + .5 * health)
        ctxb = dict(rs=1 - float(rs[t]), vr=vr, ms=.5 * (1 - sec) + .5 * (1 - health))
        for tf in rows:
            x = resample(d, tf)
            for dst, fn, cx in ((rows, analyze, ctx), (bear, analyze_bear, ctxb)):
                try: res = fn(x, tf, cx)
                except Exception as e: print("skip", sym, tf, e); res = None
                if res: res.update(sym=sym, name=m["name"], sector=m["sector"], uni=m["uni"]); dst[tf].append(res)
    for tf in rows: rows[tf].sort(key=lambda r: -r["score"]); bear[tf].sort(key=lambda r: -r["score"])
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=5.5)))
    out = dict(updated=now.strftime("%d %b %Y, %H:%M IST"), last_bar=nf.index[-1].strftime("%d %b %Y"),
               market=dict(nifty=round(float(n.iloc[-1]), 2), a50=a50, a200=a200, b50=round(b50), b200=round(b200),
                           mood="Healthy" if health == 1 else "Mixed" if health >= .66 else "Weak"), rows=rows, bear=bear,
               px={t[:-3]: round(float(d.c.iloc[-1]), 2) for t, d in data.items()})
    os.makedirs("docs", exist_ok=True)
    txt = json.dumps(out, separators=(",", ":"), default=lambda x: x.item() if hasattr(x, "item") else str(x))
    open("docs/data.json", "w", encoding="utf-8").write(txt.replace("NaN", "null"))
    print({k: len(v) for k, v in rows.items()}, "results")
    watch = {x.upper() for x in read_list("watchlist.txt")}; journal = read_list("journal.txt")
    extra = list(watch | {l.split(",")[0].strip().upper() for l in journal})
    for tf in rows:  # chart data for the top 80 of each timeframe plus watchlist and journal stocks
        ch = {}
        for sy in dict.fromkeys([r["sym"] for r in rows[tf][:80]] + [r["sym"] for r in bear[tf][:80]] + extra):
            if sy + ".NS" in data and len(data[sy + ".NS"]) > 60:
                try: ch[sy] = chart_data(data[sy + ".NS"], tf)
                except Exception as e: print("chart", sy, tf, e)
        open(f"docs/charts_{tf}.json", "w").write(json.dumps(ch, separators=(",", ":")))
    ideas = []
    try:  # option ideas for the best bullish (calls) and bearish (puts) setups, plus Nifty and Bank Nifty
        import options
        ext = load(["^NSEBANK", "^INDIAVIX"]); idx = {}
        cands = [(r["sym"], "bull", r) for r in rows["D"][:30] if r["score"] >= 6] + [(r["sym"], "bear", r) for r in bear["D"][:30] if r["score"] >= 6]
        for nm_, d in (("NIFTY", nf), ("BANKNIFTY", ext.get("^NSEBANK"))):
            if d is None or len(d) < 260: continue
            c = d.c; idx[nm_] = dict(spot=round(float(c.iloc[-1]), 2), chg=round(float((c.iloc[-1] / c.iloc[-2] - 1) * 100), 2),
                                     a50=bool(c.iloc[-1] > c.rolling(50).mean().iloc[-1]), a200=bool(c.iloc[-1] > c.rolling(200).mean().iloc[-1]))
            for side, fn, ms in (("bull", analyze, health), ("bear", analyze_bear, 1 - health)):
                res = fn(d, "D", dict(rs=.5, vr=1., ms=ms))
                if res: res.update(sym=nm_, name=nm_); cands.append((nm_, side, res))
        summary, ideas = options.run(cands, idx, nf.index[-1])
        vx = ext.get("^INDIAVIX"); summary["vix"] = round(float(vx.c.iloc[-1]), 1) if vx is not None else None
        out["px"].update({k: v["spot"] for k, v in idx.items()})
        jd = lambda x: x.item() if hasattr(x, "item") else str(x)
        open("docs/options.json", "w", encoding="utf-8").write(json.dumps(summary, separators=(",", ":"), default=jd).replace("NaN", "null"))
        open("docs/data.json", "w", encoding="utf-8").write(json.dumps(out, separators=(",", ":"), default=jd).replace("NaN", "null"))
    except Exception as e: print("options failed:", e)
    try: alerts(out, rows, bear, watch, journal, nf.index[-1], ideas)
    except Exception as e: print("alerts failed:", e)


if __name__ == "__main__":
    main()
