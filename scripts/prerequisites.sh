#!/usr/bin/env bash
# Shared Fedora development checks. Sourcing this file performs no actions.
SYSTEM_PACKAGES=(python3 python3-devel gcc gcc-c++ make git patchelf pkgconf-pkg-config glib2-devel gobject-introspection-devel cairo-devel cairo-gobject-devel gtk3 pango gdk-pixbuf2)
OPTIONAL_PACKAGES=(gtk-layer-shell gtk-layer-shell-devel xorg-x11-server-Xwayland)
log() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
check_uv_location() {
    local executable resolved environment
    [[ -z ${VIRTUAL_ENV:-} ]] || { warn 'Deactivate the virtual environment first.'; return 1; }
    executable=$(type -P uv) || { warn 'uv unavailable'; return 1; }
    resolved=$(realpath -- "$executable") || return 1
    environment=$(realpath -m -- "$PWD/.venv") || return 1
    case "$executable" in "$PWD/.venv/"*) warn 'uv must be installed outside the project environment.'; return 1;; esac
    case "$resolved" in "$environment/"*) warn 'uv must be installed outside the project environment.'; return 1;; esac
}
check_fedora() {
    [[ -r /etc/os-release ]] || return 1
    ( . /etc/os-release; [[ ${ID:-} == fedora ]] )
}
collect_missing_packages() {
    missing_packages=()
    local package
    for package in "${SYSTEM_PACKAGES[@]}"; do
        rpm -q "$package" >/dev/null 2>&1 || missing_packages+=("$package")
    done
}
check_system() {
    local failed=0 tool library package
    check_fedora || { warn 'Development setup currently targets Fedora.'; return 1; }
    command -v rpm >/dev/null || return 1
    check_uv_location || failed=1
    collect_missing_packages
    if ((${#missing_packages[@]})); then
        printf 'Missing RPM: %s\n' "${missing_packages[@]}"
        failed=1
    fi
    for tool in python3 uv git gcc g++ make patchelf pkg-config; do
        if command -v "$tool" >/dev/null; then printf 'OK: %s\n' "$tool"; else warn "Missing command: $tool"; failed=1; fi
    done
    for library in girepository-2.0 cairo cairo-gobject; do
        if pkg-config --exists "$library" 2>/dev/null; then printf '%s: %s\n' "$library" "$(pkg-config --modversion "$library")"; else warn "Missing pkg-config module: $library"; failed=1; fi
    done
    for package in "${OPTIONAL_PACKAGES[@]}"; do
        rpm -q "$package" || warn "Optional backend package missing: $package"
    done
    return "$failed"
}
check_environment() {
    local failed=0
    [[ -f uv.lock ]] || { warn 'uv.lock missing'; return 1; }
    UV_PYTHON_DOWNLOADS=never uv lock --check --offline || failed=1
    UV_PYTHON_DOWNLOADS=never uv sync --check --locked --offline --group dev --group build || failed=1
    if [[ -x .venv/bin/python ]]; then
        .venv/bin/python - <<'PY' || failed=1
import sys
import gi
import cairo
for namespace, version in (("Gtk", "3.0"), ("Gdk", "3.0"), ("GdkPixbuf", "2.0"), ("Pango", "1.0"), ("PangoCairo", "1.0")):
    gi.require_version(namespace, version)
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo
# Do not initialise a display or create a window.
print("Python:", sys.version.split()[0], "GTK:", Gtk.get_major_version(), "Cairo:", cairo.cairo_version_string())
try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell
except (ImportError, ValueError) as exc:
    print("Optional GTK Layer Shell unavailable:", exc)
else:
    print("GTK Layer Shell import available")
PY
    else warn 'Project virtual environment missing'; failed=1; fi
    return "$failed"
}
