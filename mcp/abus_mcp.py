#!/usr/bin/env python3
import sys
from pathlib import Path

for parent in Path(__file__).resolve().parents:
    if (parent / "abus/__init__.py").is_file():
        sys.path.insert(0, str(parent))
        break
from abus.mcp import main

main()
