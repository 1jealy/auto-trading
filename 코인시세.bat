@echo off
rem Coin price app - closing this window stops the app.
cd /d "%~dp0"
start "" http://127.0.0.1:8765
.venv\Scripts\python.exe crypto_live\server.py 8765 --engine
