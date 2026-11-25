#!/usr/bin/env bash
# Isolated Vencord development tooling; never injects into Discord.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
source scripts/prerequisites.sh
REVISION=d086992fb674299a2fa05ac6432edc3a777389f3
PREVIOUS_REVISION=8d64c8be7e68616f8f116a6fff9480051aa2a0c5
UPSTREAM=https://github.com/Vendicated/Vencord.git
PNPM_VERSION=12.15.0
WORK="$ROOT/build/vencord-dev"
CHECKOUT="$WORK/source"
TOOLCHAIN="$WORK/toolchain"
PNPM="$TOOLCHAIN/node_modules/.bin/pnpm"
PLUGIN="$ROOT/plugin/vencoverlay"
mode=${1:---check}
[[ $# -le 1 ]] || die 'Expected at most one frontend command.'
case "$mode" in
    --help|-h)
        printf '%s\n' 'Usage: bash scripts/frontend.sh [--setup | --check | --typecheck | --lint | --report | --build | --verify]' \
            '--setup: fetch pinned upstream and install frozen dependencies (network access).' \
            '--check: read-only readiness check; no downloads.' \
            '--typecheck / --lint: copy current plugin to managed checkout and run upstream tools.' \
            '--report: read-only toolchain inventory.' \
            '--build: validate, compile and publish a minified desktop distribution.' \
            '--verify: verify dist/vencoverlay-vencord.zip without the Node toolchain.'
        exit 0;;
    --setup|--check|--typecheck|--lint|--report|--build|--verify) ;;
    *) die "Unknown frontend command: $mode";;
esac
if [[ "$mode" == --verify ]]; then
    exec python3 scripts/frontend_artifacts.py verify "$ROOT/dist/vencoverlay-vencord.zip"
fi
# Never follow a relocated build directory or an unmanaged checkout.
for path in "$ROOT/build" "$WORK" "$CHECKOUT" "$TOOLCHAIN"; do
    [[ ! -L "$path" ]] || die "Refusing symlinked frontend path: $path"
done
# Serialise operations that replace the generated plugin or upstream dist.
case "$mode" in
    --setup|--typecheck|--lint|--build)
        if [[ "$mode" == --setup && $EUID == 0 ]]; then die 'Run frontend setup as your normal user.'; fi
        command -v flock >/dev/null || die 'flock is required for frontend development operations.'
        mkdir -p "$ROOT/build"
        [[ ! -L "$ROOT/build/.vencord-dev.lock" ]] || die 'Refusing symlinked frontend lock.'
        exec 9>"$ROOT/build/.vencord-dev.lock"
        flock -n 9 || die 'Another frontend operation is running; retry after it finishes.';;
