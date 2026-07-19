#!/usr/bin/env sh
set -u

mode="auto"
target="."
failures=0

usage() {
  printf '%s\n' "Usage: bash install/preflight.sh [--mode auto|pipx|editable|source|installed] [--target PATH_OR_WHEEL]"
}

fail() {
  failures=$((failures + 1))
  printf '[FAIL] %s\n' "$1"
  printf 'NEXT: %s\n' "$2"
}

pass() {
  printf '[PASS] %s\n' "$1"
}

warn() {
  printf '[WARN] %s\n' "$1"
  printf 'NEXT: %s\n' "$2"
}

path_contains() {
  case ":${PATH:-}:" in
    *":$1:"*) return 0 ;;
    *) return 1 ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      mode="$2"
      shift 2
      ;;
    --target)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      target="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$mode" in
  auto|pipx|editable|source|installed) ;;
  *) printf 'Unsupported mode: %s\n' "$mode" >&2; usage >&2; exit 2 ;;
esac

if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 is missing." "Install Python 3.8 or newer. To bootstrap current pipx, install Python 3.10 or newer."
  exit 1
fi

python_version=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || printf 'unknown')
python_supported=$(python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; printf '%s' "$?")
if [ "$python_supported" -ne 0 ]; then
  fail "Python $python_version is unsupported; JSONL requires Python 3.8+." "Install Python 3.8 or newer and rerun this preflight."
  exit 1
fi
pass "Python $python_version satisfies JSONL's Python 3.8+ requirement."

if [ "$mode" = "auto" ]; then
  if [ -f "$target/src/jsonl_history_search/cli.py" ]; then
    mode="source"
  elif command -v jsonl-history-search >/dev/null 2>&1 || command -v convo >/dev/null 2>&1; then
    mode="installed"
  else
    mode="pipx"
  fi
  printf '[INFO] Auto-selected mode: %s\n' "$mode"
fi

if [ "$mode" = "source" ]; then
  if [ -f "$target/src/jsonl_history_search/cli.py" ]; then
    pass "Source mode is available at $target; console wrappers are optional in this mode."
    printf 'RUN: cd "%s" && PYTHONPATH=src python3 -m jsonl_history_search.cli validate-install --format text\n' "$target"
  else
    fail "Source mode was requested, but $target is not a JSONL source checkout." "Pass --target /path/to/JSON-search-history."
  fi
  [ "$failures" -eq 0 ]
  exit $?
fi

if [ "$mode" = "editable" ]; then
  if [ ! -f "$target/pyproject.toml" ] || [ ! -d "$target/src/jsonl_history_search" ]; then
    fail "Editable mode requires a JSONL source checkout; $target is not one." "Clone or obtain the checkout, then pass --target /path/to/JSON-search-history."
  fi
  if python3 -m pip --version >/dev/null 2>&1; then
    pass "python3 -m pip is available for the contributor editable install."
  else
    fail "python3 -m pip is missing." "Install pip for this Python (for Debian/Ubuntu: sudo apt install python3-pip), then rerun the preflight."
  fi
  if [ "$failures" -eq 0 ]; then
    printf 'RUN: python3 -m pip install -e "%s"\n' "$target"
  fi
  [ "$failures" -eq 0 ]
  exit $?
fi

if [ "$mode" = "pipx" ]; then
  if ! python3 -m pip --version >/dev/null 2>&1; then
    warn "python3 -m pip is missing; Python-based pipx bootstrap is unavailable." "Prefer your OS package manager for pipx, or install pip for this Python first."
  else
    pass "python3 -m pip is available."
  fi
  if ! command -v pipx >/dev/null 2>&1; then
    python_for_pipx=$(python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; printf '%s' "$?")
    if [ "$python_for_pipx" -eq 0 ]; then
      fail "pipx is missing." "Install pipx with your OS package manager or run: python3 -m pip install --user pipx"
    else
      fail "pipx is missing, and Python $python_version is too old to bootstrap current pipx (Python 3.10+)." "Install a newer Python for pipx, or use --mode editable/source with JSONL's supported Python 3.8+."
    fi
  else
    pass "pipx is available."
    pipx_bin=$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)
    if [ -n "$pipx_bin" ]; then
      if path_contains "$pipx_bin"; then
        pass "pipx app directory is on PATH: $pipx_bin"
      else
        fail "pipx app directory is not on PATH: $pipx_bin" "Run: pipx ensurepath; then open a new shell and rerun this preflight."
      fi
    else
      warn "pipx did not report PIPX_BIN_DIR." "Run: pipx ensurepath; open a new shell; then verify with pipx environment --value PIPX_BIN_DIR."
    fi
  fi
  if [ "$failures" -eq 0 ]; then
    printf 'RUN: pipx install "%s"\n' "$target"
  fi
  [ "$failures" -eq 0 ]
  exit $?
fi

missing_wrappers=""
for wrapper in jsonl-history-search convo; do
  if command -v "$wrapper" >/dev/null 2>&1; then
    pass "$wrapper is on PATH."
  else
    missing_wrappers="$missing_wrappers $wrapper"
  fi
done
if [ -n "$missing_wrappers" ]; then
  if command -v pipx >/dev/null 2>&1 && pipx list --short 2>/dev/null | grep -q 'jsonl-history-search'; then
    fail "The Python package is registered with pipx, but wrappers are missing from PATH:$missing_wrappers" "Run: pipx reinstall jsonl-history-search && pipx ensurepath; then open a new shell."
  elif python3 -c 'import jsonl_history_search' >/dev/null 2>&1; then
    fail "The Python package imports, but wrappers are missing from PATH:$missing_wrappers" "Reinstall from the supplied checkout/wheel, then add its scripts directory to PATH."
  else
    fail "JSONL is not installed and wrappers are missing from PATH:$missing_wrappers" "Run the pipx preflight with --mode pipx --target /path/to/checkout-or-wheel, then follow its RUN command."
  fi
fi
[ "$failures" -eq 0 ]
