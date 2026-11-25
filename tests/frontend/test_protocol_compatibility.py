"""Cross-language tests against the real TypeScript implementation (opt-in)."""

import json
import subprocess
from pathlib import Path

import pytest

from vencoverlay.protocol import (
    MAX_FRAME_BYTES,
    MAX_FRAME_HEIGHT,
    MAX_FRAME_WIDTH,
    MAX_MESSAGE_BYTES,
    FrameMessage,
    SpeakingMessage,
    StateMessage,
    ToastMessage,
    parse_message,
)

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugin/vencoverlay"
NODE_MODULES = ROOT / "build/vencord-dev/source/node_modules"
RUNNER = Path(__file__).with_name("protocol_runner.cjs")


@pytest.fixture(scope="module")
def compiled_protocol(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    if not request.config.getoption("--frontend"):
        pytest.skip("requires explicit --frontend opt-in")
    compiler = NODE_MODULES / ".bin/tsc"
    assert compiler.is_file(), "Run ./setup.sh --frontend first"
    output = tmp_path_factory.mktemp("frontend-protocol")
    subprocess.run(
        [
            str(compiler),
            "--strict",
            "--target",
            "ES2022",
            "--module",
            "commonjs",
            "--lib",
            "ES2022,DOM",
            "--skipLibCheck",
            "--outDir",
            str(output),
            str(PLUGIN / "protocol.ts"),
            str(PLUGIN / "voice.ts"),
            str(PLUGIN / "webcam.ts"),
        ],
        cwd=ROOT,
        check=True,
        timeout=60,
    )
    return output / "protocol.js"


def run_protocol(compiled: Path, mode: str) -> list[bytes]:
    result = subprocess.run(
        [
            "node",
            str(RUNNER),
            str(compiled),
            mode,
            str(NODE_MODULES / "typescript/lib/typescript.js"),
            str(PLUGIN / "native.ts"),
            str(PLUGIN / "index.ts"),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout.splitlines()


def test_typescript_messages_parse_in_python(compiled_protocol: Path) -> None:
    active, idle, speaking, join, leave, frame = (
        parse_message(raw) for raw in run_protocol(compiled_protocol, "wire")
    )
    assert isinstance(active, StateMessage)
    assert active.channel is not None
    assert active.channel.id == "1"
    assert active.users[0].name == "Zoë 🎮"
    assert active.users[0].deaf and active.users[0].video and active.users[0].live
    assert active.preferences.position == "middle-right"
    assert active.preferences.style == "mac"
    assert active.preferences.scale == 1.8
    assert active.preferences.opacity == 0.3
    assert active.preferences.accent == "ABCDEF"
    assert active.preferences.webcam_width == 480
    assert isinstance(idle, StateMessage)
    assert idle.channel is None and not idle.users
    assert idle.preferences.scale == 1 and idle.preferences.webcam_width == 280
    assert speaking == SpeakingMessage("2", False)
    assert join == ToastMessage("join", "Zoë 🎮 joined\nvoice", None)
    assert leave == ToastMessage("leave", "Zoë 🎮 joined\nvoice", None)
    assert frame == FrameMessage("2", 960, 1920, bytes((1, 2, 3)))


def test_typescript_normalisation_and_limits(compiled_protocol: Path) -> None:
    (raw,) = run_protocol(compiled_protocol, "boundaries")
    limits: object = json.loads(raw)
    assert limits == [MAX_MESSAGE_BYTES, MAX_FRAME_BYTES, MAX_FRAME_WIDTH, MAX_FRAME_HEIGHT]


def test_native_ipc_rejects_invalid_payloads(compiled_protocol: Path) -> None:
    state, frame = (parse_message(raw) for raw in run_protocol(compiled_protocol, "native"))
    assert isinstance(state, StateMessage)
    assert state.users[0].id == "2"
    assert frame == FrameMessage("2", 960, 1920, bytes((1, 2, 3)))


def test_native_connection_lifecycle(compiled_protocol: Path) -> None:
    run_protocol(compiled_protocol, "lifecycle")


def test_native_reconnects_after_overlay_restart(compiled_protocol: Path) -> None:
    run_protocol(compiled_protocol, "reconnect")


def test_renderer_synchronises_new_connections(compiled_protocol: Path) -> None:
    run_protocol(compiled_protocol, "renderer")


def test_voice_membership_and_speaking_lifecycle(compiled_protocol: Path) -> None:
    run_protocol(compiled_protocol, "voice")


def test_renderer_voice_channel_transitions(compiled_protocol: Path) -> None:
    run_protocol(compiled_protocol, "voice-renderer")


def test_webcam_discovery_and_resource_management(compiled_protocol: Path) -> None:
    run_protocol(compiled_protocol, "webcam")


def test_renderer_webcam_preferences_and_lifecycle(compiled_protocol: Path) -> None:
    run_protocol(compiled_protocol, "webcam-renderer")


def test_renderer_settings_defaults_and_live_changes(compiled_protocol: Path) -> None:
    run_protocol(compiled_protocol, "settings-renderer")
