# VencOverlay frontend development

The maintained custom Vencord plugin is `plugin/vencoverlay/`. The Python
application and the plugin can be developed independently. Production bundles
are generated from the pinned upstream source; the inherited bundle is no longer
part of the working tree.

## Pinned upstream environment

- Vencord v1.15.19: `d086992fb674299a2fa05ac6432edc3a777389f3`
- Node.js: 22 or newer, as required by the pinned upstream package
- pnpm: 12.15.0, as specified by upstream

These pins are maintained in `scripts/frontend.sh`. Upstream's own lockfile,
TypeScript configuration and Oxlint configuration are used without replacement
or relaxed compiler settings. This is a compatibility baseline, not a claim that
this revision is the latest Vencord release or works with every Discord version.

## Setup and validation

Use an ordinary user account with Node.js and npm available on PATH. If using
nvm, activate an appropriate installed Node version in the shell first.

```sh
./setup.sh --frontend
./validate.sh --frontend --coverage
```

Frontend setup uses network access to fetch the pinned Git commit from the
official Vencord repository and install the pinned package manager locally. It
then installs upstream dependencies with `--frozen-lockfile`. The checkout and
all frontend dependencies live under the ignored `build/vencord-dev/` area.
Setup migrates a clean managed checkout at the previous v1.15.15 pin to the
new pin; unexpected revisions, tracked changes and unrelated userplugins are
refused. Dependencies are synchronised again with the new frozen lockfile.
No global pnpm installation, Discord injection or Flatpak changes are performed.
Dependency installation can execute upstream package installation scripts.

Validation copies the current plugin into the managed checkout at
`src/userplugins/vencoverlay/`. This copy is generated; edit the maintained
plugin source instead. Validation rejects an unexpected upstream revision,
tracked modifications, stale dependency fingerprint or other userplugin
folders. It runs the installed TypeScript compiler over the upstream project and
Oxlint over the VencOverlay plugin, with warnings treated as failures. It does
not call pnpm or install dependencies. Existing Python checks remain intact.

With `--frontend`, pytest additionally compiles the standalone TypeScript
protocol module using the pinned compiler and checks its actual emitted JSON
against the Python parser. The tests cover message schemas, settings defaults,
UTF-8 and webcam size limits, native IPC rejection, and connection recovery
using ephemeral loopback sockets. Deterministic socket and renderer-stub tests
also cover cancellation, stale connection identifiers, bounded writes and
snapshot synchronisation. These tests neither require Discord nor open port 6889.
They are skipped during ordinary Python-only validation. The separate Vencord
TypeScript and Oxlint gates still check the complete custom plugin.
Do not run frontend setup and validation concurrently against the same checkout.

To inspect frontend readiness without downloading anything:

```sh
bash scripts/frontend.sh --check
bash scripts/frontend.sh --report
```

To run individual frontend checks:

```sh
bash scripts/frontend.sh --typecheck
bash scripts/frontend.sh --lint
```

Ordinary `./setup.sh` and `./validate.sh` remain Python-only. Combine
`--frontend` with `--graphical` and `--coverage` when full validation is needed.

Plugin build and installation are separate operations. Production frontend
artifacts are minified by the upstream production build, verified before
installation, and distributed with the required licence notices. Development
source remains readable and is not minified.

## Connection lifecycle

The native helper owns one IPv4 loopback socket, including during connection
attempts. The plugin explicitly starts and stops the helper. Failed connections
are retried every two seconds; pending connections time out after five seconds.
Changing the port or disabling the plugin cancels the previous socket and retry.
Each successful connection has a new local IPC epoch (not a wire protocol field).
The renderer polls connection status every 250 ms and sends a fresh state snapshot
before sending incremental updates. Full-state heartbeats remain at two seconds.

The native helper limits pending socket writes to 4 MiB for webcam frames and
8 MiB plus one newline byte for control messages, including the proposed write.
Frames are disposable under backpressure. If a valid control write would exceed
the bound, the connection is reset and a fresh snapshot is required. A successful
socket write means accepted for transmission, not acknowledged by the overlay.
These are user-space socket buffer limits, not a bound on total process memory.

## Voice-state lifecycle

`voice.ts` owns the membership snapshot and speaking flags. The renderer reads
Discord's selected voice channel and voice-state store, using the same snapshot
for participants, notifications and webcam eligibility. A selected channel is
not treated as active until the local user's membership is present. Missing
membership data yields an idle snapshot; missing optional metadata uses fallback
names and avatars. The first complete membership snapshot, channel transitions,
store recovery and plugin restart establish baselines without join/leave toasts.
Incomplete or duplicate membership records suppress membership notifications.

