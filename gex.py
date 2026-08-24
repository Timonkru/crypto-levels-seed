"""
GEX core: Black-Scholes gamma -> gamma exposure -> flip / call wall / put wall / max pain.
Provider-agnostic: takes a standardized option chain plus the spot, computes the levels.

Crypto adaptation of gamma-seed/gex.py (same math, same conventions), with one
deliberate difference: crypto trades 24/7, so time is measured in CALENDAR days
on an ACT/365 basis (DAYS_PER_YEAR = 365) instead of the equity busdays/252
convention. This affects the expected-move scaling and the per-day charm unit.

Standardized chain (pandas.DataFrame), columns:
    strike  : float   strike price (in index/spot space)
    type    : 'C'/'P'
    oi      : float   open interest (in contracts; Deribit options: 1 contract = 1 coin)
    iv      : float   implied vol (decimal, e.g. 0.45)
    T       : float   time to expiry in years (ACT/365)
    mult    : float   contract multiplier (Deribit BTC/ETH options: 1.0)

Sign convention (the standard one, BUT an assumption!): dealers long call gamma,
short put gamma -> call GEX positive, put GEX negative. Flip = spot where total
GEX changes sign. In crypto the dealer-positioning assumption is even less
validated than in index options - treat every level as a model estimate.
"""
import numpy as np

DAYS_PER_YEAR = 365.0  # crypto trades 24/7 -> ACT/365, not busdays/252


def _npdf(x):
    return np.exp(-0.5 * x * x) / np.sqrt(2.0 * np.pi)


