#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"
source scripts/prerequisites.sh
usage() {
    cat <<'HELP'
Usage: ./validate.sh [--coverage] [--graphical]
  Default      Validate maintained Python code and shell scripts.
  --coverage   Report Python test coverage (no minimum yet).
  --graphical  Permit graphical tests (requires a suitable desktop).
  --help       Show help.
No frontend validation is implemented yet. No dependencies are installed.
HELP
}
coverage=false
graphical=false
for arg in "$@"; do
    case "$arg" in
        --coverage) coverage=true;;
        --graphical) graphical=true;;
        -h|--help) usage; exit 0;;
        *) die "Unknown argument: $arg (use --help)";;
    esac
done
[[ -z ${VIRTUAL_ENV:-} ]] || die 'Deactivate the virtual environment first.'
command -v uv >/dev/null || die 'uv unavailable; run ./setup.sh first.'
[[ -x .venv/bin/python ]] || die 'Project environment missing; run ./setup.sh first.'
export UV_PROJECT_ENVIRONMENT="$PROJECT_ROOT/.venv"
export UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never
log 'Checking development prerequisites'
check_system || die 'System prerequisites incomplete; run ./setup.sh.'
check_environment || die 'Environment needs repair; run ./setup.sh.'
log 'Checking project version'
./version.sh check || die 'Project version or lockfile check failed.'
passed=0
failed=0
run_check() {
    local label=$1
    shift
    log "$label"
    if "$@"; then
        printf 'PASS: %s\n' "$label"
        passed=$((passed + 1))
    else
        printf 'FAIL: %s\n' "$label" >&2
        failed=$((failed + 1))
    fi
}
check_shell() {
    local file result=0
    for file in ./*.sh scripts/*.sh; do
        [[ -f "$file" ]] || continue
        [[ "$file" != ./wasd-*.sh ]] || continue
        bash -n "$file" || result=1
    done
    return "$result"
}
paths=(tests)
[[ ! -d src ]] || paths+=(src)
runner=(uv run --locked --no-sync --offline)
pytest_args=(--tb=short -q)
if [[ $graphical == true ]]; then pytest_args+=(--graphical); fi
if [[ $coverage == true ]]; then
    if [[ -d src ]]; then pytest_args+=(--cov=src --cov-report=term-missing); else
        pytest_args+=(--cov=tests --cov-report=term-missing)
        warn 'Coverage measures test infrastructure only; application package not yet present.'
    fi
fi
run_check 'Shell syntax' check_shell
run_check 'Ruff linting' "${runner[@]}" ruff check "${paths[@]}"
run_check 'Ruff formatting' "${runner[@]}" ruff format --check "${paths[@]}"
run_check 'Strict Basedpyright' "${runner[@]}" basedpyright "${paths[@]}"
run_check 'Pytest' "${runner[@]}" pytest "${pytest_args[@]}"
log 'Validation summary'
printf 'Passed: %s\nFailed: %s\n' "$passed" "$failed"
printf 'Frontend: NOT VALIDATED (tooling pending)\n'
if ((failed)); then exit 1; fi
printf 'All selected validation checks passed.\n'
