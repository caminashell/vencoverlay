"""Application execution boundary, independent of command-line parsing."""

import sys


def run() -> int:
    """Execute the application and return its exit status.

    Runtime resources will be acquired and released here as they are migrated.
    No resources are acquired by the current placeholder.
    """
    print("VencOverlay: application runtime not yet implemented.", file=sys.stderr)
    return 1
