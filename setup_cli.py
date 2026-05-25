#!/usr/bin/env python3
"""Run the CLI directly from a source checkout without installing the package."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_translation_pipeline.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
