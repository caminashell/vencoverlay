# Install VencOverlay — Fedora x86-64 development preview

This package targets official stable Discord Flatpak (`com.discordapp.Discord`)
with an existing compatible Vencord installation on Fedora x86-64. Fedora 43
GNOME Wayland is the previously tested runtime baseline. Fedora 44 and later
versions may be compatible, but have not yet been verified. Other distributions,
architectures and desktops are not verified. A Fedora-wide package name does not
guarantee native library compatibility.
Vesktop already includes Vencord, but its installer integration is not available yet.
**Completely exit the target application before installation, updates or removal.**

1. Install [Discord](https://flathub.org/apps/com.discordapp.Discord) and
   [Vencord](https://vencord.dev/download/) beforehand. Confirm Vencord works.
2. Ensure Bash, host Python 3.11+, Flatpak, `flock`, `ostree`, GTK3, Cairo,
   Pango, GdkPixbuf, GLib/GObject and XWayland are available. No compiler,
   Git, virtual environment, Node or pnpm is needed.
3. Extract the archive as your regular user. Open a terminal in the extracted
   directory and verify the files with `sha256sum -c SHA256SUMS`. Compare the
   archive SHA-256 with the publisher's trusted checksum before extraction.
   Checksums do not authenticate the publisher. Only install trusted packages.
4. Completely quit Discord, including its tray/background process, and any
   standalone VencOverlay instance. Check compatibility and install:

   ```sh
   ./install.sh --check \
   && ./install.sh \
   && ./install.sh --check
   ```

   Review each result before proceeding; confirm the installation with `YES`.
   The installer detects the compatible existing Vencord layout automatically.
   After installation, preflight should report `Installation state: Installed`.
   Do not use `sudo`. Do not use `--prepare-only` for an active installation.
   The installer verifies the native executable checksum and checks GTK/Cairo/Pango
   and the selected desktop backend in the current graphical session without mapping
   an overlay. Run it from your logged-in desktop. It refuses incompatible native
   startup, unsupported layouts or incompatible Vencord builds.
   If both user and system Discord Flatpak installations are present, use
   `--scope user` or `--scope system` consistently. Do not bypass a refusal by
   manually overwriting Discord or Vencord files.
5. Start Discord normally via its desktop launcher. Enable VencOverlay in
   Vencord's Plugins settings, then join a voice channel. The overlay starts
   with Discord and is hidden outside a voice channel.

## Updating and removing

Completely quit Discord before updating or uninstalling. Extract a new trusted
compatible package into a new directory and repeat the verification and
installation steps. The installer detects upgrades automatically.
Keep the extracted installer for future removal:

```sh
./uninstall.sh
```

Confirm with `YES`. The installer restores the backed-up Vencord runtime and
retains user configuration and Discord settings. A previous preparation-only
installation can be completed directly with `./install.sh` if its loader and
managed files are unchanged and all compatibility checks pass. If preflight
reports `Incomplete` after Discord or Vencord reinstallation, inspect the
reason. For a previously adopted installation with an intact original backup,
a verified Vencord loader and a compatible runtime of the same original Vencord
revision, close Discord and run `./install.sh --repair`. Review and confirm the
repair, then run `./install.sh --check`. Changed external runtime files are
preserved in a separate `.vencoverlay-recovery-dist-*` directory; uninstall
still restores the original backup. Do not delete backups or bypass refusals.

When removal reveals the original Flatpak launcher, uninstall attempts to refresh
its desktop export symlink. If this is not permitted, a warning is printed but
removal still succeeds. The desktop may need a session refresh in that case.
No elevated privileges or GNOME extension changes are attempted. Do not run
the installer or uninstaller with sudo.

## About this package

`release-manifest.json` records the project version, target, Vencord revision,
source revision, working-tree status and file hashes. It records the Fedora
version on which the archive was assembled separately from the prior runtime
test baseline and recorded native build environment. Keep the native metadata
file alongside the executable. `source/` contains a project source snapshot;
the frontend ZIP includes pinned upstream Vencord source, custom plugin source
and legal notices. Native checks are not a guarantee of all runtime behaviour
or authentication of the publisher. This preview is not signed. Packages with
`source_dirty: true` are local test packages, not public release artifacts.

For third-party licences and corresponding source, see
`licenses/third-party-evidence.zip` and `licenses/source-notices.zip` when
included. The latter contains `notices/THIRD-PARTY-SOURCE.md`, which identifies
component attribution, licences and rebuild instructions. The matching separate
source archive is named in `licenses/source-asset.json`, along with its SHA-256;
for a public distribution it must be available from the same [GitHub release](https://github.com/caminashell/vencoverlay/releases)
as the installation package. Ordinary installation does not require downloading
that archive. Candidate packages with missing source materials are not for public
publication.

See the [VencOverlay repository](https://github.com/caminashell/vencoverlay)
for documentation and support. VencOverlay builds on
[Vesktop Overlay](https://github.com/Akii-G/vesktop-overlay) by
[Akii-G](https://github.com/Akii-G). See the included `LICENSE` and frontend
licence notices.
