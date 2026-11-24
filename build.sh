#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"
source scripts/prerequisites.sh

usage() {
    cat <<'HELP'
Usage: ./build.sh [--jobs N] [--no-compression]
Build and verify the Linux onefile executable at dist/vencoverlay.
Run ./setup.sh and ./validate.sh first. No dependencies are installed.
HELP
}
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
mkdir -p build dist
stage=$(mktemp -d "$PROJECT_ROOT/build/nuitka.XXXXXXXX")
publish=''
cleanup() {
    if [[ -n $publish ]]; then rm -f -- "$publish"; fi
    rm -rf -- "$stage"
}
trap cleanup EXIT
log 'Building VencOverlay Linux onefile executable'
"${runner[@]}" python -m nuitka --mode=onefile \
    --include-distribution-metadata=vencoverlay \
    --output-dir="$stage" --output-filename=vencoverlay \
    "${options[@]}" src/vencoverlay
binary="$stage/vencoverlay"
[[ -f $binary && -x $binary ]] || die 'Compiled executable missing.'
log 'Verifying compiled executable'
actual=$("$binary" --version)
[[ $actual == "$expected" ]] || die "Version mismatch: $actual (expected $expected)"
"$binary" --help
status=0
"$binary" --unknown >"$stage/invalid.log" 2>&1 || status=$?
[[ $status == 2 ]] || die "Unexpected invalid argument exit status: $status"
status=0
"$binary" >"$stage/runtime.log" 2>&1 || status=$?
[[ $status == 1 ]] || die "Unexpected placeholder exit status: $status"
grep -Fxq 'VencOverlay: application runtime not yet implemented.' "$stage/runtime.log" || die 'Unexpected runtime output.'
publish=$(mktemp "$PROJECT_ROOT/dist/.vencoverlay.XXXXXXXX")
install -m 755 -- "$binary" "$publish"
mv -fT -- "$publish" "$PROJECT_ROOT/dist/vencoverlay"
publish=''
log 'Build completed successfully'
printf '%s\nExecutable: %s\n' "$actual" "$PROJECT_ROOT/dist/vencoverlay"
