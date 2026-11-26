# Official Discord Flatpak installation and recovery

The installer supports automatic adoption of the
conventional Vencord ASAR loader and an advanced clean per-user mode. Both
paths have automated filesystem and
simulated Flatpak tests. Adoption, upgrade, uninstall and original runtime
restoration have also passed live testing on Fedora GNOME Wayland with the
system-installed official Discord Flatpak. Clean per-user installation and
other layouts remain unverified in a live client. Review the read-only preflight
before activation.

The normal end-user route requires Discord with Vencord already installed and
working. Vesktop includes Vencord itself, but this installer does not support
Vesktop; do not use these commands against it. Completely quit the target
client, including its tray/background process, before installation, updates or
removal. The clean per-user and external patch paths described below are
existing advanced implementation paths, not the normal supported end-user
workflow.

For the normal end-user adoption workflow, start with the
[README installation steps](../README.md#installation).

## Read-only preflight

Run from the project root or extracted release directory, as the desktop user
(never with `sudo`):

```sh
./install.sh --check
```

If both per-user and system installations exist, select `--scope user` or
`--scope system`. The supported target is the stable x86-64 Flatpak
`com.discordapp.Discord`. Preflight reports the resolved deployment and loader
layout, whether the candidate ASAR matches the deployed OSTree commit, whether
Discord is running, the local desktop entry, and available artifacts. It does
not read Discord credentials or settings contents, patch Discord, set Flatpak
overrides, or install anything. Discord may remain open during preflight.
For recognised Vencord adoption, `Loader: verified existing Vencord` is reported
only after the loader, original ASAR, sandbox visibility and plugin checks pass.
The sandbox visibility probe can briefly appear as a Discord Flatpak instance.
Installation and removal allow up to five seconds for a listed instance to
exit before refusing; no listed instance is ignored and no process is killed.
Installation rechecks the running state before publishing changes.
The check reports one of `Not installed`, `Prepared`, `Installed`, or
`Incomplete`. These states describe verified integration ownership, not plugin
enablement or a live connection. Incomplete state causes a safe refusal with
the reason; an unchanged prepared state is inactive but can be completed.

## Artifact and installer requirements

Prebuilt artifacts do not need a development environment. If installing from a
source checkout, first prepare the build environment described in
[Development and builds](development.md), then run:

```sh
./setup.sh --frontend \
&& ./validate.sh --frontend \
&& ./build.sh \
&& ./build.sh --frontend \
&& ./build.sh --verify-frontend
```

Validation is recommended; both builds are necessary. Stop on failure. A source
checkout alone does not contain these artifacts. Prefer a complete trusted
installation archive from [GitHub Releases](https://github.com/caminashell/vencoverlay/releases)
when available. The same archive can be generated locally for testing; see
[release packaging](development.md#local-release-packaging).

The installer consumes `dist/vencoverlay`, `dist/vencoverlay-native.json` and
`dist/vencoverlay-vencord.zip`; it does not compile or download dependencies.
The archive is copied into staging and independently verified using the existing
production artifact verifier. The executable's reported version must match the
archive version. Native build metadata binds the executable hash to the native
source inputs and build environment. The installer checks the metadata and runs
`--check-runtime` from the current graphical session before activation. No overlay
is mapped and no listener starts. These checks assume trusted artifacts; checksums
and native probes do not constitute publisher authentication.

Installation uses the host's Python 3.11+ standard library, Bash, Flatpak and
`flock` (util-linux), not uv, Node, pnpm, Git, Python development packages or a
compiler. The existing native application runtime library requirements still
apply. `ostree` is additionally required to verify the original deployed ASAR before
automatic patching or existing-loader adoption.
If that cannot be established, the installer refuses to patch.

## Deployment layout

- `${XDG_DATA_HOME:-$HOME/.local/share}/vencoverlay/flatpak/` contains the
  executable, managed launcher, loader template, complete verified release
  archive (including source, licences and debug maps), and ownership manifest.
- `$HOME/.var/app/com.discordapp.Discord/data/vencoverlay/runtime/` contains
  only the Discord runtime bundles and their legal notices and metadata.
  The corresponding sandbox path is `/var/data/vencoverlay/runtime/`.
- The canonical desktop entry lives in
  `${XDG_DATA_HOME:-$HOME/.local/share}/applications/com.discordapp.Discord.desktop`.

Existing application configuration, avatar cache and Vencord profile are not
edited. Adoption replaces the existing compiled runtime as a unit and backs
up all its original files; unrelated custom plugins block adoption. No additional Flatpak filesystem grants are made.
The generated desktop launcher starts the standalone overlay outside the
sandbox, passes through Flatpak URI/file-forwarding markers and desktop action
arguments, and terminates its own overlay child when the foreground Flatpak
command exits. The shared runtime lock prevents overlapping managed launches
and installation. A Discord instance started independently of the managed
launcher is not taken over. A second launch during the initial startup window
may be refused rather than start a second overlay; an already running Discord
instance receives forwarded arguments without another overlay being started.

## Activation paths (do not use until preflight is reviewed)

Quit Discord completely, including the tray, and stop any older standalone
overlay before installation, upgrade or uninstall. Do not perform Flatpak
updates, repairs, or unrelated patch operations concurrently. Do not use `sudo`
to execute this installer. Confirmation requires typing `YES` unless `--yes`
is explicitly supplied.

**Advanced clean per-user deployment:** This existing, live-unverified route
requires explicit `./install.sh --patch-clean`. Normal installation refuses
unpatched Discord and requires working Vencord beforehand.
The loader is only installed when the deployment is writable, no competing
loader/unpacked archive layout is detected, and the ASAR matches the installed
OSTree commit. A backup hard link retains the original ASAR inode, and the new
loader is published by renaming a staged file. This follows the current Vencord
installer's `app.asar` / `_app.asar` loader arrangement and the pinned Vencord
patcher's original-ASAR resolution. If a local desktop override already exists,
`--replace-launcher` must be explicitly supplied to permit its replacement. Its
original contents and file mode are backed up for restoration. The generated
entry is based on the Flatpak-exported desktop entry rather than an unknown
existing wrapper.

**Existing conventional Vencord loader (user or system deployment):** Run a
read-only adoption assessment first:

```sh
./install.sh --check
```

Adoption supports only a two-file ASAR with an index consisting of a literal
`require` of the current user's `$HOME/.config/Vencord/dist/patcher.js` and the
conventional Discord package metadata. It verifies `_app.asar` against OSTree,
checks runtime ownership and sandbox visibility, and compares source-map plugin
directories with the verified release. Unknown loader code, unsupported paths,
missing source maps, detected custom plugins, or an existing unowned backup
block adoption. Source maps provide evidence of compiled directories, not
proof of the enabled plugin list or of equivalent plugin functionality.

After reviewing the assessment, quit Discord completely and stop any older
standalone overlay. Install with `./install.sh` (and select
`--scope system` if needed). If upstream plugin directories disappear from the
new distribution, also supply `--allow-plugin-removal` to explicitly accept
that compatibility change; this does not bypass custom-plugin protection.
Before adoption the renderer, patcher and preload build revisions must agree.
An exact matching revision is accepted; otherwise only explicitly known upstream
revision/version pairs can establish safe version ordering. Downgrades, unknown
foreign revisions and different revisions sharing a version are refused. The
plugin-removal flag does not bypass these checks. Managed upgrades compare the
verified installed release manifest with the candidate manifest as well.
Normal `YES` confirmation still applies. The same command detects adoption for
upgrades; `--adopt-existing` remains an optional explicit advanced override. A local launcher override still requires `--replace-launcher` on initial
installation.

The installer leaves the protected `app.asar`, `_app.asar` and Flatpak overrides
untouched. It moves the entire original runtime directory to
`$HOME/.config/Vencord/.vencoverlay-original-dist` using a same-filesystem rename,
then publishes the verified Discord runtime at the loader's existing `dist`
path. This preserves the original inode, file modes, ACLs, and other filesystem
metadata on the backup, rather than copying over individual files. The backup
is kept across upgrades, and its file inventory and the active runtime are
checked against the manifest. The separate managed sandbox runtime directory
is not created in adoption mode. The full production archive (including maps)
remains in the host installation directory. `./uninstall.sh` automatically
recognises adoption and restores the original directory and desktop entry.
It does not unpatch Discord or remove its existing Vencord settings.

**Other system deployments or unknown/foreign loader:** The installer will not
modify a protected deployment or overwrite another loader. `./install.sh --prepare-only` is a separate, reversible staging
operation that installs the verified user-owned files without touching the
Discord loader or desktop entry. It also creates a small ASAR loader template at
`<host-install>/app.asar`. The template loads the sandbox patcher at
`/var/data/vencoverlay/runtime/patcher.js`, and falls back to the original ASAR
if that runtime is absent. This fallback is not a mechanism for preserving
another user's custom Vencord installation. A system-wide patch may affect
other users; do not apply it without reviewing the existing patching mechanism.

The external patcher must preserve the prior loader and original application
and install the exact staged loader alongside `_app.asar`. No administrator
patch operation has been automated in this initial stage. After the external
patch is separately verified, rerunning the installer (without `--prepare-only`)
recognises the exact managed loader and activates the desktop entry. An unknown
loader outside the explicitly supported adoption layout remains a blocker.
Preservation of other compiled custom plugins cannot be assumed by replacing a
Vencord bundle; do not replace their distribution in place.

Enable VencOverlay manually in Discord's Vencord plugin settings after a verified
activation. The existing settings migration is handled by Vencord, not the
installer. Live loopback communication, voice state synchronisation and
retention of existing VencOverlay settings have been verified. Migration from
an original OverlayBridge profile and live camera capture remain unverified.

## Prepared but not active

`--prepare-only` stages files for a separately managed patch; it is not the
normal adoption workflow. A prepared installation can report a managed version
and installed artifacts while the desktop launcher is absent. Its manifest has
`desktop_sha256: null`, `adopted_runtime: null` and `owned_loader: false`.
The manifest is under `${XDG_DATA_HOME:-$HOME/.local/share}/vencoverlay/flatpak/install-manifest.json`.
These fields describe ownership, not the success of a live connection.

For a confirmed preparation-only state with an unchanged conventional Vencord
loader, the installer can complete adoption directly. Fully quit Discord and
any standalone overlay, then run:

```sh
./install.sh --check \
&& ./install.sh \
&& ./install.sh --check
```

The transition replaces the staged host files, removes the unused managed
sandbox runtime, preserves the original Vencord directory as a backup and
publishes the managed launcher in the same rollback transaction. No
intermediate uninstall is necessary. If both installation scopes exist, supply
the appropriate `--scope` consistently. If the deployment, loader, managed files
or original backup has changed, inspect the refusal before taking corrective
steps. Do not delete backups manually. The generic loader description may still
be `foreign-or-unsupported` until the adoption-specific checks establish the
recognised layout; `Original _app.asar matches OSTree: True` confirms the
original application archive was verified.

## Repair after Discord or Vencord reinstallation

Uninstalling Discord does not remove VencOverlay's user-owned installation
record. Reinstalling Discord or applying Vencord independently can invalidate
that record. Normal installation, preflight and removal refuse changed managed
files or deployments rather than silently replacing them.

For a previously adopted installation, completely quit Discord and run from
the latest extracted package or source checkout with matching build artifacts:

```sh
./install.sh --repair
```

Review the proposed repair and confirm with `YES`, then run `./install.sh --check`.
Repair requires the same Flatpak scope, a verified conventional Vencord loader
and original Discord archive, unchanged managed host files and launcher, and
an intact original Vencord backup. A replaced runtime must pass the normal
version and source-map plugin checks and have the same Vencord revision as the
original backup. These checks do not authenticate arbitrary external JavaScript.
Repair is explicit and does not bypass plugin removal consent.

If the runtime changed, its entire directory is moved into a unique
`.vencoverlay-recovery-dist-*` sibling directory in `~/.config/Vencord/` and
retained. The original `.vencoverlay-original-dist` is unchanged and remains
the uninstall baseline. Repair records the new verified deployment and loader
fingerprint. The reviewed runtime, original backup, host files, launcher,
loader, deployment and stopped state are rechecked before publication. Ordinary
publication failures roll back the repair. Retained recovery directories are
not removed by uninstall. If repair refuses, do not delete the record or backups;
inspect the reason before proceeding. Different external Vencord revisions,
changed backups, unknown loaders and custom plugin conflicts need separate review.

## Upgrade and removal

Reinstalling the verified release updates only managed files, preserves the
original launcher backup and any adopted runtime backup, and leaves settings
intact. Adopted runtime upgrades are detected automatically. Existing managed content
is checked against its recorded hashes; changed or unmanaged files cause a
refusal rather than an overwrite. Ordinary failures during publication trigger
rollback, with previous directories retained until publication succeeds.

`./uninstall.sh` removes the managed files and restores the original desktop
entry (or removes our entry if there was none). An automatically owned loader
is restored only if its original archive still matches the recorded hash.
An externally installed VencOverlay loader must be restored by its owner
**before** its managed sandbox runtime files are removed. For an adopted
Vencord loader, the original Vencord runtime is instead restored automatically
and the existing loader stays in place. A prepared-only installation can be removed if the
previous loader has not changed. Configuration and Discord/Vencord settings are
retained deliberately.

After successful removal of a managed desktop override, the uninstaller attempts
to refresh the original Flatpak export symlink using a timestamp-only operation
without following the link. The export must resolve to the verified active
Discord deployment. A missing or unexpected export, or insufficient permissions,
produces a warning without failing removal. The launcher contents and link
destination are unchanged; no privilege escalation is attempted. If a previous
local launcher is restored, this refresh is skipped. On GNOME, a stale application
list may still require a session refresh if the timestamp operation is unavailable.
On the affected Fedora 43 GNOME Wayland computer, the original icon reappeared
after this refresh during live uninstallation. Touching the user applications
directory and creating/removing a temporary desktop entry did not resolve the
observed problem. No system-wide file modification beyond the verified symlink
timestamps, elevated privileges or extension configuration changes are attempted.

There is no automatic crash-recovery journal, release signing, or automatic
repatching after Flatpak updates. A forced process kill or power loss during
multiple filesystem operations can leave `.vencoverlay-backup-*` directories or
an original `_app.asar`; stop and inspect before retrying, and do not delete
those backups. The installer refuses unknown deployments and unsafe ownership
changes. After Flatpak updates, run preflight again; compatible adopted installations
may require explicit repair after Vencord has been reapplied. Other installation targets, named additional
system installations and desktop environments remain untested. One automatic
startup failure after an upgrade was observed on GNOME; subsequent explicit and
normal desktop launches succeeded without a code change. Its cause remains
unconfirmed.

## Upstream references

- Vencord installer ASAR arrangement:
  https://github.com/Vencord/Installer/blob/main/patcher.go
- ASAR format used by Vencord Installer:
  https://github.com/Vencord/Installer/blob/main/app_asar.go
- Pinned Vencord loader and original-ASAR resolution:
  https://github.com/Vendicated/Vencord/blob/d086992fb674299a2fa05ac6432edc3a777389f3/src/main/patcher.ts
- Flathub Discord wrapper:
  https://github.com/flathub/com.discordapp.Discord/blob/master/discord.sh
