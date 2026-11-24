"""Command-line entry point for VencOverlay."""

import argparse
from importlib.metadata import version

from vencoverlay.application import run


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
    parser.parse_args()
    return run()
