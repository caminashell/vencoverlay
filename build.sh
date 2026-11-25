#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"
source scripts/prerequisites.sh

usage() {
    cat <<'HELP'
Usage: ./build.sh [--jobs N] [--no-compression]
       ./build.sh --frontend
       ./build.sh --verify-frontend
Build and verify the Linux onefile executable at dist/vencoverlay.
Build the minified Vencord distribution at dist/vencoverlay-vencord.zip with --frontend.
Verify an existing frontend distribution with --verify-frontend.
Run ./setup.sh and ./validate.sh first (add --frontend for the frontend). No dependencies are installed.
HELP
}
if [[ ${1:-} == --frontend || ${1:-} == --verify-frontend ]]; then
    [[ $# == 1 ]] || die 'Frontend mode does not accept native build options.'
    if [[ $1 == --frontend ]]; then
        exec bash scripts/frontend.sh --build
    else
        exec bash scripts/frontend.sh --verify
    fi
fi
options=()
while (($#)); do
    case "$1" in
        -h|--help) usage; exit 0;;
        --jobs)
            [[ ${2:-} =~ ^[1-9][0-9]*$ ]] || die '--jobs requires a positive integer.'
            options+=("--jobs=$2")
            shift 2;;
        --no-compression) options+=(--onefile-no-compression); shift;;
        *) die "Unknown argument: $1 (use --help)";;
    esac
done
export UV_PROJECT_ENVIRONMENT="$PROJECT_ROOT/.venv"
export UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never
log 'Checking development prerequisites'
check_system || die 'System prerequisites incomplete; run ./setup.sh.'
check_environment || die 'Environment needs repair; run ./setup.sh.'
./version.sh check || die 'Project version or lockfile check failed.'
runner=(uv run --locked --no-sync --offline)
expected=$("${runner[@]}" vencoverlay --version)
log 'Checking Nuitka'
"${runner[@]}" python -m nuitka --version
# The runtime imports GI lazily so backend selection precedes GTK initialisation.
# Nuitka 4.3 includes GI typelibs through explicit repository namespace imports.
native_options=(
    --include-package=cairo
    --include-module=gi
    --include-module=gi._gi_cairo
    --include-module=gi.repository.Gtk
    --include-module=gi.repository.Gdk
    --include-module=gi.repository.GdkPixbuf
    --include-module=gi.repository.GLib
    --include-module=gi.repository.GObject
    --include-module=gi.repository.Gio
    --include-module=gi.repository.Atk
    --include-module=gi.repository.Pango
    --include-module=gi.repository.PangoCairo
    --include-module=gi.repository.cairo
)
# Retain CPython's accelerated stdlib implementations instead of Nuitka's
# optional pure-Python fallbacks. JSON parsing is on the live message path.
accelerator_options=(--include-module=_json --include-module=_bisect)
# Layer Shell is optional on the build host, as in the development prerequisites.
if "${runner[@]}" python -c 'import gi; gi.require_version("GtkLayerShell", "0.1")' >/dev/null 2>&1; then
    native_options+=(--include-module=gi.repository.GtkLayerShell)
    log 'Including optional GTK Layer Shell support'
else
    warn 'GTK Layer Shell typelib unavailable on the build host; X11/XWayland build only.'
fi
mkdir -p build dist
stage=$(mktemp -d "$PROJECT_ROOT/build/nuitka.XXXXXXXX")
publish=''
cleanup() {
    if [[ -n $publish ]]; then rm -f -- "$publish"; fi
    rm -rf -- "$stage"
}
trap cleanup EXIT
# Nuitka 4.3.5's GI plugin queries unversioned typelibs when generating
# virtual modules. On hosts with GTK3 and GTK4, this can discover GTK4
# dependencies even though the application requires GTK3 at runtime.
# Use an isolated compiler copy; never patch the installed dependency.
log 'Preparing version-pinned GTK3 compiler discovery'
"${runner[@]}" python - "$stage/compiler" <<'PYTHON'
from importlib.metadata import version
from importlib.util import find_spec
from pathlib import Path
from shutil import copytree, ignore_patterns
import sys

if version("Nuitka") != "4.3.5":
    raise SystemExit("Review the GTK3 GI workaround for this Nuitka version before building.")
spec = find_spec("nuitka")
if spec is None or spec.origin is None:
    raise SystemExit("Cannot locate installed Nuitka package.")
source = Path(spec.origin).parent
local = Path(sys.argv[1]) / "nuitka"
plugin = source / "plugins/standard/GiPlugin.py"
text = plugin.read_text(encoding="utf-8")
original = "Repository.require('%s', None)"
if text.count(original) != 1:
    raise SystemExit("Nuitka GI discovery implementation changed; review the GTK3 workaround.")
