"""Convenience entry point for `python main.py` from the repo root.

Equivalent to `python -m xbook`. The actual CLI lives in xbook/cli.py.
"""
import sys

from xbook.cli import main

if __name__ == "__main__":
    sys.exit(main())
