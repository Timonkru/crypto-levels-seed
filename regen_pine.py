"""Rebuild CryptoLevels_auto.pine from the STORED levels (data/levels_*.csv)
without refetching or recomputing anything - for template/layout changes after
the daily freeze. Same role as gamma-seed/regen_pine.py.
"""
from datetime import date

import build_seed as B


def main():
    levels = {}
    for coin, _, _ in B.COINS:
        stored = B.load_stored(coin)
        if stored:
            levels[coin] = stored
    if not levels:
        print("No stored levels - run build_seed.py first.")
        return 1
    out = B.gen_auto_pine(levels, date.today(), note=" (regen)")
    print(f"Pine regenerated from stored levels: {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
