# VencOverlay project roadmap

This document describes planned development following the `0.1.0.dev1`
development preview. It is a living roadmap, not a statement of current
compatibility or a commitment to release dates. For current installation
requirements, see the [README](../README.md).

## Current baseline

The first development preview supports the stable Discord Flatpak with a
compatible existing Vencord installation on Fedora 43 x86-64, tested under
GNOME Wayland. Installation, upgrades, restoration, recovery and voice-overlay
operation have been exercised. Native X11 and GTK Layer Shell backends exist,
but their wider live compatibility has not yet been established. Vesktop
integration and Windows or macOS graphical backends are not implemented. Live webcam
capture remains unverified.

The goal is to extend this foundation without regressing existing functionality,
changing the user experience unnecessarily or maintaining separate permanent
source branches for each operating system.

## Planned client integration

Discord requires a compatible Vencord installation. Vesktop already includes
Vencord and requires a separate integration strategy. Package availability does
not establish VencOverlay compatibility; each target must be validated.

### Discord on Linux

| Installation channel | Target environment | Status |
|---|---|---|
| [Flatpak / Flathub](https://flathub.org/apps/com.discordapp.Discord) | Fedora 43 GNOME Wayland | Tested baseline |
| Flatpak / Flathub | Other Linux distributions and desktop sessions | Planned |
| [RPM Fusion Nonfree](https://github.com/rpmfusion/discord) | Fedora and compatible derivatives | Planned |
| [Official RPM download](https://discord.com/download) | Fedora and compatible RPM-based systems | Planned |
| Official DEB download | Debian, Ubuntu and compatible derivatives | Planned |
| Official Arch package (`.pkg.tar.zst`) | Arch Linux and compatible derivatives | Planned |
| Official TAR.GZ download | Compatible Linux systems | Planned |
| [Arch Extra repository](https://archlinux.org/packages/extra/x86_64/discord/) | Arch Linux (`pacman`) | Planned |
| [CachyOS repositories](https://packages.cachyos.org/package/extra/x86_64/discord) | CachyOS (`pacman`) | Planned |
| Manjaro Extra repository | Manjaro (`pacman` / Pamac) | Planned; verify current package and runtime layout |
| [Snap Store](https://snapcraft.io/discord) | Ubuntu and other Snap-enabled systems | Planned; confinement feasibility to assess |

Discord's native Linux packages now use an updater/bootstrapper with the
application runtime in a writable user location, as described in the
[November 2026 Linux update announcement](https://discord.com/blog/discord-linux-a-year-of-upgrades).
Installation discovery must identify the actual active runtime rather than
assuming the package-owned executable is the application itself. Updates must
be tested for compatibility with the managed Vencord integration.

### Vesktop on Linux

| Installation channel | Target environment | Status |
|---|---|---|
| [Flatpak / Flathub](https://flathub.org/apps/dev.vencord.Vesktop) | Supported Linux platforms | Planned |
| [Official RPM download](https://vesktop.dev/install/linux/) | Fedora and compatible derivatives | Planned |
| Official DEB download | Debian, Ubuntu and compatible derivatives | Planned |
| [AUR `vesktop-bin`](https://aur.archlinux.org/packages/vesktop-bin) | Arch, CachyOS, Manjaro and compatible derivatives | Planned |
| Official AppImage | Compatible Linux platforms | Investigation required |
| Official portable tarball | Compatible Linux platforms | Deferred |

The AppImage needs a feasibility assessment covering discovery, runtime
integration and updates. Development builds from `vesktop-git` are not an
initial acceptance target. On Manjaro, verify AUR package compatibility with
the selected Manjaro repository branch before accepting this installation path.

### Windows 11

| Client | Installation channel | Status |
|---|---|---|
| Discord | Microsoft Store | Planned; verify actual installation layout |
| Discord | [Official DiscordSetup.exe](https://discord.com/download) | Planned |
| Vesktop | [Official Vesktop installer](https://vesktop.dev/install/windows/) (`Vesktop-Setup-*.exe`) | Planned |

The initial Windows target is Windows 11 x86-64. A native transparent,
click-through Windows overlay backend, process/launcher integration and
Windows-native development and release tooling are required. Windows Store
installation must be investigated separately, but may share an integration
path with the executable installer if their actual layouts are equivalent.

### macOS (lower priority)

| Client | Installation channel | Status |
|---|---|---|
| Discord | [Official macOS download](https://discord.com/download), with [Vencord installed using its macOS installer](https://vencord.dev/download/) | Planned; lower priority |
| Vesktop | [Official macOS DMG](https://vesktop.dev/install/mac/), with bundled Vencord | Planned; lower priority |

macOS follows the Linux and Windows 11 implementation milestones. Assess
supported macOS versions and Apple Silicon (ARM64) and Intel (x86-64) separately
before selecting build and test targets. Investigate native transparent,
click-through and non-focusable overlay windows, fullscreen Spaces, monitor
geometry and scaling, application bundle and runtime discovery, launch and
shutdown integration, updates, restoration, Gatekeeper, code signing and
notarisation requirements. Do not assume that Linux or Windows graphical
backends or packaged binaries can be reused unchanged. Provide native setup,
validation, build, distribution, installation and removal tooling as required.
No macOS compatibility is currently claimed.

### Windows portable Vesktop editions (deferred)

| Client | Installation channel | Status |
|---|---|---|
| Vesktop | Windows x64 Portable | Deferred; not currently planned for implementation |
| Vesktop | Windows ARM64 Portable | Deferred; not currently planned for implementation |

These editions remain outside the planned implementation scope and are listed
below macOS for completeness, not as committed future milestones.

## Operating system and desktop coverage

The following is the proposed initial platform test matrix. Exact OS versions,
architecture and desktop sessions must be recorded with test results. Versions
should be chosen according to upstream support and practical testing capacity.

| Platform family | Initial test targets | Package management | Status |
|---|---|---|---|
| Fedora | Fedora 43 baseline, newer supported Fedora releases | DNF / RPM | Extend existing coverage |
| Debian | Current stable Debian | APT / DPKG | Planned |
| Ubuntu | Supported Ubuntu LTS releases | APT / DPKG, Snap | Planned |
| Arch | Arch Linux | Pacman, AUR | Planned |
| CachyOS | CachyOS | Pacman, AUR | Planned |
| Manjaro | Manjaro Stable, x86-64 | Pacman, Pamac, AUR | Planned |
| Windows | Windows 11 x86-64 | Windows installers, Microsoft Store | Planned |
| macOS | Supported versions and architectures to be established | DMG / application bundles | Planned; lower priority |

Manjaro is a primary Linux target alongside Arch and CachyOS, not merely a
derivative assumed to inherit their compatibility. It maintains its own
repositories and staged package updates; initially test the Stable branch
and record its dependency versions independently. See the
[Manjaro repository documentation](https://wiki.manjaro.org/index.php?title=Switching_Branches)
and [AUR guidance](https://wiki.manjaro.org/index.php?title=Arch_User_Repository).
Do not mix official Arch repositories into a Manjaro installation.

Linux desktop coverage should include GNOME Wayland, GNOME X11 where available,
KDE Plasma Wayland and X11 where available, and at least one representative
wlroots-based compositor (for example Sway or Hyprland). Desktop/session
coverage is separate from distribution coverage. A compositor that cannot
provide safe overlay behaviour must be reported as unsupported, not silently
accepted.

Additional candidates for later investigation include Linux Mint and Pop!_OS
(DEB-based), Nobara (Fedora-based), EndeavourOS (Arch-based),
openSUSE (RPM/Zypper), NixOS (Nix packages and declarative integration), and
immutable/gaming systems such as Fedora Atomic desktops, Bazzite and SteamOS.
For NixOS, see the [Discord packaging documentation](https://wiki.nixos.org/wiki/Discord).
These are not promises of support. Immutable systems and NixOS may require
distinct integration approaches; SteamOS Game Mode must be evaluated
separately from desktop mode. Package availability on these additional
systems should be checked before adding them to the supported matrix.

## Development and distribution tooling

Maintain one shared project with platform-specific components only where
necessary. Preserve the existing Linux commands and their safety checks while
extending them to the supported environments:

| Operation | Linux | Windows (planned) | macOS (lower priority) |
|---|---|---|---|
| Development prerequisites and environment | `setup.sh` | Native equivalent, e.g. `setup.ps1` | Native equivalent |
| Static analysis and tests | `validate.sh` | Native equivalent | Native equivalent |
| Native and frontend builds | `build.sh` | Native equivalent | Native equivalent |
| Distribution assembly and verification | `release.sh` | Native equivalent | Native equivalent |
| Client discovery, preflight and installation | `install.sh` | Native equivalent | Native equivalent |
| Removal and restoration | `uninstall.sh` | Native equivalent | Native equivalent |

Script names and packaging formats for Windows and macOS are provisional. Linux setup
must resolve appropriate dependencies for each distribution family and detect
unsupported versions clearly. Shared Python and TypeScript validation should
remain consistent across platforms. Retain strict type checking, linting,
unit tests, graphical tests and production frontend minification. Maintain
pinned frontend source and dependencies, and keep readable maintained sources.

Build standalone native executables on appropriate target build environments.
Determine the actual native library and ABI compatibility before sharing an
artifact across distributions; a Fedora-built executable is not assumed to
work on Debian or Arch. Prefer shared release artifacts where justified by
compatibility testing, not separate builds for every client distribution
method. Record target OS, architecture, native dependencies, source revision
and runtime compatibility. Preserve matching source archives, notices and
checksums for each published build, including any modified dependencies.

The authoritative release branch remains `main`. Use focused feature/fix
branches, validate and review changes, merge into `main`, and build releases
from the exact clean commit that will be tagged. Existing releases remain
unchanged.

## Stream thumbnails (feasibility investigation)

Investigate replacing participant webcam thumbnails with thumbnails of Discord
Go Live streams and screen shares. This is a proposed change to overlay
functionality, not an implemented feature or a guarantee of technical
feasibility. Voice participants, speaking indicators, avatars and existing
streaming-status indicators must remain available independently of thumbnails.

The current implementation tracks streaming state (`selfStream`) but captures
only explicitly identified participant camera video elements. It deliberately
excludes screen shares. Existing frame resizing, JPEG encoding, localhost
transport, image caching and rendering provide a potential foundation for
stream thumbnails, subject to investigation and testing.

Investigate the following approaches in order:

1. Reuse Discord's existing authorised stream preview images, including the
   mechanism used to display previews before watching a stream. Investigate
   availability, refresh cadence, caching, permissions and stream identity.
   The [BiggerStreamPreview Vencord plugin](https://vencord.dev/plugins/BiggerStreamPreview)
   is a useful reference for the existing preview interface, not proof that
   live or background thumbnail capture is available.
2. Capture reduced-resolution frames from streams the user is already watching,
   where the client exposes an identifiable, playable video element. Establish
   whether this continues when the call view is hidden, minimised or unfocused.
3. Establish whether periodically refreshed thumbnails of unwatched streams
   are available without subscribing to their video feeds. If not, document
   the limitation rather than silently joining streams or increasing network
   and decoding load.

The feasibility investigation should distinguish periodic preview images from
live video-frame thumbnails and explicitly record which modes require the user
to watch a stream. Respect stream visibility, channel membership, preview
privacy settings and the user's existing access; do not bypass disabled
previews, automatically join streams or capture unrelated video content.

Before replacing webcam functionality, demonstrate a working proof of concept
on the existing Fedora 43 Discord Flatpak baseline. Evaluate:

- Correct association of each thumbnail with its stream and participant,
  including multiple simultaneous streams and users with both camera and stream
  enabled. Never infer identity from unidentified video elements.
- Thumbnail aspect ratio, refresh frequency, bandwidth, CPU/GPU and memory
  overhead, bounded image sizes and existing transport backpressure.
- Behaviour when streams start, stop, become unavailable or change, when the
  user changes channels, and when the client closes or reconnects. Remove stale
  thumbnails and show the ordinary participant/streaming indicator when no
  valid thumbnail is available.
- Compatibility with Discord and Vesktop, including their supported operating
  systems, packaging methods and desktop sessions as those targets are added.
- Any required changes to the plugin settings, protocol, native renderer,
  settings migration, tests and user documentation. Preserve unrelated settings
  and define compatibility between frontend and native versions.

Keep the current webcam implementation unchanged until feasibility has been
established and replacement behaviour has been reviewed and accepted. If the
investigation is successful, replace webcam thumbnail functionality with
stream thumbnail functionality rather than committing to maintaining two
parallel capture features. If the investigation fails, retain the existing
implementation and document the blocker. No release version or delivery date
is assigned to this investigation.

## Proposed implementation order

1. **Cross-platform Linux foundation.** Extend platform detection, prerequisite
   setup, validation, native builds and distribution tooling across Fedora,
   Debian/Ubuntu and Arch/CachyOS/Manjaro. Preserve the Fedora Flatpak
   baseline.
2. **Native Discord integration.** Support the official Linux bootstrapper
   formats and repository packages using verified runtime discovery, Vencord
   adoption, managed launchers and safe repair/restoration.
3. **Vesktop integration on Linux.** Implement the Flatpak and native package
   paths with its bundled Vencord. Investigate AppImage feasibility.
4. **Sandboxed and additional Linux environments.** Investigate Snap confinement
   and expand desktop/session and derivative coverage. Implement only verified
   and maintainable integration paths.
5. **Windows 11 foundation and integration.** Implement the overlay backend,
   native tooling and release packaging, then support Discord and Vesktop
   installation channels individually.
6. **macOS foundation and integration (lower priority).** Assess supported OS
   versions and architectures, implement a suitable native overlay backend and
   development/release tooling, and validate Discord with Vencord and Vesktop
   from their official macOS installation channels.
7. **Release acceptance and documentation (throughout).** Publish only those target
   combinations that pass the applicable acceptance criteria. Maintain a
   compatibility matrix, installation guides and reproducible release evidence.

The stream thumbnail feasibility investigation is an independent functional
workstream. It may be undertaken on the existing baseline without waiting for
Windows or macOS support and must not block the platform compatibility
milestones.

These milestones describe a proposed order, not fixed version assignments.
Compatibility can be delivered incrementally in development previews before a
stable release is considered.

## Acceptance criteria for each supported target

- Development environment setup and all applicable validation checks pass.
- Native and minified frontend builds succeed; production packages pass
  integrity, provenance and corresponding-source verification.
- Prebuilt packages install on a clean target without requiring development
  tools. Preflight identifies the selected client and its active runtime,
  validates compatibility and refuses ambiguous or unsafe layouts.
- Fresh installation, upgrade, repair after client/Vencord updates, and
  uninstallation restore or preserve the original client runtime, launcher
  and user settings as appropriate. Unsupported updates fail safely.
- Launching the selected client starts the overlay automatically; there is no
  separate login-autostart service or manual overlay start requirement.
- Voice state, participants, speaking indicators, avatars, call transitions
  and optional webcam behaviour (or stream thumbnail behaviour if the proposed
  replacement has been implemented) are verified. The overlay remains
  click-through and non-focusable, supports the intended fullscreen behaviour,
  and respects monitor geometry, scaling and desktop panels.
- Closing the client terminates the overlay and releases its local listener.
  Relaunching works and does not create duplicate instances. Multiple installed
  clients/scopes are detected without overwriting the wrong installation;
  simultaneous-client behaviour is explicitly defined and tested before support
  is claimed. The default listener remains localhost-only.
- Existing supported configurations pass regression testing.

Use automated unit/integration tests and platform build jobs for reproducible
checks. Use clean graphical VMs or dedicated machines for live client,
compositor, upgrade, rollback and game/fullscreen acceptance. CI success alone
is not evidence of live desktop compatibility. Track results per OS version,
architecture, desktop/session, client version, Vencord revision and
installation channel, distinguishing planned, implemented but unverified,
and tested configurations.

## Deferred scope

Windows portable Vesktop releases (x64 and ARM64) are outside the initial
scope. Linux portable Vesktop tarballs, additional CPU architectures outside
the selected platform targets, mobile platforms and development/test client
channels are also deferred unless separately prioritised. Apple Silicon and
Intel Mac feasibility will be assessed as part of the lower-priority macOS
milestone; this does not change the exclusion of Windows ARM64 Portable. No claim is made that every package manager, desktop
or Linux derivative will be supported automatically.
