# crypto-levels-seed

Daily BTC/ETH derivative levels for TradingView — computed in Python from **public
APIs** (no keys, no accounts), baked into a generated Pine v6 script, pasted once
per day into the Pine editor. Sibling project of
[gamma-seed](https://github.com/Timonkru/gamma-seed) (same operating model for
index options), adapted for a 24/7 market.

Pine cannot call external APIs. This is the escape hatch: the data run happens
outside TradingView, the chart only renders frozen numbers.

## What it draws

| Layer | Source | Levels |
|---|---|---|
| **Structure map** (7–45 DTE) | Deribit option board (OI + mark IV) | Gamma flip, call/put walls + second walls, max pain, expected move ±1d |
| **Near map** (0–5 DTE) | Deribit | 0DTE call/put walls, near flip |
| **Flow** (model estimate) | Deribit | Vanna & charm totals + dominant strikes |
| **Derivatives snapshot** | Binance Futures | Funding (real prints), OI in $bn + 24h delta, global long/short account ratio, top-trader position ratio — shown in the label |
| **Liquidation clusters** | OKX liquidation prints | Top 3 recent forced-flow price clusters (lime = short liqs, maroon = long liqs) |
| **Vol regime** | `DERIBIT:DVOL` / `ETHDVOL` (live in Pine) | Tertile regime + direction in the label |

## Daily ritual

```
python build_seed.py        (or double-click 1_crypto_levels.bat)
```

→ prints a summary, writes `CryptoLevels_auto.pine`, the `.bat` puts it on the
clipboard. Paste into the TradingView Pine editor (Ctrl+A, Ctrl+V), save,
**freeze until tomorrow**. The label shows the level date and turns orange with
a STALE warning after 36h. Coin auto-detection from the chart symbol (BTC/ETH),
manual offset input for broker basis.

## Legend (what every line is)

| Look | Level | Nature |
|---|---|---|
| Yellow thick | Gamma flip (7-45 DTE) | model (dealer-sign assumption) |
| Red / green thick | Call wall / put wall | model on real OI |
| Red / green dashed | Second walls (strike shelf) | model on real OI |
| Orange / teal dotted | 0DTE call / put wall (0-5 DTE) | model on real OI |
| Blue dotted pair | Expected move +/-1d (ATM IV, ACT/365) | real IV, mechanical |
| Grey dotted (off) | Max pain | classic calculation |
| Purple / dark cyan dotted | Vanna / charm strike | model, unvalidated - context only |
| Dark green dotted, w2 | Liq cluster: SHORTS flushed = forced BUYING happened there | real past events (OKX) |
| Dark red dotted, w2 | Liq cluster: LONGS flushed = forced SELLING | real past events (OKX) |
| Background green/red/grey | Long gamma / short gamma / flip zone | derived from flip |

Label lines: regime + flip distance + net GEX | vanna/charm flows | Binance
funding / OI +24h delta / global L-S account ratio / top-trader position ratio |
DVOL tertile regime (the only LIVE value, via `DERIBIT:DVOL`) | level date
(`(regen)` = layout rebuilt, numbers unchanged; `(COIN=stored)` = provider
failed, yesterday's numbers; orange STALE after 36h).

Time windows: option levels + derivatives snapshot = frozen at build time.
Liq clusters = the most recent ~1600 OKX prints - roughly 24h on quiet days,
elastically SHORTER on cascade days (the window is activity-dependent).

## Honest limitations

- **Every level is a model estimate.** GEX/flip/walls assume the standard
  dealer-sign convention (long calls, short puts) — an assumption, in crypto
  even less validated than in index options. Context, not signals.
- **Liquidation clusters are PAST events**, not a forward heatmap. Forward
  liquidation maps (Coinglass-style) are estimates everywhere — nobody sees
  resting liquidation levels; we deliberately only draw what actually printed.
- Strikes are used nominally although Deribit options settle on futures with a
  small basis vs the index (<1% inside the 45-DTE window). Adjusting strikes
  per expiry breaks per-strike wall aggregation and produced provably broken
  walls (CW == PW) — nominal strikes are the smaller error.
- Snapshot, not live: levels update once per day by design (freeze discipline —
  intraday recomputation of structure levels is a repaint trap).
- Funding/positioning snapshot is Binance-only; liq prints are OKX-only.
  Single-venue proxies for a multi-venue market.

## Files

| File | Purpose |
|---|---|
| `build_seed.py` | Orchestrator + Pine template (pure ASCII) + level persistence |
| `gex.py` | Black-Scholes gamma/vanna/charm → flip/walls/max pain (ACT/365 crypto convention) |
| `providers.py` | Deribit / Binance / OKX public API clients, fail-soft |
| `1_crypto_levels.bat` | Double-click ritual: build + clipboard |

MIT License.
