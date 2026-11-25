#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR"
python3 - "$ROOT" "$@" <<'PY'
from __future__ import annotations

import collections
import datetime as dt
import os
from pathlib import Path
import subprocess
import sys
import platform
import tomllib

ROOT = Path(sys.argv[1]).resolve()
ARGS = set(sys.argv[2:])
if ARGS & {"--help", "-h"}:
    print("Usage: ./stats.sh [--files] [--heatmap-only] [--no-color]\nReports working-tree text statistics and committed Git activity. No dependencies installed.")
    raise SystemExit(0)
VALID_ARGS = {"--files", "--heatmap-only", "--no-color"}
if ARGS - VALID_ARGS:
    raise SystemExit(f"Unknown option(s): {', '.join(sorted(ARGS - VALID_ARGS))}. Available: {', '.join(sorted(VALID_ARGS))}")
SHOW_FILES = "--files" in ARGS
HEATMAP_ONLY = "--heatmap-only" in ARGS
COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ and "--no-color" not in ARGS
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".basedpyright", ".tox", ".nox", ".cache", "build", "dist", "coverage", "htmlcov", ".next", ".parcel-cache", "output"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".so", ".o", ".a", ".dll", ".dylib", ".class", ".whl", ".zip", ".gz", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".pdf", ".sqlite", ".sqlite3", ".db", ".lockb"}
SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".html", ".jinja", ".jinja2", ".sh", ".bash", ".c", ".h", ".rs"}
LANGUAGES = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript JSX", ".ts": "TypeScript", ".tsx": "TypeScript JSX", ".css": "CSS", ".scss": "SCSS", ".html": "HTML/templates", ".jinja": "Jinja templates", ".jinja2": "Jinja templates", ".sh": "Shell", ".bash": "Shell", ".c": "C", ".h": "C headers", ".rs": "Rust"}