def bs_gamma(S, K, T, sigma, r=0.0):
    S = np.asarray(S, float); K = np.asarray(K, float)
    T = np.asarray(T, float); sigma = np.asarray(sigma, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        g = _npdf(d1) / (S * sigma * np.sqrt(T))
    return np.where(np.isfinite(g), g, 0.0)


def _signed_gex_at(chain, S, r=0.0):
    """GEX per option at hypothetical spot S ($ per 1% move). Call +, put -."""
    g = bs_gamma(S, chain["strike"].values, chain["T"].values, chain["iv"].values, r)
    sign = np.where(chain["type"].values == "C", 1.0, -1.0)
    return sign * g * chain["oi"].values * chain["mult"].values * (S ** 2) * 0.01


# ---- Charm & vanna (2nd-order Greeks, r=0). With q=r=0, vanna and charm are
#      IDENTICAL for call and put at the same strike -> dealer aggregation uses
#      the same sign convention as GEX (long call, short put). Model estimate only! ----
def _d1d2(S, K, T, sigma, r=0.0):
    with np.errstate(divide="ignore", invalid="ignore"):
        srt = sigma * np.sqrt(T)
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / srt
        d2 = d1 - srt
    return d1, d2, srt


def bs_vanna(S, K, T, sigma, r=0.0):
    """dDelta/dVol (= dVega/dSpot). Positive on the OTM-call/ITM-put side. Per 1.0 vol (decimal)."""
    S = np.asarray(S, float); K = np.asarray(K, float)
    T = np.asarray(T, float); sigma = np.asarray(sigma, float)
    d1, d2, _ = _d1d2(S, K, T, sigma, r)
    with np.errstate(divide="ignore", invalid="ignore"):
        v = -_npdf(d1) * d2 / sigma
    return np.where(np.isfinite(v), v, 0.0)


def bs_charm(S, K, T, sigma, r=0.0):
    """dDelta/dt (time passes, T shrinks), per YEAR. r=0 -> phi(d1)*d2/(2T)."""
    S = np.asarray(S, float); K = np.asarray(K, float)
    T = np.asarray(T, float); sigma = np.asarray(sigma, float)
    d1, d2, srt = _d1d2(S, K, T, sigma, r)
    with np.errstate(divide="ignore", invalid="ignore"):
        c = -_npdf(d1) * (2.0 * r * T - d2 * srt) / (2.0 * T * srt)
    return np.where(np.isfinite(c), c, 0.0)


def _signed_vanna_at(chain, S, r=0.0):
    """Dealer vanna exposure per option: $-delta shift per 1 vol POINT (0.01)."""
    v = bs_vanna(S, chain["strike"].values, chain["T"].values, chain["iv"].values, r)
    sign = np.where(chain["type"].values == "C", 1.0, -1.0)
    return sign * v * chain["oi"].values * chain["mult"].values * S * 0.01


def _signed_charm_at(chain, S, r=0.0):
    """Dealer charm exposure per option: $-delta shift per CALENDAR DAY (year/365)."""
    c = bs_charm(S, chain["strike"].values, chain["T"].values, chain["iv"].values, r)
    sign = np.where(chain["type"].values == "C", 1.0, -1.0)
    return sign * c * chain["oi"].values * chain["mult"].values * S / DAYS_PER_YEAR


def total_vanna(chain, S, r=0.0):
    return float(_signed_vanna_at(chain, S, r).sum())


def total_charm(chain, S, r=0.0):
    return float(_signed_charm_at(chain, S, r).sum())


def _dominant_strike(chain, vals):
    """Strike with the largest |aggregated| exposure (the 'wall')."""
    agg = {}
    for k, v in zip(chain["strike"].values, vals):
        agg[k] = agg.get(k, 0.0) + v
    if not agg:
        return None
    return float(max(agg, key=lambda k: abs(agg[k])))


def total_gex(chain, S, r=0.0):
    return float(_signed_gex_at(chain, S, r).sum())


def gamma_flip(chain, spot, r=0.0, lo=0.80, hi=1.20, n=900):
    """Spot at which total GEX = 0 (zero crossing nearest to the current spot)."""
    grid = np.linspace(spot * lo, spot * hi, n)
    vals = np.array([total_gex(chain, s, r) for s in grid])
    sign = np.sign(vals)
    cross = np.where(np.diff(sign) != 0)[0]
    if len(cross) == 0:
        return None
    best = None
    for i in cross:
        x0, x1 = grid[i], grid[i + 1]; y0, y1 = vals[i], vals[i + 1]
        z = x0 - y0 * (x1 - x0) / (y1 - y0)
        if best is None or abs(z - spot) < abs(best - spot):
            best = z
    return float(best)


def per_strike_gex(chain, spot, r=0.0):
    gex = _signed_gex_at(chain, spot, r)
    out = {}
    for k, v in zip(chain["strike"].values, gex):
        out[k] = out.get(k, 0.0) + v
    return out  # {strike: net_gex}


def max_pain(chain):
    """Strike that minimizes the total payout to option buyers (classic)."""
    strikes = np.unique(chain["strike"].values)
    K = chain["strike"].values; oi = chain["oi"].values
    isC = chain["type"].values == "C"; mult = chain["mult"].values
    best_E, best_pain = None, None
    for E in strikes:
        callpay = np.maximum(E - K[isC], 0) * oi[isC] * mult[isC]
        putpay = np.maximum(K[~isC] - E, 0) * oi[~isC] * mult[~isC]
        pain = callpay.sum() + putpay.sum()
        if best_pain is None or pain < best_pain:
            best_pain, best_E = pain, E
    return float(best_E)


def compute_levels(chain, spot, r=0.0, neutral_pct=0.3):
    chain = chain.copy()
    chain = chain[(chain["oi"] > 0) & (chain["iv"] > 0) & (chain["T"] > 0)]
    if len(chain) < 4:
        return None
    psg = per_strike_gex(chain, spot, r)
    strikes = np.array(sorted(psg))
    netg = np.array([psg[k] for k in strikes])
    # Call wall = strike with the largest positive GEX; put wall = most negative.
    # Second walls = the next-largest clusters (the "strike shelf" behind them).
    cw = cw2 = pw = pw2 = None
    pos = np.argsort(netg)
    if (netg > 0).any():
        cw = float(strikes[pos[-1]])
        rest = [strikes[j] for j in pos[::-1][1:]
                if netg[j] > 0 and abs(strikes[j] - cw) > 1e-9]
        cw2 = float(rest[0]) if rest else None
    if (netg < 0).any():
        pw = float(strikes[pos[0]])
        rest = [strikes[j] for j in pos[1:]
                if netg[j] < 0 and abs(strikes[j] - pw) > 1e-9]
        pw2 = float(rest[0]) if rest else None
    tg = total_gex(chain, spot, r)
    flip = gamma_flip(chain, spot, r)
    # Regime with a flip ZONE: close to the flip is no signal, just no-man's-land
    if flip is None:
        regime = "long" if tg > 0 else "short"
    else:
        d = (spot - flip) / spot * 100
        regime = "neutral" if abs(d) < neutral_pct else ("long" if d > 0 else "short")
    # Expected move (1 day) from ATM IV: spot * iv * sqrt(1/365) - ACT/365 (24/7 market)
    atm = chain.loc[(chain["strike"] - spot).abs().sort_values().index[:6], "iv"]
    atm_iv = float(atm.median()) if len(atm) else None
    em_1d = (spot * atm_iv / np.sqrt(DAYS_PER_YEAR)) if atm_iv else None
    tv = total_vanna(chain, spot, r)
    tc = total_charm(chain, spot, r)
    vanna_strike = _dominant_strike(chain, _signed_vanna_at(chain, spot, r))
    charm_strike = _dominant_strike(chain, _signed_charm_at(chain, spot, r))
    return {
        "spot": float(spot),
        "total_gex": tg,
        "regime": regime,
        "gamma_flip": flip,
        "call_wall": cw, "call_wall2": cw2,
        "put_wall": pw, "put_wall2": pw2,
        "max_pain": max_pain(chain),
        "atm_iv": (round(atm_iv, 4) if atm_iv else None),
        "exp_move_1d": (round(em_1d, 2) if em_1d else None),
        "dist_to_flip_pct": (round((spot - flip) / spot * 100, 2) if flip else None),
        # Vanna>0: vol DOWN -> dealers BUY (vanna rally fuel in the grind);
        #          vol UP -> dealers sell (downside accelerant).
        "total_vanna": tv,
        "vanna_flow": ("vol-down=buy" if tv > 0 else "vol-down=sell"),
        "vanna_strike": vanna_strike,
        # Charm>0: as time passes -> dealers BUY (support into expiry).
        "total_charm": tc,
        "charm_flow": ("time=buy-support" if tc > 0 else "time=sell-pressure"),
        "charm_strike": charm_strike,
    }
