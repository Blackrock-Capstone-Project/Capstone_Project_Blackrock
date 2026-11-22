#!/usr/bin/env python3
"""Compatibility pull entry. Use python -m scripts.data_pipeline for update/process/audit."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.data_pipeline.taq_sql import day_sql, finalize, CHECKPOINTS, CROSS_VENUES


def main():
    from scripts.data_pipeline.__main__ import main as pipeline_main
    argv = sys.argv[1:]
    if argv and argv[0] == 'pull':
        argv[0] = 'update'
    return pipeline_main(argv)


if __name__ == '__main__':
    raise SystemExit(main())
