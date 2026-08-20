#!/usr/bin/env bash
# 개발 서버 실행 (http://localhost:8000, docs: /docs)
set -euo pipefail
cd "$(dirname "$0")/.."
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
