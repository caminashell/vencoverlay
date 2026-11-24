#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
source scripts/prerequisites.sh
mode=${1:-install}
[[ $# -le 1 ]] || die 'Expected at most one argument'
case "$mode" in
    -h|--help) printf '%s\n' 'Usage: ./setup.sh [--check | --report]' 'Default: offer missing Fedora RPMs, then synchronise Python dependencies.' '--check: check readiness without installation.' '--report: detailed read-only diagnostics.'; exit 0;;
    install|--check|--report) ;;
    *) die "Unknown option: $mode";;
esac
[[ -f pyproject.toml ]] || die 'Missing pyproject.toml; apply A1.1 first.'
[[ -z ${VIRTUAL_ENV:-} ]] || die 'Deactivate the virtual environment first.'
export UV_PROJECT_ENVIRONMENT="$PWD/.venv"
if [[ "$mode" != install ]]; then
    export UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never
fi
if command -v uv >/dev/null; then check_uv_location || die 'Use an independent uv installation.'; fi
if [[ "$mode" == --report ]]; then
    log 'System and project report'
    cat /etc/os-release || true
    printf 'Desktop: %s\nSession: %s\n' "${XDG_CURRENT_DESKTOP:-unknown}" "${XDG_SESSION_TYPE:-unknown}"
    python3 -c 'import tomllib; print("Project version:", tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'
    for tool in python3 uv gcc; do
        if command -v "$tool" >/dev/null; then "$tool" --version || true; else warn "$tool unavailable"; fi
    done
    for tool in node npm pnpm; do
        if command -v "$tool" >/dev/null; then
            printf '%s: %s (version not executed in read-only report)\n' "$tool" "$(command -v "$tool")"
        else warn "$tool unavailable"; fi
    done
    if command -v uv >/dev/null; then uv python list --only-installed --offline || true; fi
    if command -v git >/dev/null; then git status --short; git branch --show-current; git log -1 --oneline; fi
    if command -v pkg-config >/dev/null; then
        for library in girepository-2.0 gobject-introspection-1.0 cairo cairo-gobject gtk+-3.0 gtk-layer-shell-0 pango pangocairo gdk-pixbuf-2.0; do
            printf '%s: ' "$library"; pkg-config --modversion "$library" 2>/dev/null || printf 'Unavailable\n'
        done
    fi
fi
if [[ "$mode" != install ]]; then
    failed=0
    check_system || failed=1
    if command -v uv >/dev/null; then check_environment || failed=1; else failed=1; fi
    exit "$failed"
fi
check_fedora || die 'Development setup currently targets Fedora; existing runtime support is unchanged.'
[[ $EUID != 0 ]] || die 'Run as your normal user, not root.'
command -v rpm >/dev/null || die 'rpm is missing'
collect_missing_packages
if ((${#missing_packages[@]})); then
    printf 'Required Fedora RPMs to install:\n'; printf '  %s\n' "${missing_packages[@]}"
    read -r -p 'Install these packages with DNF? [y/N] ' answer || die 'Confirmation required'
    [[ "$answer" =~ ^([yY]|[yY][eE][sS])$ ]] || die 'Installation declined'
    sudo dnf install "${missing_packages[@]}"
fi
optional_missing=()
for package in "${OPTIONAL_PACKAGES[@]}"; do
    rpm -q "$package" >/dev/null 2>&1 || optional_missing+=("$package")
done
if ((${#optional_missing[@]})); then
    printf 'Optional backend RPMs:\n'; printf '  %s\n' "${optional_missing[@]}"
    if read -r -p 'Install optional backend packages? [y/N] ' answer && [[ "$answer" =~ ^([yY]|[yY][eE][sS])$ ]]; then
        sudo dnf install "${optional_missing[@]}"
    fi
fi
command -v uv >/dev/null || die 'Install uv from a trusted source (https://docs.astral.sh/uv/getting-started/installation/) and rerun setup.'
check_system || die 'System prerequisites incomplete.'
log 'Synchronising Python dependencies'
if [[ ! -f uv.lock ]]; then
    uv lock
fi
uv sync --locked --group dev --group build
check_environment || die 'Python environment verification failed.'
log 'Development environment ready'
