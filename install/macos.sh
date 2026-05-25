#!/usr/bin/env bash
set -euo pipefail

PACKAGE_SPEC="${PACKAGE_SPEC:-llm-translation-pipeline[all] @ git+https://github.com/PhilippeTrounev/llm-translation-pipeline.git}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required. Install Python 3.10+ first." >&2
  exit 1
fi

python3 -m pip install --user --upgrade pipx
python3 -m pipx ensurepath
python3 -m pipx install "${PACKAGE_SPEC}" --force

if ! command -v pandoc >/dev/null 2>&1; then
  echo
  echo "Optional: install pandoc for DOCX export:"
  echo "  brew install pandoc"
fi

echo
echo "Installed. Restart your shell if llm-translate is not on PATH."
echo "Run: llm-translate setup"