versions = {
    "Gtk": "3.0", "Gdk": "3.0", "GdkX11": "3.0", "GdkWayland": "3.0",
    "GdkPixbuf": "2.0", "GLib": "2.0", "GObject": "2.0", "Gio": "2.0",
    "Atk": "1.0", "Pango": "1.0", "PangoCairo": "1.0", "cairo": "1.0",
    "GtkLayerShell": "0.1",
}
replacement = "namespace = '%s'\nRepository.require(namespace, " + repr(versions) + ".get(namespace))"
copytree(source, local, ignore=ignore_patterns("__pycache__", "*.pyc"))
(local / "plugins/standard/GiPlugin.py").write_text(
    text.replace(original, replacement), encoding="utf-8"
)
print("Prepared isolated Nuitka 4.3.5 with version-pinned GI discovery.")
PYTHON
log 'Building VencOverlay Linux onefile executable'
"${runner[@]}" env PYTHONPATH="$stage/compiler" python -m nuitka --mode=onefile \
    --include-distribution-metadata=vencoverlay \
    --output-dir="$stage" --output-filename=vencoverlay \
    --report="$stage/compilation-report.xml" \
    "${native_options[@]}" "${accelerator_options[@]}" "${options[@]}" src/vencoverlay
log 'Verifying bundled GTK typelibs'
"${runner[@]}" python - "$stage/compilation-report.xml" <<'PYTHON'
from pathlib import Path
import sys
from xml.etree import ElementTree

report = ElementTree.parse(sys.argv[1])
typelibs = {
    Path(node.get("name", "")).name
    for node in report.iter("data_file")
    if node.get("name", "").endswith(".typelib")
}
required = {"Gtk-3.0.typelib", "Gdk-3.0.typelib", "PangoCairo-1.0.typelib",
            "GdkPixbuf-2.0.typelib", "Pango-1.0.typelib"}
missing = required - typelibs
conflicts = {name for name in typelibs if name.startswith(
    ("Gtk-4.", "Gdk-4.", "GdkX11-4.", "GdkWayland-4.", "Gsk-")
)}
if missing or conflicts:
    raise SystemExit(f"Invalid GTK3 bundle: missing={sorted(missing)}, conflicts={sorted(conflicts)}")
print("GTK3 typelib bundle verified; no GTK4/GSK typelibs included.")
PYTHON
log 'Verifying bundled standard-library accelerators'
"${runner[@]}" python - "$stage/compilation-report.xml" <<'PYTHON'
import sys
from xml.etree import ElementTree

report = ElementTree.parse(sys.argv[1])
modules = {node.get("name") for node in report.iter("module")}
missing = {"_json", "_bisect"} - modules
if missing:
    raise SystemExit(f"Missing standard-library accelerators: {sorted(missing)}")
print("Standard-library accelerators verified: _json, _bisect.")
PYTHON
binary="$stage/vencoverlay"
[[ -f $binary && -x $binary ]] || die 'Compiled executable missing.'
log 'Verifying compiled executable'
actual=$("$binary" --version)
[[ $actual == "$expected" ]] || die "Version mismatch: $actual (expected $expected)"
help_output=$("$binary" --help)
printf '%s\n' "$help_output"
grep -Fq -- '--demo' <<< "$help_output" || die 'Compiled demo option missing.'
status=0
"$binary" --unknown >"$stage/invalid.log" 2>&1 || status=$?
[[ $status == 2 ]] || die "Unexpected invalid argument exit status: $status"
# Test startup failure without opening a display, binding a port or touching user data.
mkdir -p "$stage/smoke-config/vencoverlay"
printf 'port = 0\n' > "$stage/smoke-config/vencoverlay/config.toml"
status=0
XDG_CONFIG_HOME="$stage/smoke-config" "$binary" >"$stage/runtime.log" 2>&1 || status=$?
[[ $status == 1 ]] || {
    cat "$stage/runtime.log" >&2
    die "Unexpected configuration failure exit status: $status"
}
grep -Fq 'port must be 1 to 65535' "$stage/runtime.log" || {
    cat "$stage/runtime.log" >&2
    die 'Unexpected runtime output.'
}
publish=$(mktemp "$PROJECT_ROOT/dist/.vencoverlay.XXXXXXXX")
install -m 755 -- "$binary" "$publish"
mv -fT -- "$publish" "$PROJECT_ROOT/dist/vencoverlay"
publish=''
log 'Build completed successfully'
printf '%s\nExecutable: %s\n' "$actual" "$PROJECT_ROOT/dist/vencoverlay"
