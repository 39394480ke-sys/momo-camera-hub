#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f config.local.yaml ]]; then
  cp config.example.yaml config.local.yaml
fi

exec uv run momo-camera-hub --config config.local.yaml
