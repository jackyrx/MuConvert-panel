#!/usr/bin/env bash
# macOS launcher.
# Removes Gatekeeper quarantine and re-signs bundled MuConvert binaries ad-hoc,
# then delegates to the generic run.sh.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "run-mac.sh is for macOS. Use ./run.sh on Linux." >&2
  exit 1
fi

# Downloaded zips inherit com.apple.quarantine; clear it recursively.
xattr -dr com.apple.quarantine "$SCRIPT_DIR" 2>/dev/null || true

# Re-sign both macOS binaries ad-hoc; harmless if already signed.
for bin in \
  "$SCRIPT_DIR/muconvert/vendor/muconvert/osx-arm64/MuConvert" \
  "$SCRIPT_DIR/muconvert/vendor/muconvert/osx-x64/MuConvert"
do
  if [ -f "$bin" ]; then
    chmod +x "$bin" 2>/dev/null || true
    codesign --force --sign - "$bin" >/dev/null 2>&1 || true
  fi
done

exec "$SCRIPT_DIR/run.sh" "$@"
