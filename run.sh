#!/usr/bin/env bash
# Spouštěč hlídače nájemních bytů (Mac / Linux).
# Používá se v cronu nebo launchd — postará se o správný adresář i virtuální prostředí.
cd "$(dirname "$0")" || exit 1
if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
python3 main.py "$@"
