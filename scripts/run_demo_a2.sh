#!/usr/bin/env bash
set -euo pipefail

CONFIG="${1:-configs/demo_a2.yaml}"

if [[ -z "${NIAGARA_USER:-}" ]]; then
  read -r -p "Enter NIAGARA_USER: " NIAGARA_USER
  export NIAGARA_USER
fi

if [[ -z "${NIAGARA_PASS:-}" ]]; then
  read -r -s -p "Enter NIAGARA_PASS: " NIAGARA_PASS
  echo
  export NIAGARA_PASS
fi

if command -v ufw >/dev/null 2>&1; then
  ufw allow 47808/udp >/dev/null 2>&1 || true
fi

python -m gateway.demo_a2_mirror --config "$CONFIG"