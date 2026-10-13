@echo off
cd /d "%~dp0"
py -3 -B app.py
if errorlevel 1 pause
