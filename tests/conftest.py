"""Shared test selection policy."""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--graphical",
        action="store_true",
        default=False,
        help="Allow tests requiring a graphical desktop.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--graphical"):
        return
    skip = pytest.mark.skip(reason="requires explicit --graphical opt-in")
    for item in items:
        if item.get_closest_marker("graphical") is not None:
            item.add_marker(skip)
