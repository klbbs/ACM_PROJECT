#!/usr/bin/env python3
"""Entry point for the one-command ACM Lab updater.

Run through ``./update.sh`` (preferred) or directly:

    python3 scripts/update.py --help
"""
import sys
from pathlib import Path

# A clean deployment's first preflight must not leave bytecode caches behind.
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from acmweb.updater import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
