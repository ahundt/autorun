#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clautorun - Claude Agent SDK Command Interceptor

Intercepts autorun commands before they reach Claude Code, saving tokens and providing instant responses.
"""
import sys
from pathlib import Path

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from clautorun.main import main

if __name__ == "__main__":
    main()