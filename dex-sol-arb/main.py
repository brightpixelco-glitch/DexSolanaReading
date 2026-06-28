#!/usr/bin/env python3
"""Entry point. Run with --ui to launch the web interface, or without for CLI mode."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))


def main():
    if "--ui" in sys.argv:
        from src.ui.app import start_ui
        start_ui()
    else:
        from src.bot import run_bot
        asyncio.run(run_bot())


if __name__ == "__main__":
    main()
