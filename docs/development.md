# Development and builds

For installation with prebuilt artifacts, start at the [README](../README.md#installation).
This guide is for contributors and users who need to build their own artifacts.
The [technical specification](technical-specification.md) describes the implemented
architecture; the [frontend guide](frontend-development.md) covers the pinned Vencord toolchain.

## Build from source

Fedora Workstation is the development setup's current target. Install a trusted,
independent [uv](https://docs.astral.sh/uv/getting-started/installation/) installation,
Python 3.13+, Git, Node.js 22+ and npm. The setup script checks native development
libraries and offers to install missing Fedora RPMs with DNF. Keep the project's
virtual environment deactivated when running setup and build scripts.

Obtain the desired source revision from the project repository. For the current
cross-platform refactor development branch:

```sh
git clone --branch feature/cross-platform-refactor https://github.com/caminashell/vencoverlay.git \
&& cd vencoverlay \
&& ./setup.sh --frontend \
&& ./validate.sh --frontend --coverage \
&& ./build.sh \
&& ./build.sh --frontend \
&& ./build.sh --verify-frontend
```

The branch name above is the development baseline, not a permanent release
channel. Setup may need network access and confirmation to install missing
packages. Validation is a recommended quality gate, not an installation
dependency. Stop if a command fails. Build and validation do not patch Discord.
The two builds are independent and publish `dist/vencoverlay` (with
`dist/vencoverlay-native.json`) and `dist/vencoverlay-vencord.zip`.
After building, return to the [installation steps](../README.md#3-close-discord).
To install on another compatible machine, assemble and transfer a complete
[release package](#local-release-packaging); the development toolchain is not
needed on that machine. For normal use, Discord must already
be patched with Vencord. Vesktop bundles Vencord but does not yet have an
implemented installation path. Completely quit the target client and any
running overlay before installation, upgrades or removal.

## Python development and validation

```sh
./setup.sh
./validate.sh
./validate.sh --coverage
```

The Python environment uses locked dependencies with dev/build groups. Setup
checks native development prerequisites; validation does not install them.
`./setup.sh --check` and `./setup.sh --report` provide read-only diagnostics.
After changes to package metadata, including README content, run `./setup.sh`
to refresh editable package metadata before validation if necessary.

For a native graphical test, stop Discord and any installed overlay first:

```sh
./validate.sh --frontend --graphical --coverage
```

Frontend validation requires the already prepared upstream environment. See
[frontend development](frontend-development.md) for the source pin, frozen
package dependencies and separate frontend verification. Restart Discord
normally after graphical validation has finished; rebuild and upgrade only
when runtime or bundle changes need to be deployed.

## Offline demonstration and standalone executable

```sh
uv run --locked --no-sync --offline vencoverlay --demo
```

The demo cycles through idle and active states and both visual styles, without
Discord, a socket or network image downloads. Ctrl+C stops it. On Linux,
`SIGUSR1` sent to the application PID reported at startup toggles visibility.

```sh
./build.sh
./dist/vencoverlay --version
./dist/vencoverlay --demo
```

The verified Nuitka onefile executable remains dynamically linked and requires
compatible native libraries. The current build includes an isolated, version-
specific Nuitka 4.3.5 workaround for GTK3 typelib discovery; review it before
upgrading Nuitka. The remaining `gi.repository` compiler warning is not
suppressed; the compiled GNOME Wayland demo has been exercised successfully.
Do not start normal mode alongside another instance listening on port 6889.

## Project layout

| Path | Purpose |
|---|---|
| `src/vencoverlay/` | Python application, protocol, state, image services and rendering |
| `plugin/vencoverlay/` | Maintained TypeScript plugin and native IPC helper |
| `tests/` | Foundation, unit, integration, frontend and opt-in graphical tests |
| `scripts/` | Toolchain, build and Flatpak installation helpers |
| `setup.sh`, `validate.sh`, `build.sh` | Environment, quality gates and production builds |
| `install.sh`, `uninstall.sh` | Managed Discord Flatpak integration |
| `version.sh`, `stats.sh`, `snapshot.sh` | Version management, project statistics and source snapshots |
| `docs/` | User installation, developer guides and technical specification |
| `build/`, `dist/` | Generated, Git-ignored workspace and production artifacts |

## Maintenance and releases

`pyproject.toml` is the project version source. Use `./version.sh --help` to
inspect supported operations. `./stats.sh` reports repository statistics and
`./snapshot.sh` creates a filtered source archive; neither replaces validation.
Keep code, documentation, protocol tests and release artifacts aligned with the
same revision. Do not commit generated environments or distribution files.
The frontend archive carries source, legal notices and hashes; preserve these
when distributing binaries. Signing and comprehensive third-party release
licence review remain outstanding.

### Local release packaging

Prebuilt installation packages are distributed via GitHub Releases rather than
Git history once published. Generated artifacts and release archives remain under ignored `dist/`.
On a Fedora x86-64 development host, build both production artifacts and
assemble a package. Fedora 43 GNOME Wayland is the previously tested runtime
baseline; Fedora 44 and later versions are not yet verified. Prefer the oldest
intended runtime baseline for production builds until native ABI compatibility
has been established.

```sh
./build.sh
./build.sh --frontend
./release.sh
```

`release.sh` uses existing artifacts; it does not build, install or upload them.
The output is `dist/releases/vencoverlay-<version>-fedora-x86_64.tar.gz`.
Verify an existing archive without extracting or executing it:

```sh
./release.sh --verify dist/releases/vencoverlay-0.1.0.dev1-fedora-x86_64.tar.gz
```

Substitute the project version if it has changed. Use `./release.sh --replace`
only to regenerate a local package of the same version. Failed assembly does
not replace an existing archive. The archive contains installer helpers, both
artifacts, a standalone installation guide, licence, source snapshot, release
manifest and SHA-256 inventory. The outer archive SHA-256 is printed after
verification. Review the source snapshot before sharing it.

The packager checks the assembly-host family and architecture, ELF architecture,
reported native version, frontend archive and plugin source alignment. It records
the actual assembly host Fedora version separately from the prior runtime test
baseline. The native build records its host, libc identification, executable hash
and native input hashes; packaging checks these against the current source snapshot.
This is local provenance, not independently authenticated proof. No exact Fedora
version restriction is imposed. The installer checks ELF architecture, hashes and
version and runs `--check-runtime` in the current graphical session before activation.
This checks native startup, not every compositor or future ABI compatibility. The
manifest records Git revision and dirty state; dirty packages are for local
testing only. Before publishing, build fresh artifacts from a reviewed
clean revision, complete the dependency licence review and verify extracted
installation, updates and restoration on the supported target. Normal
`./install.sh` detects existing compatible Discord/Vencord integration and
adopted upgrades automatically. Vesktop installation remains separate work.
Publication is not automated.

### Third-party licence evidence

The native build retains `build/compilation-report.xml` (local, contains build paths)
and `build/native-licences.zip`. The latter contains candidate notices, exact RPM
source package names where ownership can be established, Python dependency metadata
and the compiler-reported modules, included DLLs and data files. Entries without an
RPM owner remain explicitly unresolved. It is not a complete inventory of statically
linked code, compiler runtime code or vendored components.

After building both artifacts, run:

```sh
./release.sh --audit-licences
./release.sh --licences
```

Use `--replace` with the second command if replacing an existing local package.
The first command verifies the frontend archive, reads the bundled source maps and
collects candidate licence files from the managed pnpm checkout; it writes
`build/release-licences.zip`. The evidence archive is bound by hashes to the native
executable and frontend archive. The second command includes it under
`licenses/third-party-evidence.zip` and verifies its identity and contents. The
manifest marks this evidence `pending-human-review`. Packages without evidence are
marked `not-collected` and remain useful for local testing only.

Do not publish either package as a compliant release yet. Review exact corresponding
source (including Fedora patches and compiler modifications), vendored code, any
missing notices and LGPL replacement/relinking requirements. Preserve the full
licences and make the required corresponding source available alongside the release.
The collected evidence is not legal certification, and source RPM names alone are
not a source distribution. Do not publish the raw compiler report without checking
it for private paths. The audit step never downloads sources or publishes anything.

### Companion third-party source asset

After the native and frontend builds and licence audit, collect source candidates:

```sh
./release.sh --collect-sources
./release.sh --licences --sources --replace
```

This developer-only operation requires network access, `curl`, system Python and
`rpm`. It refuses unresolved compiler-reported DLL or data-file ownership. It
retrieves exact Fedora source RPMs (including distribution patches and spec files)
from Koji, checks RPM digests and source-package identity, retrieves Python source
distributions from `uv.lock` with SHA-256 verification, and retrieves mapped npm
package tarballs with the pinned pnpm SHA-512 integrity values. Koji downloads
can be unsigned; these checks do not establish independent signature trust.
The collection is staged before replacing any companion archive. Downloads may
be substantially larger than the installation package; no source RPM is installed.

The companion archive is `dist/releases/vencoverlay-<version>-third-party-source.tar.gz`.
It contains the downloaded artifacts, extracted root notices and a source manifest
bound to the licence evidence and both compiled artifacts. The installer archive
includes the supplemental notices and the companion filename, size and SHA-256
when assembled using `--licences --sources`. Verify both archives before publishing:

```sh
./release.sh --verify-sources dist/releases/vencoverlay-0.1.0.dev1-third-party-source.tar.gz
./release.sh --verify dist/releases/vencoverlay-0.1.0.dev1-fedora-x86_64.tar.gz
```

Use the actual release version in these paths. Source verification requires the
matching local `build/release-licences.zip`. The installation archive is still
marked `pending-human-review`; package integrity is not a licence clearance.
The downloaded npm package may not be the preferred source for modification.
Review `missing_source_notices` in the companion source manifest. The collector
now includes reviewed supplemental notices from `docs/licences/` for Vencord
revision `d086992fb674299a2fa05ac6432edc3a777389f3` and `@vap/core` 0.0.12.
The `@vap/core` licence comes from its published Git revision; the vendored
component notices preserve the attributions in the pinned source and bundles.
The full MPL 2.0 text and MIT notices are provided alongside the existing
Apache 2.0 text. Review the [source and notices guide](licences/third-party-source.md)
for exact source locations and any outstanding upstream NOTICE requirements.
A changed Vencord pin or `@vap/core` version requires a new notice review.

### Rebuilding with modified dependencies

The onefile executable contains PyGObject and Pycairo extensions. Recipients
may rebuild and debug changes to these libraries. The normal release lockfile
is deliberately strict; modify the lockfile in a separate development tree
rather than disabling the production dependency checks. Use a compatible Fedora
43 x86-64 development machine or VM and keep the normal installation unchanged.

1. Extract `source/vencoverlay-source.zip` into a separate directory. Use the
   exact project revision from the release manifest, or initialise a local Git
   repository if starting from the extracted source. Follow the normal Fedora
   development prerequisites above. Keep the project environment deactivated.
2. Extract the matching PyGObject and Pycairo source tarballs from the companion
   archive's `python/` directory into a separate source workspace. Preserve
   their licences and notices. Modify the library sources as desired.
3. In the disposable project's `pyproject.toml`, add the following table (or
   merge these entries into an existing `[tool.uv.sources]` table). Replace the
   example paths with the actual extracted source directories. Keep dependency
   names and compatible version metadata unchanged for the initial test.

   ```toml
   [tool.uv.sources]
   pygobject = { path = "/absolute/path/to/pygobject-3.58.1" }
   pycairo = { path = "/absolute/path/to/pycairo-1.29.2" }
   ```

4. Pycairo 1.29.2 declares `[tool.uv]` with `package = false`. When using its
   extracted source as a local dependency, change that value to `true` in the
   extracted Pycairo `pyproject.toml`, not the VencOverlay configuration. Otherwise
   uv can resolve the dependency without installing the `cairo` extension. For
   the exact version above, the following command makes the tested correction:

   ```sh
   sed -i 's/^package = false$/package = true/' \
       /absolute/path/to/pycairo-1.29.2/pyproject.toml
   ```

   Substitute the actual extracted path and confirm the value is now `true`.
   Re-resolve and install the modified local sources. Build dependencies may
   need network access and additional development packages for the libraries.
   Stop Discord and VencOverlay before the graphical runtime check.

   ```sh
   uv lock
   ./setup.sh
   uv run --locked --no-sync --offline python -c 'import gi, cairo; print(gi.__file__, cairo.__file__)'
   ./build.sh
   ./dist/vencoverlay --version
   env -u PYTHONPATH -u PYTHONHOME -u VIRTUAL_ENV \
       ./dist/vencoverlay --check-runtime
   ```

5. Verify that an intentional observable modification in each library is
   present when the rebuilt binary runs (for example, a diagnostic marker in
   its initialisation code). Inspect the build report to confirm the modified
   extensions were bundled. Run the demo and graphical checks on that test
   host. A successful unmodified build alone is not a replacement/rebuild test.
   Do not publish this modified test artifact with the original source manifest.

For bundled Fedora libraries and typelibs, use the matching source RPMs from
`rpm/`, including their spec files and patches. Build modified packages using
Fedora's RPM build tools in a compatible isolated environment, install the
appropriate rebuilt runtime and development packages there, and repeat the
native build. System-supplied libraries remain dynamically linked. The exact
RPM rebuild workflow depends on the package; follow the Fedora package's spec
and build dependencies. Preserve the matching modified sources if redistributing.

Nuitka 4.3.5's source is in the companion `python/` directory. The isolated GI
compiler modification is fully expressed in `build.sh`: it copies the compiler
package and changes the unversioned `Repository.require` query to use the pinned
GTK3 namespace versions. To rebuild with that source, use an additional local
`nuitka = { path = "/absolute/path/to/nuitka-4.3.5" }` source entry and repeat the
lock and setup steps. The script reapplies the documented modification and
rejects incompatible compiler versions or changes to the expected patch site.

#### Verified modified-extension rebuild

On 26 November 2026, the development candidate `0.1.0.dev1` was rebuilt in an
isolated Fedora 43 x86-64 workspace using CPython 3.14.10, GCC 15.3.1,
Nuitka 4.3.5, PyGObject 3.58.1 and Pycairo 1.29.2. Both library source archives
came from the candidate companion archive. The two native initialisation
functions (`PyInit__gi` in `gi/gimodule.c` and `PyInit__cairo` in
`cairo/cairomodule.c`) were modified to emit distinct diagnostic markers with
`PySys_WriteStderr`. The Pycairo packaging correction above was necessary.
After the local source substitution, `uv lock`, `./setup.sh` and `./build.sh`
succeeded. The isolated executable reported `VencOverlay 0.1.0.dev1` and the
runtime probe, with the Python environment variables unset as above, emitted:

```text
VENCOVERLAY_TEST_MODIFIED_PYGOBJECT
VENCOVERLAY_TEST_MODIFIED_PYCAIRO
VencOverlay runtime check passed
```

This confirms the modified C extensions were loaded by the rebuilt onefile
executable and the graphical startup probe passed. The main repository,
installation and production artifacts were not modified by this test. The
modified test binary is not a release asset. This verifies the two Python
extension rebuild routes on the stated environment; it does not verify every
Fedora source RPM rebuild, other platforms, bit-for-bit reproducibility, or
complete redistribution compliance. Normal installation requires no compiler.
Never publish the installation package without its matching required source.

### Publication checklist

The first planned public version is `0.1.0.dev1`. A locally verified development
archive is not itself a public release. Before publication:

1. Review outstanding source changes, validate them and complete the dependency
   licence and corresponding-source review for the native and frontend bundles.
   Confirm the packaged source inventory contains no private material.
2. Set the intended version with `./version.sh set VERSION` (replace `VERSION`
   with the chosen PEP 440 version). Refresh the environment with `./setup.sh`.
   Run `./version.sh check` and `./validate.sh --frontend --graphical --coverage`
   with Discord and VencOverlay stopped. Review and commit the intended source,
   documentation and lockfile changes; leave generated artifacts ignored.
3. From that clean committed revision, run both `./build.sh` and
   `./build.sh --frontend`, verify the frontend with `./build.sh --verify-frontend`
   then run `./release.sh --audit-licences` and `./release.sh --collect-sources`.
   Review the source inventory, supplemental notices and modification test.
   Assemble with `./release.sh --licences --sources --replace`. Verify both
   archives using `--verify-sources` and `--verify` as shown above.
4. Inspect `release-manifest.json`: `source_dirty` must be false and
   `source_revision` must match the intended release commit. Confirm all versions,
   native inputs, plugin source and packaged checksums agree. Extract into a
   fresh directory and test install, launch, upgrade and removal on the supported
   Fedora target; do not claim untested platforms or live webcam support.
5. Check the companion `source-manifest.json` against the final native and
   frontend inventories. Confirm no unresolved copied native ownership or
   missing source notices remain, that the source guide and reviewed supplemental
   notices are present, and that the exact preferred source for modification
   and any applicable additional notices are available. Verify the project and
   frontend source archives include the corresponding build scripts. Check that
   the installation archive's `licenses/source-asset.json` identifies the
   actual companion archive by name, size and SHA-256. Confirm the verified
   modified-extension rebuild still applies to the final dependency identities.
6. Record the maintainer's manual licence/source review against the exact
   installation and companion SHA-256 values. The manifest records automated
   evidence status, not a human or legal certification; do not treat a verified
   archive as permission to publish. Push the reviewed source commit and create
   a matching Git tag and GitHub pre-release only after the review passes.
   Upload both verified archives and `SHA256SUMS` to the same release. Generate
   the outer checksums from the release directory (use the actual version):

   ```sh
   (
       cd dist/releases
       sha256sum vencoverlay-0.1.0.dev1-fedora-x86_64.tar.gz \
           vencoverlay-0.1.0.dev1-third-party-source.tar.gz > SHA256SUMS
       sha256sum -c SHA256SUMS
   )
   ```

   Keep the required source available alongside the binaries without an
   additional charge. Document tested platforms, Vencord compatibility,
   recovery restrictions and known limitations. Do not publish a dirty build.

The packaging script never creates commits, tags, uploads or releases. Signing
and automatic publication are not implemented. The current release is intended
as an explicitly labelled development preview, not a stable release.
