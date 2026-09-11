#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -d .venv ] || { echo "Chưa cài. Chạy: bash setup.sh"; exit 1; }
npm --prefix desktop start
