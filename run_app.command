#!/bin/bash
# Double-click launcher for macOS (first time: right-click > Open, or run "chmod +x run_app.command").
cd "$(dirname "$0")" || exit 1
if [ ! -d ".venv" ]; then
  echo "First run: creating a private Python environment (about a minute)..."
  python3 -m venv .venv || { echo "python3 not found. Install Python 3.10+ from python.org"; exit 1; }
  .venv/bin/pip install --upgrade pip >/dev/null
  .venv/bin/pip install -r requirements.txt || exit 1
fi
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env. Open it in TextEdit, add your keys (optional), save, then run this launcher again."
  open -e .env
  exit 0
fi
exec .venv/bin/streamlit run app.py
