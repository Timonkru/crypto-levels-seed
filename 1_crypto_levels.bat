@echo off
rem ============================================================
rem  CRYPTO LEVELS BUILD -- run once daily (any time, 24/7
rem  market; pick a fixed ritual hour and FREEZE afterwards).
rem  Pulls Deribit options + Binance derivatives + OKX liqs,
rem  computes the levels, then puts the Pine code on the
rem  CLIPBOARD.
rem ============================================================
cd /d "%~dp0"
echo === Crypto levels build (BTC + ETH) ===
echo.
python build_seed.py
if errorlevel 1 (
    echo.
    echo ERROR - check the output above. Old Pine stays valid.
    pause
    exit /b 1
)
clip < CryptoLevels_auto.pine
echo.
echo ============================================================
echo  DONE. Pine code is on the CLIPBOARD.
echo  TradingView Pine editor: Ctrl+A, Ctrl+V, Save. Freeze.
echo  Note: "(BTC=stored)" in the label means that coin kept
echo  yesterday's levels because a provider failed today.
echo ============================================================
pause