Channel-session and membership/video revisions prevent stale snapshot responses
from authorising incremental updates or camera frames for a different call.
Speaking flags are pruned on departure and cleared on a channel transition.
Events naming a different channel are ignored; events without a channel ID can
only be checked against the current membership (Discord does not supply an IPC
session token on those events). Notifications are not queued for offline replay.
The existing 30 ms coalescing, 250 ms connection poll and two-second heartbeat
remain unchanged. The frontend regression suite covers these transitions and
simulates a frame encoding across a channel change. Live guild voice-channel
membership, speaking and channel transitions have passed testing in official
Discord Flatpak. Direct/group call behaviour and camera identification remain
unverified in live testing.

## Webcam capture lifecycle

`webcam.ts` owns camera discovery and encoding. It uses a single in-flight
capture cycle, including across disable/re-enable and FPS changes. Cycles that
arrive while an encoder is busy are skipped rather than queued. A single
OffscreenCanvas is reused, resized when necessary and released when capture
becomes inactive. The configured FPS is an upper bound per camera; slow
encoding or backpressure may reduce the actual rate. There is no additional
Discord video subscription or webcam persistence.

Only playing videos with an explicit `data-selenium-video-tile` participant ID
matching an eligible camera user are captured. Stream-key tiles containing a
colon, unmatched videos, and detached or paused elements are excluded. The
previous generic React-property and last-unmatched-video heuristics have been
removed deliberately: ambiguous identity must produce a placeholder instead
of an incorrectly attributed image. Camera discovery is cached for up to 500 ms
within a stable call and settings revision; cached elements, tile identity and
media source are rechecked before drawing and after asynchronous encoding.
The largest ready view of a participant's camera is selected if duplicated.

The existing 5–30 FPS settings, JPEG quality 0.72, capture dimensions, 2 MiB
compressed frame limit and native 4 MiB write-buffer limit are unchanged.
Capacity is checked before discovery and before each subsequent camera is
encoded; the native helper still enforces the projected size of each write.
A rotating starting point helps avoid repeatedly favouring the first camera
under congestion. Changes to voice membership, connection, relevant settings
or plugin lifecycle invalidate old work. Camera settings must be represented
in a successfully sent state snapshot before frames resume.

`VencOverlayDebug()` reports matching metadata, effective settings and local
connection status, not encoded image contents.
The existing call-view requirement remains. The DOM marker convention comes
from the inherited plugin and must be verified with actual Discord camera
and screen-share tiles during live testing; unmarked layouts will show a
placeholder rather than be guessed. Live camera capture and screen-share
exclusion testing were deferred. Screen/window-share capture is not implemented. Simulated capture tests check discovery,
resource reuse, concurrency, source replacement and oversized images; renderer
integration tests check settings, backpressure, FPS changes and stop/start.

## Plugin settings

`settings.ts` defines the existing 15 Vencord plugin settings and converts their
saved values into one typed effective configuration. The protocol module owns
the shared appearance defaults, numeric bounds, default port and supported
frame rates, so the native wire validator, UI and capture dimension calculation
use the same limits. Setting keys, choice values, defaults and slider markers
are unchanged. The existing `OverlayBridge` migration still runs through
Vencord's `migratePluginSettings` before defining the `VencOverlay` settings.
No new configuration file is introduced. The plugin settings include a source
and support link to the maintained repository, alongside original author credit.

Invalid saved values fall back to the existing defaults without rewriting the
saved settings. The port accepts an integer from 1 to 65535 (or a decimal
string representing one); invalid input uses 6889. The effective port must
match the standalone application's configured listening port. No restart is
required for appearance changes or the plugin's port change. The application
must already be listening on the chosen port for the new connection to succeed.

Appearance changes coalesce into a new state snapshot. Self visibility, webcam
visibility and tile width invalidate pending webcam captures and require a new
snapshot. FPS changes replace the capture interval and invalidate pending work;
FPS itself is local and is not transmitted in the wire protocol. Notification
changes apply to subsequent membership events. Settings edits while stopped
produce no connection or capture work; the next start reads the effective
saved values. The existing protocol, socket retry policy and capture limits
are unchanged.

The existing `VencOverlayDebug()` command includes effective settings and the
configured port, last observed connection epoch and snapshot readiness flag.
These are renderer-side observations, not a server acknowledgement or proof
that a configured port is listening. Diagnostics are available only while the
plugin is enabled and do not contain encoded frames. The regression suite
executes the real settings definitions with simulated Vencord APIs, checking
all keys, defaults and option values, migration invocation, invalid saved
values, live changes and stop/start. Live appearance, visibility, notification
and connection-port changes have passed testing, as has retention of existing
VencOverlay settings during upgrades. Migration from an original OverlayBridge
profile and live webcam-specific settings remain unverified.

## Production distribution

After frontend setup and validation, build the desktop distribution:

```sh
./validate.sh --frontend --coverage
./build.sh --frontend
./build.sh --verify-frontend
```

