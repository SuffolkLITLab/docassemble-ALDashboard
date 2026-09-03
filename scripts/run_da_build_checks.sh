#!/usr/bin/env bash

set -euo pipefail

python -m compileall . -q
uv build --sdist --wheel --out-dir dist/

mapfile -d '' da_files < <(
  find . \
    \( \
      \( -name "*.yml" -path "./docassemble/*/questions/*" \) -o \
      \( -name "*.docx" -path "./docassemble/*/data/templates/*" \) \
    \) -print0
)

if ((${#da_files[@]})); then
  python -m dayamlchecker \
    --docx-accessibility-severity warning \
    "${da_files[@]}"
fi

python -m dayamlchecker.check_questions_urls
