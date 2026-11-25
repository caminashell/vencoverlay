"""Application execution boundary, independent of command-line parsing."""

import logging
import sys
from dataclasses import replace

from vencoverlay.backend import select_backend
from vencoverlay.config import load_config
from vencoverlay.logging_setup import application_logging
from vencoverlay.paths import resolve_paths
from vencoverlay.runtime import run_runtime

logger = logging.getLogger(__name__)


def run(*, demo: bool = False) -> int:
    """Load local configuration and execute the application."""
    try:
        paths = resolve_paths()
        config = load_config(paths.config_file)
        if demo:
            config = replace(config, log_to_file=False)
        with application_logging(config, paths):
            try:
                selection = select_backend()
                if demo:
                    return run_runtime(config, paths, selection, demo=True)
                return run_runtime(config, paths, selection)
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                logger.error("Unable to run VencOverlay: %s", exc)
                return 1
    except (OSError, ValueError) as exc:
        print(f"VencOverlay: {exc}", file=sys.stderr)
        return 1