`./build.sh` without arguments still builds only the standalone Python
application. `--frontend` builds only the Vencord distribution; it does not
recompile the application. The two builds can be run independently. The
frontend build reruns upstream TypeScript and Oxlint validation and uses the
already installed dependencies. It does not invoke pnpm, install dependencies,
fetch source, inject Vencord, or modify an installed application. A missing or
stale toolchain must first be repaired with `./setup.sh --frontend`.

The production wrapper invokes the pinned upstream desktop builder with
`--standalone --disable-updater`. It checks the actual upstream build options
for bundling, minification, external source maps and linked legal notices
before compilation. JavaScript and emitted CSS bundles are minified by
Vencord's own esbuild configuration, not a second minifier. The standalone
build includes both official Discord Desktop and Vesktop targets; inclusion
is not a claim of tested runtime portability. The updater is disabled so an
upstream update cannot silently replace this custom distribution. Future
upgrades must go through the VencOverlay build/install workflow. The upstream
commit timestamp is supplied in milliseconds; dependencies and source revision
are pinned, but byte-identical archives across different hosts are not promised.

The generated archive is `dist/vencoverlay-vencord.zip`:

- `runtime/patcher.js`, `runtime/renderer.js`, `runtime/preload.js` and
  `runtime/renderer.css`: official Discord Desktop target.
- `runtime/vencordDesktopMain.js`, `runtime/vencordDesktopRenderer.js`,
  `runtime/vencordDesktopPreload.js` and `runtime/vencordDesktopRenderer.css`:
  Vesktop target.
- `runtime/`: also includes linked legal notices, licence and attribution
  files, and a CommonJS package marker.
- `debug/`: external source maps, separate from installable runtime files.
- `source/vencord-source.zip`: the pinned Git source tree, maintained custom
  plugin, build wrapper, licence and instructions for rebuilding the bundles.
- `manifest.json`: project and Vencord versions, exact upstream revision,
  Node/pnpm toolchain, build flags, plugin source hashes and SHA-256 and byte
  counts for every payload file.

Publication checks the complete runtime file set, build banners, linked
notices, plugin markers, and matching custom plugin source content in the
renderer and native source maps. It verifies the staged archive before
atomically replacing any previous release ZIP. Existing checkout output is
restored after the build; generated partial files are removed on normal
failure. Frontend setup, linting, typechecking and builds use an exclusive
lock to protect the managed checkout. Do not run manual upstream commands
against this checkout during managed operations.

`--verify-frontend` checks an existing archive without Node.js, pnpm or the
upstream checkout. It verifies internal consistency, not publisher identity;
SHA-256 is not a signature. The build prints the complete archive's SHA-256
for external verification. The installer must consume only verified runtime
files for the selected target, preserve relevant licence notices, and leave
source and debug maps outside the installed runtime. The Flatpak installer
implements these requirements. The corresponding source
and notices must remain available with any distributed binaries; a broader
release licence review, including third-party dependencies, is still needed.

The test suite exercises packaging with synthetic compiler outputs and real
Git archives, failure rollback, invalid build configuration and checksum
rejection. It does not substitute for a real pinned Vencord production build
or live Discord testing. The pinned production build and the conventional
loader adoption path have also been exercised on Fedora GNOME Wayland.

## Flatpak deployment development

Rootless installation, adoption, upgrades, removal and read-only preflight are
documented in [Flatpak integration](flatpak-installation.md). Live adoption of a
conventional Vencord loader is verified on the system-installed Discord Flatpak.
The clean per-user installation and externally managed system patch paths have
not been validated in a live client.

## Live validation baseline

The Fedora GNOME Wayland/XWayland voice-overlay regression passed participant
rendering, speaking/mute/deafen, membership notifications, live visual settings,
fullscreen interaction and reconnection. Discord avatar URLs are normalised to
static PNG at size 128 because the client returned WebP despite requesting PNG;
actual retrieval and cache creation were verified. Mac-style names retain normal
weight and animate between 50% and 100% opacity according to speaking activity.

Production executable measurements (30-second samples, both Nuitka processes):

| Condition | Average CPU | Peak CPU | Average PSS |
|---|---:|---:|---:|
| Idle | 1.53% | 2.93% | 67.41 MiB |
| Voice (8 participants) | 13.70% | 33.92% | 67.41 MiB |
| Fullscreen gameplay with voice | 6.95% | 17.62% | 67.23 MiB |

CPU percentages are relative to one logical CPU. These are short samples, not
controlled comparative benchmarks. They exclude Discord-side and compositor
costs, webcam processing and long-term memory behaviour. The participant count
for the gameplay sample was not recorded. Live webcam testing was deferred.
One automatic startup failure after an upgrade could not be reproduced; direct
execution, explicit desktop launch and subsequent normal GNOME launch succeeded.
No root cause has been established.