esac
check_node() {
    command -v node >/dev/null || { warn 'Node.js unavailable; activate Node.js >=22 (e.g. via nvm).'; return 1; }
    node -e 'if (Number(process.versions.node.split(".")[0]) < 22) { console.error("Node.js >=22 required"); process.exit(1); }' || return 1
    printf 'Node.js: %s\n' "$(node --version)"
}
check_source() {
    local changes
    [[ -f "$WORK/managed" && -d "$CHECKOUT/.git" ]] || { warn 'Managed Vencord checkout missing; run ./setup.sh --frontend.'; return 1; }
    [[ $(git -C "$CHECKOUT" remote get-url origin) == "$UPSTREAM" ]] || { warn 'Unexpected Vencord origin.'; return 1; }
    [[ $(git -C "$CHECKOUT" rev-parse HEAD) == "$REVISION" ]] || { warn 'Vencord checkout revision does not match pin.'; return 1; }
    changes=$(git --no-optional-locks -C "$CHECKOUT" status --porcelain --untracked-files=all) || return 1
    [[ -z "$changes" ]] || { warn 'Managed Vencord checkout contains unexpected changes; inspect it before continuing.'; return 1; }
    [[ -f "$CHECKOUT/pnpm-lock.yaml" && -f "$CHECKOUT/pnpm-workspace.yaml" ]] || return 1
    node -e 'const p=require(process.argv[1]); if(p.packageManager !== "pnpm@"+process.argv[2]) process.exit(1)' "$CHECKOUT/package.json" "$PNPM_VERSION" || return 1
}
fingerprint() {
    (cd "$CHECKOUT"; sha256sum package.json pnpm-lock.yaml pnpm-workspace.yaml)
    node --version
    printf '%s\n' "$REVISION" "$PNPM_VERSION"
}
check_ready() {
    check_node && check_source || return 1
    [[ -x "$CHECKOUT/node_modules/.bin/tsc" && -x "$CHECKOUT/node_modules/.bin/oxlint" && -x "$PNPM" ]] || { warn 'Frontend dependencies missing; run ./setup.sh --frontend.'; return 1; }
    [[ -f "$WORK/dependencies.ready" ]] && [[ $(cat "$WORK/dependencies.ready") == "$(fingerprint)" ]] || { warn 'Frontend dependencies need synchronising; run ./setup.sh --frontend.'; return 1; }
    printf 'Vencord: %s\npnpm (pinned): %s\n' "$REVISION" "$PNPM_VERSION"
}
stage_plugin() {
    [[ -d "$PLUGIN" && -f "$PLUGIN/index.ts" && -f "$PLUGIN/native.ts" ]] || die 'VencOverlay plugin source missing.'
    python3 - "$PLUGIN" "$CHECKOUT" <<'PY'
import shutil
import sys
from pathlib import Path

source, checkout = map(Path, sys.argv[1:])
userplugins = checkout / 'src/userplugins'
target = userplugins / 'vencoverlay'
if any(p.is_symlink() for p in (source, checkout / 'src', userplugins, target)):
    raise SystemExit('Refusing symlinked plugin paths')
if any(p.is_symlink() for p in source.rglob('*')):
    raise SystemExit('Refusing symlinks in plugin source')
userplugins.mkdir(exist_ok=True)
if any(p != target for p in userplugins.iterdir()):
    raise SystemExit('Unexpected userplugins in managed checkout; inspect before continuing')
if target.exists():
    shutil.rmtree(target)
shutil.copytree(source, target, ignore=shutil.ignore_patterns('node_modules', '__pycache__'))
PY
}
lint_plugin() (
    cd "$CHECKOUT"
    # Upstream ignores src/userplugins in Git. Oxlint skips ignored directories
    # during traversal, but accepts explicitly named source files.
    shopt -s globstar nullglob
    local files=(src/userplugins/vencoverlay/**/*.{ts,tsx,js,jsx,mts,cts,mjs,cjs})
    ((${#files[@]} > 0)) || die 'No VencOverlay source files to lint.'
    node_modules/.bin/oxlint --deny-warnings "${files[@]}"
)
build_production() (
    # Do not touch any installed Discord or Vesktop files. Preserve any previous
    # checkout output; publish a single verified archive, not a loose directory.
    check_ready || die 'Frontend prerequisites incomplete; run ./setup.sh --frontend.'
    [[ ! -L "$ROOT/dist" ]] || die 'Refusing symlinked distribution directory.'
    [[ ! -L "$CHECKOUT/dist" ]] || die 'Refusing symlinked upstream output directory.'
    stage_plugin
    (cd "$CHECKOUT"; node_modules/.bin/tsc --noEmit)
    lint_plugin
    mkdir -p "$ROOT/dist"
    stage=$(mktemp -d "$WORK/production.XXXXXXXX")
    moved=false
    building=false
    cleanup_production() {
        if [[ $building == true ]]; then rm -rf -- "$CHECKOUT/dist"; fi
        if [[ $moved == true ]]; then
            mv -T -- "$stage/previous-dist" "$CHECKOUT/dist" || {
                warn "Previous checkout output retained at $stage/previous-dist"; return 1;
            }
        fi
        rm -rf -- "$stage"
    }
    trap cleanup_production EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    if [[ -e "$CHECKOUT/dist" ]]; then
        [[ -d "$CHECKOUT/dist" ]] || die 'Unexpected upstream output path.'
        mv -T -- "$CHECKOUT/dist" "$stage/previous-dist"
        moved=true
    fi
    building=true
    timestamp=$(git -C "$CHECKOUT" show -s --format=%ct "$REVISION")
    [[ $timestamp =~ ^[0-9]+$ ]] || die 'Invalid upstream commit timestamp.'
    timestamp+=000
    log 'Building minified Vencord desktop targets'
    (cd "$CHECKOUT"; VENCORD_HASH="${REVISION:0:7}" VENCORD_REMOTE=Vendicated/Vencord \
        SOURCE_DATE_EPOCH="$timestamp" node "$ROOT/scripts/frontend-build.mjs")
    check_source || die 'Upstream checkout changed during production build.'
    log 'Verifying and packaging production artifacts'
    python3 "$ROOT/scripts/frontend_artifacts.py" package --root "$ROOT" --checkout "$CHECKOUT" \
        --revision "$REVISION" --node "$(node --version)" --pnpm "$PNPM_VERSION" \
        --timestamp "$timestamp" --output "$ROOT/dist/vencoverlay-vencord.zip"
)
case "$mode" in
    --build) build_production;;
    --report)
        printf 'Vencord revision: %s\npnpm pin: %s\nCheckout: %s\n' "$REVISION" "$PNPM_VERSION" "$CHECKOUT"
        for tool in node npm; do command -v "$tool" || true; done
        check_ready;;
    --check) check_ready;;
    --setup)
        [[ $EUID != 0 ]] || die 'Run frontend setup as your normal user.'
        check_node || die 'Node.js prerequisites incomplete.'
        command -v npm >/dev/null || die 'npm unavailable; install with Node.js.'
        if [[ ! -e "$WORK" ]]; then
            mkdir -p "$WORK"
            printf '%s\n' 'VencOverlay managed development checkout' > "$WORK/managed"
        fi
        [[ -f "$WORK/managed" ]] || die "Refusing unmanaged frontend directory: $WORK"
        if [[ ! -e "$CHECKOUT" ]]; then
            log 'Fetching pinned Vencord source'
            git init -q "$CHECKOUT"
            git -C "$CHECKOUT" remote add origin "$UPSTREAM"
        fi
        # Allow retry after initial fetch or an explicit migration from the previous pin.
        if ! git -C "$CHECKOUT" rev-parse --verify HEAD >/dev/null 2>&1; then
            [[ $(git -C "$CHECKOUT" remote get-url origin) == "$UPSTREAM" ]] || die 'Unexpected Vencord origin.'
            git -C "$CHECKOUT" fetch --depth=1 origin "$REVISION"
            git -C "$CHECKOUT" checkout --detach "$REVISION"
        fi
        current=$(git -C "$CHECKOUT" rev-parse HEAD)
        if [[ "$current" != "$REVISION" ]]; then
            [[ "$current" == "$PREVIOUS_REVISION" ]] || die 'Unexpected Vencord revision; refusing migration.'
            [[ $(git -C "$CHECKOUT" remote get-url origin) == "$UPSTREAM" ]] || die 'Unexpected Vencord origin.'
            changes=$(git --no-optional-locks -C "$CHECKOUT" status --porcelain --untracked-files=all) || die 'Cannot inspect checkout.'
            [[ -z "$changes" ]] || die 'Managed Vencord checkout contains unexpected changes; refusing migration.'
            [[ ! -L "$CHECKOUT/src/userplugins" ]] || die 'Refusing symlinked userplugins.'
            if [[ -d "$CHECKOUT/src/userplugins" ]]; then
                unexpected=$(find "$CHECKOUT/src/userplugins" -mindepth 1 -maxdepth 1 ! -name vencoverlay -print)
                [[ -z "$unexpected" ]] || die 'Unexpected userplugins in managed checkout; refusing migration.'
            fi
            log 'Updating managed Vencord checkout to the new pinned revision'
            git -C "$CHECKOUT" fetch --depth=1 origin "$REVISION"
            git -C "$CHECKOUT" checkout --detach "$REVISION"
        fi
        check_source || die 'Vencord source verification failed.'
        rm -f "$WORK/dependencies.ready"
        if [[ ! -x "$PNPM" ]] || [[ $(cd "$ROOT"; "$PNPM" --version) != "$PNPM_VERSION" ]]; then
            log "Installing project-local pnpm $PNPM_VERSION"
            mkdir -p "$TOOLCHAIN"
            npm install --prefix "$TOOLCHAIN" --no-save --package-lock=false --ignore-scripts --no-audit --no-fund "pnpm@$PNPM_VERSION"
        fi
        [[ $(cd "$ROOT"; "$PNPM" --version) == "$PNPM_VERSION" ]] || die 'Incorrect local pnpm version.'
        log 'Installing Vencord frozen dependencies'
        (cd "$CHECKOUT"; "$PNPM" install --frozen-lockfile)
        check_source || die 'Upstream checkout changed during dependency installation.'
        fingerprint > "$WORK/dependencies.ready"
        check_ready || die 'Frontend environment verification failed.'
        stage_plugin
        log 'Frontend development environment ready';;
    --typecheck|--lint)
        check_ready || die 'Frontend prerequisites incomplete; run ./setup.sh --frontend.'
        stage_plugin
        cd "$CHECKOUT"
        # Invoke installed binaries directly; validation never invokes a package manager.
        if [[ "$mode" == --typecheck ]]; then
            node_modules/.bin/tsc --noEmit
        else
            lint_plugin
        fi;;
esac
