@echo off
rem Coin monitor for phone/tablet on the same Wi-Fi. The PIN and address are shown below.
cd /d "%~dp0"
.venv\Scripts\python.exe crypto_live\server.py 8765 --lan --engine
pause
