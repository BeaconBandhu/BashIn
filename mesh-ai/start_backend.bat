@echo off
:: Use Python 3.11 — required for ML packages (numpy, torch, sentence-transformers)
set PYTHON=py -3.11

cd /d "%~dp0backend"
echo Installing dependencies with Python 3.11...
%PYTHON% -m pip install -r requirements.txt
echo.
echo Starting MeshAI backend on http://localhost:8000
echo API docs at http://localhost:8000/docs
echo.
%PYTHON% main.py
pause
