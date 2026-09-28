#!/usr/bin/env bash
# Upload the built distributions to PyPI. Uses ~/.pypirc (mode 600, an account-wide
# token) when it exists; otherwise prompts for a token and keeps it in this shell only.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=~/.venvs/cachemiss/bin/python
"$PY" -m pip install -q twine
FILES=(dist/cachemiss-*.whl dist/cachemiss-*.tar.gz)
ls "${FILES[@]}"
"$PY" -m twine check "${FILES[@]}"
if [ -r ~/.pypirc ]; then
  "$PY" -m twine upload --skip-existing "${FILES[@]}"
else
  read -r -s -p "PyPI token (input hidden): " TOKEN; echo
  TWINE_USERNAME=__token__ TWINE_PASSWORD="$TOKEN" "$PY" -m twine upload --skip-existing "${FILES[@]}"
  unset TOKEN
fi
echo "done: https://pypi.org/project/cachemiss/"
