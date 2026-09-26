#!/usr/bin/env bash
# Upload the built distributions to PyPI. Prompts for the token; it is never echoed,
# never written to disk, and never leaves this shell.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=~/.venvs/cachemiss/bin/python
"$PY" -m pip install -q twine
ls dist/*.whl dist/*.tar.gz
"$PY" -m twine check dist/*
read -r -s -p "PyPI token (input hidden): " TOKEN; echo
TWINE_USERNAME=__token__ TWINE_PASSWORD="$TOKEN" "$PY" -m twine upload dist/*
unset TOKEN
echo "done: https://pypi.org/project/cachemiss/"
