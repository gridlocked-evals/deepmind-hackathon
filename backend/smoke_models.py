"""Compatibility entry point for the unified gateway smoke command."""
import asyncio
from .gateway.smoke import main

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
