# Release Notes

## v0.6.0b1 / ver0.6b

Date: 2026-10-25

### Launchers

- New `run-mac.sh` for macOS:
  - clears `com.apple.quarantine` recursively
  - re-signs `osx-arm64` / `osx-x64` binaries ad-hoc
  - delegates to `run.sh`
- New `run-win.bat` Windows alias for `run.bat`.
- `run.sh` and `run.bat` remain available as generic launchers.

### macOS Gatekeeper

Downloaded zips may be blocked by Gatekeeper. `run-mac.sh` handles this
automatically. Manual fallback:

```bash
xattr -dr com.apple.quarantine .
find muconvert/vendor/muconvert -name MuConvert -exec codesign --force --sign - {} \;
```

If still blocked: System Settings → Privacy & Security → Security →
`MuConvert` → Open Anyway.

### Assets

- `MuGame-ver0.6b.zip`
- `MuGame-ver0.6b-full.zip`
- `muconvert_panel-0.6.0b1-py3-none-any.whl`
- `muconvert_panel-0.6.0b1-py3-none-macosx_11_0_arm64.whl`
- `muconvert_panel-0.6.0b1-py3-none-macosx_10_15_x86_64.whl`
- `muconvert_panel-0.6.0b1-py3-none-win_amd64.whl`
- `muconvert_panel-0.6.0b1-py3-none-manylinux2014_x86_64.whl`
- `SHA256SUMS-ver0.6b.txt`

## v0.5.0b1 / ver0.5b

- Added `run.sh` / `run.bat` launchers.
- Kept source-only repo + GitHub Release asset layout.
- macOS binary remains ad-hoc signed; manual `xattr` may be required.