def git(*args: str) -> bytes | None:
    try:
        return subprocess.check_output(["git", "--no-optional-locks", "-C", str(ROOT), *args], stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return None


def readable(path: Path) -> tuple[int, int, int] | None:
    try:
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            return None
        text = data.decode("utf-8")
    except (OSError, UnicodeError):
        return None
    return len(text.splitlines()), len(text), len(data)


def generated(rel: str) -> bool:
    p = Path(rel)
    return (p.name == "_version.py" or p.name.endswith(".min.js") or p.name.endswith(".min.css")
            or p.name in {"uv.lock", "pnpm-lock.yaml", "package-lock.json", "yarn.lock"} or rel.endswith(".map"))


def is_test(rel: str) -> bool:
    p = Path(rel)
    return "tests" in p.parts or p.name.startswith("test_") or p.name.endswith("_test.py")


def is_distribution_source(rel: str) -> bool:
    p = Path(rel)
    if generated(rel) or is_test(rel) or p.suffix.lower() not in SOURCE_SUFFIXES:
        return False
    return (rel.startswith("src/vencoverlay/") or rel.startswith("plugin/") or rel.startswith("frontend/src/")
            or rel.startswith("packaging/") or rel.startswith("frontend/scripts/"))


def fmt(n: int) -> str:
    return f"{n:,}"


def section(title: str) -> None:
    print(f"\n{title}\n{'─' * len(title)}")


files: list[tuple[str, int, int, int]] = []
dirs: set[str] = set()
skipped_binary = 0
for base, folder_names, names in os.walk(ROOT, followlinks=False):
    base_path = Path(base)
    relative_base = base_path.relative_to(ROOT)
    folder_names[:] = sorted(
        d for d in folder_names
        if d not in SKIP_DIRS and not d.endswith(".egg-info")
        and not (base_path / d).is_symlink()
    )
    for d in folder_names:
        dirs.add((relative_base / d).as_posix())
    for name in sorted(names):
        path = base_path / name
        if name == ".coverage":
            continue
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if path.suffix.lower() in SKIP_SUFFIXES:
            skipped_binary += 1
            continue
        counts = readable(path)
        if counts is None:
            skipped_binary += 1
            continue
        files.append((rel, *counts))

production = [f for f in files if is_distribution_source(f[0])]
tests = [f for f in files if is_test(f[0]) and Path(f[0]).suffix.lower() in SOURCE_SUFFIXES and not generated(f[0])]
generated_files = [f for f in files if generated(f[0])]
source_all = [f for f in files if Path(f[0]).suffix.lower() in SOURCE_SUFFIXES and not generated(f[0])]


def totals(rows: list[tuple[str, int, int, int]]) -> tuple[int, int, int, int]:
    return len(rows), sum(r[1] for r in rows), sum(r[2] for r in rows), sum(r[3] for r in rows)


def show_totals(label: str, rows: list[tuple[str, int, int, int]]) -> None:
    n, lines, chars, bytes_ = totals(rows)
    print(f"{label:<30} {fmt(n):>8} {fmt(lines):>12} {fmt(chars):>14} {fmt(bytes_):>14}")


if not HEATMAP_ONLY:
    print(f"VencOverlay PROJECT STATISTICS\nRepository: {ROOT}")
    with (ROOT / "pyproject.toml").open("rb") as stream:
        metadata = tomllib.load(stream)["project"]
    print(f"Version: {metadata['version']}")
    print(f"System: {platform.system()} {platform.machine()} | Python (system): {platform.python_version()}")
    print(f"Desktop: {os.environ.get('XDG_CURRENT_DESKTOP', 'unknown')} | Session: {os.environ.get('XDG_SESSION_TYPE', 'unknown')}")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    revision = git("rev-parse", "--short", "HEAD")
    print(f"Git branch: {branch.decode().strip() if branch else 'unavailable'} | Revision: {revision.decode().strip() if revision else 'unavailable'}")
    status = git("status", "--porcelain=v1", "-z")
    print("Working tree: " + ("unavailable" if status is None else "modified" if status else "clean"))
if not HEATMAP_ONLY:
    print("Scope: current working tree; UTF-8 text files only; character counts are Unicode code points")
    print("Excluded: .git, caches, environments, generated .egg-info, node_modules, build/dist and binary files")
    section("1. SHAREABLE SUMMARY")
    print(f"Handwritten distribution source: {fmt(totals(production)[0])} files, {fmt(totals(production)[1])} lines, {fmt(totals(production)[2])} characters")
    print(f"Tests:                           {fmt(totals(tests)[0])} files, {fmt(totals(tests)[1])} lines")
    print(f"Project text files:              {fmt(totals(files)[0])} files, {fmt(totals(files)[1])} lines")
    print(f"Project directories:             {fmt(len(dirs))}")
    section("2. WORKING TREE BREAKDOWN")
    print(f"{'Category':<30} {'Files':>8} {'Lines':>12} {'Characters':>14} {'UTF-8 bytes':>14}")
    show_totals("Distribution source", production)
    show_totals("  Python application", [f for f in production if f[0].startswith("src/vencoverlay/") and f[0].endswith(".py")])
    show_totals("  Vencord plugin", [f for f in production if f[0].startswith("plugin/")])
    show_totals("Test source", tests)
    show_totals("Other handwritten source", [f for f in source_all if f not in production and f not in tests])
    show_totals("Generated text assets", generated_files)
    show_totals("All eligible text files", files)
    print(f"Binary/unreadable files skipped: {fmt(skipped_binary)}")
    section("3. DISTRIBUTION SOURCE BY LANGUAGE")
    by_language: dict[str, list[tuple[str, int, int, int]]] = collections.defaultdict(list)
    for f in production:
        by_language[LANGUAGES.get(Path(f[0]).suffix.lower(), "Other")].append(f)
    print(f"{'Language':<30} {'Files':>8} {'Lines':>12} {'Characters':>14} {'UTF-8 bytes':>14}")
    for lang, group in sorted(by_language.items(), key=lambda item: -totals(item[1])[1]):
        show_totals(lang, group)
    section("4. FILE TYPES (ENTIRE PROJECT)")
    by_type: dict[str, list[tuple[str, int, int, int]]] = collections.defaultdict(list)
    for f in files:
        by_type[Path(f[0]).suffix.lower() or "[no extension]"].append(f)
    print(f"{'Extension':<20} {'Files':>8} {'Lines':>12} {'Characters':>14}")
    for ext, group in sorted(by_type.items(), key=lambda item: -totals(item[1])[1]):
        n, lines, chars, _ = totals(group)
        print(f"{ext:<20} {n:>8,} {lines:>12,} {chars:>14,}")
    section("5. TOP-LEVEL PROJECT AREAS")
    by_area: dict[str, list[tuple[str, int, int, int]]] = collections.defaultdict(list)
    for f in files:
        by_area[f[0].split("/", 1)[0] if "/" in f[0] else "[repository root]"].append(f)
    print(f"{'Area':<30} {'Files':>8} {'Lines':>12} {'Characters':>14} {'UTF-8 bytes':>14}")
    for area, group in sorted(by_area.items(), key=lambda item: -totals(item[1])[1]):
        show_totals(area, group)
    section("6. LARGEST HANDWRITTEN SOURCE FILES")
    print(f"{'Lines':>8} {'Characters':>12}  File")
    for rel, lines, chars, _ in sorted(production, key=lambda f: (-f[1], f[0]))[:15]:
        print(f"{lines:>8,} {chars:>12,}  {rel}")
    if SHOW_FILES:
        section("7. ALL ELIGIBLE TEXT FILES")
        print(f"{'Lines':>8} {'Characters':>12} {'UTF-8 bytes':>12}  File")
        for rel, lines, chars, bytes_ in sorted(files):
            print(f"{lines:>8,} {chars:>12,} {bytes_:>12,}  {rel}")
    section("8. SOURCE / TEST RELATIONSHIP")
    prod_lines = totals(production)[1]
    test_lines = totals(tests)[1]
    print(f"Test lines per distribution-source line: {test_lines / prod_lines:.2f}" if prod_lines else "Not available")
    print(f"Average distribution-source file: {prod_lines / len(production):,.1f} lines" if production else "Not available")
    section("9. GIT HISTORY")
    commit_count = git("rev-list", "--count", "HEAD")
    if commit_count is None:
        print("No accessible Git history.")
    else:
        print(f"Commits (including merges): {fmt(int(commit_count.strip()))}")
        authors = git("shortlog", "-s", "-n", "HEAD")
        if authors:
            print(f"Contributors (author identities): {len(authors.splitlines())}")
        first = git("log", "--reverse", "--format=%aI", "HEAD")
        last = git("log", "-1", "--format=%aI", "HEAD")
        if first and last:
            first_date = dt.datetime.fromisoformat(first.splitlines()[0].decode())
            last_date = dt.datetime.fromisoformat(last.strip().decode())
            print(f"First commit: {first_date.date()} | Latest commit: {last_date.date()} | Span: {(last_date - first_date).days:,} days")
        # --no-merges avoids counting combined merge diffs as new development work.
        log = git("log", "--no-merges", "--format=COMMIT:%H", "--numstat", "--find-renames", "HEAD")
        if log is not None:
            added = deleted = replacements = changesets = 0
            for raw in log.decode("utf-8", errors="replace").splitlines():
                if raw.startswith("COMMIT:"):
                    changesets += 1
                    continue
                fields = raw.split("\t", 2)
                if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit():
                    continue
                a, d = int(fields[0]), int(fields[1])
                added += a
                deleted += d
                replacements += min(a, d)
            print(f"Non-merge commits analysed: {fmt(changesets)}")
            print(f"Lines added (Git numstat): {fmt(added)}")
            print(f"Lines deleted (Git numstat): {fmt(deleted)}")
            print(f"Estimated replaced lines: {fmt(replacements)} (sum of min(additions, deletions) per file per commit)")
            print(f"Estimated pure additions: {fmt(added - replacements)}")
            print(f"Estimated pure deletions: {fmt(deleted - replacements)}")
            print(f"Total line churn: {fmt(added + deleted)} (includes generated files and historical tests)")
        monthly = git("log", "--no-merges", "--format=%as", "HEAD")
        if monthly:
            by_month = collections.Counter(line.decode()[:7] for line in monthly.splitlines())
            print("Commits by month (latest 12):")
            for month, count in sorted(by_month.items(), reverse=True)[:12]:
                print(f"  {month}: {fmt(count)}")
        print("Git metrics cover committed history only, not uncommitted changes.")

# Read commit metadata and per-file line deltas once. Committer dates are used
# consistently for the calendar and monthly timeline; merge commits excluded.
raw_history = git("log", "--no-merges", "--format=STATS_COMMIT:%cI", "--numstat", "--find-renames", "HEAD")
if raw_history is not None:
    daily_commits: collections.Counter[dt.date] = collections.Counter()
    daily_added: collections.Counter[dt.date] = collections.Counter()
    daily_deleted: collections.Counter[dt.date] = collections.Counter()
    current_date: dt.date | None = None
    for line in raw_history.decode("utf-8", errors="replace").splitlines():
        if line.startswith("STATS_COMMIT:"):
            try:
                current_date = dt.datetime.fromisoformat(line.split(":", 1)[1]).astimezone(dt.timezone.utc).date()
            except ValueError:
                current_date = None
            if current_date is not None:
                daily_commits[current_date] += 1
            continue
        fields = line.split("\t", 2)
        if current_date is not None and len(fields) == 3 and fields[0].isdigit() and fields[1].isdigit():
            daily_added[current_date] += int(fields[0])
            daily_deleted[current_date] += int(fields[1])

    section("10. DEVELOPMENT ACTIVITY HEATMAP (GIT)")
    print("First to latest commit · UTC committer dates · non-merge commits · committed changes only")
    print("Commits = commit count; Additions = lines added (including tests and generated assets)")
    # Use the entire repository history for the time bounds, including merges.
    # This ensures the calendar begins at the first commit and ends at the latest
    # commit even when either endpoint is a merge commit.
    all_commit_dates = git("log", "--format=%cI", "HEAD")
    if all_commit_dates:
        history_dates = [
            dt.datetime.fromisoformat(value.decode()).astimezone(dt.timezone.utc).date()
            for value in all_commit_dates.splitlines()
        ]
        first_day, last_day = min(history_dates), max(history_dates)
    else:
        first_day = last_day = dt.datetime.now(dt.timezone.utc).date()
    import calendar
    import shutil
    import re

    shades = ("·", "░", "▒", "▓", "█")
    ansi = ("\033[38;5;240m", "\033[38;5;22m", "\033[38;5;28m", "\033[38;5;34m", "\033[38;5;46m")
    month_count = (last_day.year - first_day.year) * 12 + last_day.month - first_day.month + 1
    print(f"Period: {first_day.isoformat()} to {last_day.isoformat()} ({month_count:,} calendar {'month' if month_count == 1 else 'months'})")

    def render_heatmap(title: str, values: collections.Counter[dt.date]) -> None:
        maximum = max((n for day, n in values.items() if first_day <= day <= last_day), default=0)

        def intensity(n: int) -> int:
            if not n or not maximum:
                return 0
            return min(4, 1 + (n * 4 - 1) // maximum)

        def cell(day: dt.date) -> str:
            if day < first_day or day > last_day:
                return "  "
            idx = intensity(values[day])
            glyph = shades[idx] * 2
            return f"{ansi[idx]}{glyph}\033[0m" if COLOR else glyph

        # Each calendar month has a fixed 7-column grid; months wrap to
        # additional rows rather than compressing days as history grows.
        panel_width = 22
        columns = max(1, min(3, shutil.get_terminal_size((80, 24)).columns // (panel_width + 2)))
        panels: list[list[str]] = []
        year, month = first_day.year, first_day.month
        while (year, month) <= (last_day.year, last_day.month):
            first_weekday, days_in_month = calendar.monthrange(year, month)
            lines = [f"{calendar.month_name[month]} {year}", "Mo Tu We Th Fr Sa Su"]
            for week in range(6):
                slots = []
                for weekday in range(7):
                    day_number = week * 7 + weekday - first_weekday + 1
                    if 1 <= day_number <= days_in_month:
                        slots.append(cell(dt.date(year, month, day_number)))
                    else:
                        slots.append("  ")
                lines.append(" ".join(slots))
            panels.append(lines)
            month += 1
            if month == 13:
                year += 1
                month = 1

        print(f"\n{title} (peak {maximum:,}/day; {columns} month(s) per row)")
        for offset in range(0, len(panels), columns):
            group = panels[offset:offset + columns]
            for row in range(8):
                print("  ".join(line + " " * max(0, panel_width - len(re.sub(r"\x1b\[[0-9;]*m", "", line))) for panel in group for line in [panel[row]]).rstrip())
            print()
        legend = " ".join(f"{ansi[i]}{shades[i] * 2}\033[0m" if COLOR else shades[i] * 2 for i in range(5))
        print(f"Less {legend} More")

    render_heatmap("COMMITS", daily_commits)
    render_heatmap("LINES ADDED", daily_added)

    active_days = sorted(day for day, n in daily_commits.items() if n)
    streak = best_streak = 0
    previous: dt.date | None = None
    for day in active_days:
        streak = streak + 1 if previous is not None and day - previous == dt.timedelta(days=1) else 1
        best_streak = max(best_streak, streak)
        previous = day
    print(f"\nActive commit days (all history): {len(active_days):,} | Longest daily streak: {best_streak:,} {'day' if best_streak == 1 else 'days'}")
    if daily_commits:
        busiest = max(daily_commits, key=lambda d: (daily_commits[d], -d.toordinal()))
        print(f"Busiest commit day: {busiest} ({daily_commits[busiest]:,} commits)")
    if daily_added:
        largest = max(daily_added, key=lambda d: (daily_added[d], -d.toordinal()))
        print(f"Most lines added in a day: {largest} ({daily_added[largest]:,} lines)")
    print("\nMonthly code churn (latest 12 calendar months with activity):")
    months = sorted({d.strftime("%Y-%m") for d in daily_commits}, reverse=True)[:12]
    if not months:
        print("  No commits found.")
    for month in reversed(months):
        c = sum(n for d, n in daily_commits.items() if d.strftime("%Y-%m") == month)
        a = sum(n for d, n in daily_added.items() if d.strftime("%Y-%m") == month)
        d = sum(n for date, n in daily_deleted.items() if date.strftime("%Y-%m") == month)
        print(f"  {month}  {c:>5,} commits   +{a:>9,} / -{d:>9,} lines   net {a-d:+,}")
else:
    section("10. DEVELOPMENT ACTIVITY HEATMAP (GIT)")
    print("No accessible Git history.")

if not HEATMAP_ONLY:
    print("\nUse --files to show a per-file inventory of all eligible text files.")
    print("Notes: Distribution source is a source-tree estimate, not a measurement of Nuitka's compiled output.")
    print("Git replacement counts are estimates; Git does not record an independent 'changed lines' metric.")
PY
