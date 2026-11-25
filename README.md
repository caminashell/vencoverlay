# VencOverlay

A transparent Discord voice and webcam overlay for games, using Vencord to
receive call information and a native GTK3/Cairo application to draw the overlay.

VencOverlay builds on [Vesktop Overlay](https://github.com/Akii-G/vesktop-overlay),
originally created by [Akii-G](https://github.com/Akii-G). Full credit goes to
Akii-G for the original overlay and its foundation, which made this project
possible.

## Development status

VencOverlay is under development (`0.1.0.dev0`). The refactored Linux application
and standalone executable have passed offline graphical testing on Fedora
Workstation with GNOME Wayland, including fullscreen EVE Online. Live integration
with the refactored Vencord plugin and official Discord Flatpak installation are
still pending. Other desktop environments and operating systems have not yet
been validated with the refactored runtime.

The original Python overlay and inherited installation scripts have been removed
from the working tree; their source remains available in Git history. The
original bridge plugin and bundled Vencord distribution are retained for the
upcoming plugin integration milestone. End-user installation and uninstallation
are not yet supported by the refactored project.

## Application features

- Discord and Mac-style voice overlays with participant avatars and names.
- Speaking animations, mute/deafen, camera and streaming indicators.
- Webcam tiles and join/leave notifications.
- Configurable placement, scale, opacity and visual preferences through the bridge.
- Transparent, click-through, non-focusable windows; hidden when inactive.
- Offline animated demonstration without Discord or a listening socket.
- Linux X11/XWayland and GTK Layer Shell backend selection. GNOME Wayland uses
  XWayland; Layer Shell requires a compatible compositor and native library.

Live call features require a compatible, enabled Vencord bridge. The application
listens on IPv4 loopback (default port 6889) for bridge messages and may download
participant avatars over HTTP(S) during normal operation. Demo mode uses generated
images and an isolated temporary avatar cache.

## Linux development

Fedora Workstation is the primary validation environment. Run these commands
from the repository root, with the project's virtual environment deactivated:

```sh
./setup.sh
./validate.sh
./validate.sh --coverage
```

The setup script checks the required native libraries and prepares the locked
Python development/build environment. Validation does not install dependencies.
Frontend validation is not implemented yet. Use `./validate.sh --graphical --coverage` to include the native
window test on a suitable desktop. Stop other overlay instances before assessing
native window behaviour.

### Offline demonstration

```sh
uv run --locked --no-sync --offline vencoverlay --demo
```

The demo alternates between active and inactive periods and both rendering
styles. Press Ctrl+C to stop it. On Linux, send `SIGUSR1` to the application PID
reported at startup to toggle visibility.

### Standalone executable

```sh
./build.sh
./dist/vencoverlay --version
./dist/vencoverlay --demo
```

The build publishes a verified Nuitka onefile executable to `dist/vencoverlay`.
It is dynamically linked and still requires compatible native system libraries.
The build currently uses a version-specific, isolated Nuitka 4.3.5 workaround
for GTK3 typelib discovery. Review this workaround before upgrading Nuitka.
The remaining `gi.repository` compiler warning is not suppressed; the compiled
GNOME Wayland demo has been tested successfully despite it.

Do not start normal mode alongside another overlay listening on port 6889.
The refactored bridge and end-user installer are not yet ready for use.

## Local configuration

Configuration is optional. Missing settings use defaults. On Linux, the default
location is `~/.config/vencoverlay/config.toml` (or the corresponding
`XDG_CONFIG_HOME` directory).

```toml
port = 6889
monitor = 0
margin = 24
font = "Inter"
log_level = "INFO"
log_to_file = false
```

`monitor` is a zero-based monitor index. The application validates TOML values
and reports invalid configuration rather than silently replacing it. The default
avatar cache is `~/.cache/vencoverlay/avatars`; optional rotating logs are stored
in `~/.local/state/vencoverlay/vencoverlay.log`. XDG directory overrides are
respected. Demo mode disables persistent file logging.

The optional `VENCOVERLAY_BACKEND` environment variable accepts `auto`, `x11`
or `layershell`. The default is `auto`.

## Project layout

| Path | Purpose |
|---|---|
| `src/vencoverlay/` | Refactored application |
| `tests/` | Foundation, unit and opt-in graphical tests |
| `scripts/` | Shared development prerequisite checks |
| `setup.sh`, `validate.sh`, `build.sh` | Development and build tooling |
| `version.sh`, `stats.sh`, `snapshot.sh` | Version management, statistics and source snapshots |
| `plugin/overlayBridge/` | Retained original Vencord bridge |
| `vencord-dist/` | Retained original bundled Vencord build |

## License and attribution

VencOverlay is a fork and refactoring of the original
[Vesktop Overlay](https://github.com/Akii-G/vesktop-overlay) project by Akii-G.
The original design, rendering implementation and OverlayBridge plugin form the
foundation of this project. The upstream OverlayBridge source credits
`akiloyutu` in its author metadata.

VencOverlay retains the original GNU General Public License v3. See
[LICENSE](LICENSE). The original plugin source carries the
`GPL-3.0-or-later` SPDX identifier. Existing upstream notices and attribution
are preserved.
The retained `vencord-dist/` contains a build of
[Vencord](https://github.com/Vendicated/Vencord) with the original OverlayBridge
plugin added. Its existing license and legal notices are preserved.
VencOverlay is not affiliated with Discord or Vencord.
