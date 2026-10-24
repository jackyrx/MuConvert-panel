#!/usr/bin/env bash
# Simple launcher for MuConvert Panel (macOS / Linux).
# Keeps the caller's working directory so relative input/output paths behave normally.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUTF8=1

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON="$candidate"
      break
    fi
  done
fi

if [ -z "$PYTHON" ]; then
  echo "Python 3.10+ not found. Install Python and try again." >&2
  exit 1
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
  echo "Python 3.10+ required (found $("$PYTHON" -V 2>&1))." >&2
  exit 1
fi

skip_binary_check=0
for arg in "$@"; do
  case "$arg" in
    -h|--help|--version|--build|--build-all) skip_binary_check=1 ;;
  esac
done

if [ "$skip_binary_check" -eq 0 ]; then
  if ! "$PYTHON" -c 'import muconvert; muconvert.resolve_bin()' >/dev/null 2>&1; then
    cat >&2 <<'EOF'
MuConvert binary not found for this platform.

Options:
  1) Download a Release wheel and install it:
       pip install muconvert_panel-*.whl
  2) Build the host binary (requires .NET 10 SDK, git, Java):
       python3 -m muconvert --build
  3) Set MUCONVERT_BIN to an existing MuConvert binary.

For source-only clones, use option 1 or 2 first.
EOF
    exit 1
  fi
fi

exec "$PYTHON" -m muconvert "$@"
