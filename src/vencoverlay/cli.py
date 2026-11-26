"""Command-line entry point for VencOverlay."""

import argparse
import sys
from importlib.metadata import version

from vencoverlay.application import run
from vencoverlay.backend import select_backend
from vencoverlay.runtime import check_runtime


def main() -> int:
    """Parse command-line arguments and dispatch the application."""
    parser = argparse.ArgumentParser(
        prog="vencoverlay",
        description="A transparent Discord voice and webcam overlay for games.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--version", action="version", version=f"VencOverlay {version('vencoverlay')}"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--demo", action="store_true", help="Run an offline animated overlay demonstration"
    )
    modes.add_argument(
        "--check-runtime",
        action="store_true",
        help="Check native graphics without starting the overlay or listener",
    )
    args = parser.parse_args()
    if args.check_runtime:
        try:
            check_runtime(select_backend())
        except (ImportError, OSError, RuntimeError, ValueError, TypeError) as exc:
            print(f"VencOverlay runtime check: {exc}", file=sys.stderr)
            return 1
        print("VencOverlay runtime check passed")
        return 0
    return run(demo=True) if args.demo else run()
