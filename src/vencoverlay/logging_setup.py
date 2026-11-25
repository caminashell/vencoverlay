"""Application-owned logging resources."""

import logging
import time
from collections.abc import Generator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler

from vencoverlay.config import Configuration
from vencoverlay.paths import ApplicationPaths


class UTCFormatter(logging.Formatter):
    """Format log timestamps in UTC."""

    converter = staticmethod(time.gmtime)


@contextmanager
def application_logging(config: Configuration, paths: ApplicationPaths) -> Generator[None]:
    """Configure the application namespace for the lifetime of the runtime.

    Call once at startup, before starting workers, and exit after they stop.
    Existing application handlers are temporarily detached, not closed.
    """
    logger = logging.getLogger("vencoverlay")
    previous_handlers = logger.handlers[:]
    previous_level = logger.level
    previous_propagate = logger.propagate
    handlers: list[logging.Handler] = []
    formatter = UTCFormatter(
        "%(asctime)s.%(msecs)03dZ %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    try:
        handlers.append(logging.StreamHandler())
        if config.log_to_file:
            paths.state_dir.mkdir(parents=True, exist_ok=True)
            handlers.append(
                RotatingFileHandler(
                    paths.state_dir / "vencoverlay.log",
                    maxBytes=5 * 1024 * 1024,
                    backupCount=3,
                    encoding="utf-8",
                )
            )
        for handler in handlers:
            handler.setFormatter(formatter)
        for handler in previous_handlers:
            logger.removeHandler(handler)
        logger.setLevel(config.log_level)
        logger.propagate = False
        for handler in handlers:
            logger.addHandler(handler)
        yield
    finally:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()
        for handler in previous_handlers:
            logger.addHandler(handler)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
