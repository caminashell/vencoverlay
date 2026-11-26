"""Version-specific supplemental notice collection."""

from pathlib import Path

import pytest

from scripts import release_sources


def test_reviewed_notices() -> None:
    root = Path(__file__).resolve().parents[2]
    npm = {"@vap+core-0.0.12.tgz": ("url", "hash")}
    notices = release_sources.supplemental_notices(root, release_sources.REVIEWED_VENCORD, npm)
    assert set(notices) == set(release_sources.SUPPLEMENTAL_NOTICES)
    assert b"Justice Almanzar" in notices["notices/npm/@vap+core-0.0.12.tgz/LICENSE"]
    assert b"2024 Discord, Inc." in notices["notices/vendored/discord-intl-MIT.txt"]
    assert b"David Mzareulyan" in notices["notices/vendored/apng-canvas-MIT.txt"]
    assert b"Mozilla Public License Version 2.0" in notices["notices/vendored/crxToZip-MPL-2.0.txt"]


def test_reviewed_notice_scope(tmp_path: Path) -> None:
    npm = {"@vap+core-0.0.12.tgz": ("url", "hash")}
    with pytest.raises(ValueError, match="Vencord pin"):
        release_sources.supplemental_notices(tmp_path, "different", npm)
    with pytest.raises(ValueError, match="npm inventory"):
        release_sources.supplemental_notices(tmp_path, release_sources.REVIEWED_VENCORD, {})
    with pytest.raises(ValueError, match="Missing reviewed notice"):
        release_sources.supplemental_notices(tmp_path, release_sources.REVIEWED_VENCORD, npm)
