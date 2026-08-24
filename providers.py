"""
Data providers for the crypto levels seed. All endpoints are PUBLIC (no key, no
account, no auth) - same spirit as the Eurex statistics API in gamma-seed.

    deribit_chain(currency)   -> Chain(df, spot): full option board with OI + mark IV
    binance_snapshot(symbol)  -> dict: funding, OI (+24h delta), long/short ratios
    okx_liq_clusters(uly, ...)-> recent liquidation prints clustered into price levels

Every provider is wrapped by the caller in try/except - a dead endpoint degrades
that block to zeros instead of killing the build (fail-soft, levels keep the
previous day via the stored CSVs).
"""
from collections import namedtuple
from datetime import datetime, timezone
import json
import time
import urllib.request

import pandas as pd

Chain = namedtuple("Chain", "df spot")

_UA = {"User-Agent": "Mozilla/5.0 (crypto-levels-seed)"}


def _get(url, tries=3, sleep=0.4):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001 - retry any transport error
            last = e
            time.sleep(sleep)
    raise RuntimeError(f"GET {url} failed: {last}")


# ---------------------------------------------------------------- Deribit ----
def deribit_chain(currency="BTC"):
    """Full option board in ONE call: OI (coins), mark IV, underlying per expiry.

    Strikes stay NOMINAL. Deribit options settle on futures (one underlying per
    expiry, small basis vs the index), and adjusting strikes by the basis would
    turn the same nominal strike into different floats per expiry - which breaks
    the per-strike wall aggregation (the gamma-seed v1.2.1 lesson: walls MUST
    aggregate per strike). Within the 7-45 DTE structure window the basis is
    well under 1%, so evaluating nominal strikes at the index spot is the
    smaller error. First build proved it: with adjusted strikes, CW == PW.
    """
    idx = _get(f"https://www.deribit.com/api/v2/public/get_index_price"
               f"?index_name={currency.lower()}_usd")["result"]["index_price"]
    rows = _get(f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
                f"?currency={currency}&kind=option")["result"]
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        oi = float(r.get("open_interest") or 0.0)
        iv = float(r.get("mark_iv") or 0.0) / 100.0
        und = float(r.get("underlying_price") or 0.0)
        if oi <= 0 or iv <= 0 or und <= 0:
            continue
        # instrument_name: BTC-25JUN27-150000-P
        try:
            _, exps, ks, cp = r["instrument_name"].split("-")
            exp = datetime.strptime(exps, "%d%b%y").replace(
                hour=8, minute=0, tzinfo=timezone.utc)  # Deribit expiry 08:00 UTC
            strike = float(ks)
        except (ValueError, KeyError):
            continue
        dte = (exp - now).total_seconds() / 86400.0
        if dte <= 0:
            continue
        out.append({
            "strike": strike,               # nominal (see docstring: no basis adj.)
            "type": "C" if cp == "C" else "P",
            "oi": oi,                        # Deribit options: 1 contract = 1 coin
            "iv": iv,
            "T": dte / 365.0,                # ACT/365 (24/7 market)
            "dte": dte,
            "mult": 1.0,
        })
    df = pd.DataFrame(out)
    print(f"[deribit] {currency}: {len(df)} option rows with OI, index {idx:.0f}")
    return Chain(df=df, spot=float(idx))


# ---------------------------------------------------------------- Binance ----
def binance_snapshot(symbol="BTCUSDT"):
    """Derivatives snapshot: funding (real prints), OI + 24h delta, positioning."""
    prem = _get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}")
    oi = _get(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}")
    mark = float(prem["markPrice"])
    oi_coins = float(oi["openInterest"])
    snap = {
        "funding_8h_pct": float(prem["lastFundingRate"]) * 100.0,
        "oi_coins": oi_coins,
        "oi_usd_bn": oi_coins * mark / 1e9,
        "oi_d24_pct": 0.0,
        "global_ls": 0.0,
        "top_ls": 0.0,
    }
    try:
        hist = _get(f"https://fapi.binance.com/futures/data/openInterestHist"
                    f"?symbol={symbol}&period=1h&limit=25")
        if len(hist) >= 2:
            a = float(hist[0]["sumOpenInterest"]); b = float(hist[-1]["sumOpenInterest"])
            if a > 0:
                snap["oi_d24_pct"] = (b / a - 1.0) * 100.0
    except Exception as e:  # noqa: BLE001
        print(f"[binance] {symbol}: OI history failed ({e})")
    try:
        gls = _get(f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
                   f"?symbol={symbol}&period=1h&limit=1")
        snap["global_ls"] = float(gls[-1]["longShortRatio"]) if gls else 0.0
    except Exception as e:  # noqa: BLE001
        print(f"[binance] {symbol}: global L/S failed ({e})")
    try:
        tls = _get(f"https://fapi.binance.com/futures/data/topLongShortPositionRatio"
                   f"?symbol={symbol}&period=1h&limit=1")
        snap["top_ls"] = float(tls[-1]["longShortRatio"]) if tls else 0.0
    except Exception as e:  # noqa: BLE001
        print(f"[binance] {symbol}: top-trader L/S failed ({e})")
    print(f"[binance] {symbol}: funding {snap['funding_8h_pct']:+.4f}%/8h | "
          f"OI {snap['oi_coins']:,.0f} ({snap['oi_d24_pct']:+.1f}% 24h) | "
          f"L/S {snap['global_ls']:.2f} top {snap['top_ls']:.2f}")
    return snap


# -------------------------------------------------------------------- OKX ----
def okx_liq_clusters(uly="BTC-USDT", spot=None, n_levels=3, bin_pct=0.4):
    """Recent liquidation PRINTS (OKX public feed) clustered into price levels.

    These are PAST forced-flow events, not a forward heatmap: a cluster marks
    where leveraged positions were flushed recently - context levels, nothing
    predictive by itself. dir=+1: shorts were liquidated (forced BUY flow),
    dir=-1: longs were liquidated (forced SELL flow).
    """
    data = _get(f"https://www.okx.com/api/v5/public/liquidation-orders"
                f"?instType=SWAP&uly={uly}&state=filled")["data"]
    det = data[0]["details"] if data else []
    ct_val = 0.01  # contract value in coins; fetched live below
    try:
        inst = _get(f"https://www.okx.com/api/v5/public/instruments"
                    f"?instType=SWAP&instId={uly}-SWAP")["data"]
        if inst:
            ct_val = float(inst[0]["ctVal"])
    except Exception:  # noqa: BLE001 - keep default
        pass
    if not det:
        print(f"[okx] {uly}: no liquidation prints")
        return []
    px = [float(d["bkPx"]) for d in det]
    ref = spot or (sorted(px)[len(px) // 2])
    width = ref * bin_pct / 100.0
    bins = {}
    for d in det:
        p = float(d["bkPx"]); w = float(d["sz"]) * ct_val
        b = round(p / width)
        e = bins.setdefault(b, {"w": 0.0, "pw": 0.0, "dir": 0.0})
        e["w"] += w; e["pw"] += p * w
        e["dir"] += w if d.get("posSide") == "short" else -w
    top = sorted(bins.values(), key=lambda e: -e["w"])[:n_levels]
    out = [{"price": e["pw"] / e["w"], "dir": 1 if e["dir"] >= 0 else -1,
            "coins": e["w"]} for e in top if e["w"] > 0]
    lvl_txt = ", ".join(f"{o['price']:.0f}({'S' if o['dir'] > 0 else 'L'} {o['coins']:.1f})"
                        for o in out)
    print(f"[okx] {uly}: {len(det)} prints -> clusters {lvl_txt}")
    return out
