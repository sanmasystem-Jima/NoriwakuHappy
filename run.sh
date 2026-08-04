#!/bin/bash
# 法枠Happy 起動スクリプト
set -e
cd "$(dirname "$(readlink -f "$0")")"

if [ ! -d ".venv" ]; then
    echo "初回セットアップ: 仮想環境(.venv)を作成しています..."
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi

exec .venv/bin/python3 main.py
