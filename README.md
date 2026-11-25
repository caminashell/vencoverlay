# VencOverlay

A transparent Discord voice and webcam overlay for games, using Vencord to
receive call information and a native GTK3/Cairo application to draw the overlay.

VencOverlay builds on [Vesktop Overlay](https://github.com/Akii-G/vesktop-overlay),
originally created by [Akii-G](https://github.com/Akii-G). Full credit goes to
Akii-G for the original overlay and its foundation, which made this project
possible.

## Development status

VencOverlay is under development (`0.1.0.dev0`). The standalone Linux application
and the refactored Vencord plugin have passed live voice-overlay testing on
Fedora Workstation with GNOME Wayland (XWayland), using the official Discord
Flatpak with an existing conventional Vencord loader. Installation, upgrades,
removal and restoration of that loader's original runtime have been exercised.
Voice membership, speaking, mute/deafen, avatars, notifications, live settings,
fullscreen interaction and connection recovery have been verified.

Live webcam testing, other installation layouts, desktop environments and
operating systems remain unverified. Screen/window-share video capture is not
implemented. Automatic repatching after Flatpak updates is not implemented.
The original implementation and bundled distribution remain available in Git
history; the maintained plugin is built against pinned Vencord source.

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
Use `./validate.sh --graphical --coverage` to include the native window test on a
suitable desktop. Stop other overlay instances before assessing native window
behaviour. Use `./setup.sh --frontend` to prepare the pinned Vencord toolchain
and `./validate.sh --frontend --coverage` to include frontend validation.
See [frontend development](docs/frontend-development.md) for details.

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

### Discord Flatpak integration

Build and verify the frontend separately from the standalone executable:

```sh
./setup.sh --frontend
./validate.sh --frontend --coverage
./build.sh --frontend
./build.sh --verify-frontend
```

The installer consumes `dist/vencoverlay` and `dist/vencoverlay-vencord.zip`.
It does not compile dependencies or require the development toolchain. Review
[Flatpak installation](docs/flatpak-installation.md) before installing. For an
existing conventional Vencord loader, assess adoption with:

```sh
./install.sh --check --adopt-existing
```

If preflight confirms a supported layout, fully quit Discord and any running
overlay, then use `./install.sh --adopt-existing`. Start Discord from its desktop
launcher and enable VencOverlay in Vencord's plugin settings. Use the same
installation mode for upgrades. With Discord and the overlay stopped,
`./uninstall.sh` removes managed integration and restores the original adopted
runtime while retaining settings. Do not use `sudo` for these commands.
Other installation layouts have separate requirements documented in the guide.

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
| `scripts/` | Development, frontend build and installation helpers |
| `setup.sh`, `validate.sh`, `build.sh` | Development and build tooling |
| `install.sh`, `uninstall.sh` | Managed Discord Flatpak integration |
| `version.sh`, `stats.sh`, `snapshot.sh` | Version management, statistics and source snapshots |
| `plugin/vencoverlay/` | Maintained VencOverlay plugin source for Vencord |
| `docs/` | Frontend development and Flatpak installation guides |
| `build/`, `dist/` | Generated, Git-ignored build workspace and artifacts |

## Vencord plugin development

The plugin is named `VencOverlay`. Its renderer and native helper remain in
separate TypeScript entry points. Vencord’s settings migration API moves saved
preferences (including the enabled state) from the original `OverlayBridge`
name when no `VencOverlay` settings entry already exists. Existing VencOverlay
settings take precedence. The migration has not yet been tested in a live client.
The developer diagnostic function is now `VencOverlayDebug()` in Discord DevTools.

The frontend build uses pinned Vencord v1.15.19 and produces minified Discord
Desktop and Vesktop bundles. Only the Discord Flatpak adoption path has been
validated in a live client. The verified release archive includes the pinned
upstream and custom plugin source, debug maps, licence notices and checksums.
The custom distribution disables Vencord's automatic updater to protect the
custom plugin; upgrades must use the VencOverlay build/install workflow.

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
The generated frontend distribution includes
[Vencord](https://github.com/Vendicated/Vencord), its licence and linked legal
notices, project attribution and corresponding source. The inherited bundle and
its original notices remain in Git history.
VencOverlay is not affiliated with Discord or Vencord.
