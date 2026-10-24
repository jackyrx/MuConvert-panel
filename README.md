# MuConvert Panel v0.6.0b1

Pre-built wrapper + local/LAN web panel for
[MuConvert](https://github.com/MuNET-OSS/MuConvert).

版本：`0.6.0b1`（archive label: `ver0.6b`）  
Release notes：[RELEASE.md](RELEASE.md)

支援：

- maimai：Simai (`maidata.txt`) ↔ MA2
- CHUNITHM：C2S ↔ UGC，另有 experimental SUS
- Ongeki：OGKR 只能透過 upstream C# library 使用

## 特色

- 四平台 pre-built self-contained binary：`osx-arm64` / `osx-x64` / `win-x64` / `linux-x64`
- 使用者不需要安裝 `.NET SDK` / `.NET Runtime`
- `pip install` 後直接有 `muconvert` 指令
- Local / LAN web panel，手機 / 平板可用 browser 操作
- Drag & drop 上傳、package companion files、Normal → Lax 自動 fallback

## Install

Universal wheel（本機 / 私人 index；檔案較大）：

```bash
pip install dist/muconvert_panel-0.6.0b1-py3-none-any.whl
muconvert --version
muconvert --ui
```

Per-platform wheels（PyPI 友善，每個檔案 < 100 MB）：

```bash
python3 scripts/build_wheels.py --all
pip install dist/muconvert_panel-0.6.0b1-py3-none-macosx_11_0_arm64.whl
```

Universal wheel 約 124 MB，超過 PyPI 單檔 100 MB 限制；上 PyPI 請用 per-platform wheels。

## Source-only repo

Repo 的 `.gitignore` 是通用 Python / macOS 模板；release 不包含 binary、upstream
source 與 sample media。

若要 local-only 排除、不要 commit，請在 `git init` 後寫入 `.git/info/exclude`
（此檔不會進 repo）：

```bash
git init
cat >> .git/info/exclude <<'EOF'
/muconvert/vendor/muconvert/
/vendor/MuConvert-src/
/solips/
/output.ma2
/ground_truth/
/out2/
EOF
```

`dist/` 已被通用 `.gitignore` 排除。

Clone 後二選一：

```bash
# 1) 下載 GitHub Release 的 platform wheel 並安裝
uv venv /tmp/mc
uv pip install --python /tmp/mc/bin/python muconvert_panel-0.6.0b1-*.whl
/tmp/mc/bin/muconvert --version

# 2) 自行 build 四平台 binary
python3 -m muconvert --build-all
python3 -m muconvert --version
```

## CLI

```bash
# Simai -> MA2（單一難度）
python3 -m muconvert chart/maidata.txt -o out.ma2

# 多難度 -> 輸出資料夾
python3 -m muconvert chart/maidata.txt -o out/

# 轉換並複製來源已有的 companion files
python3 -m muconvert chart/maidata.txt -l 1 --lax --package -o out_pkg

# 0.2a 相容 launcher
python3 run.py --version
```

只有一個難度時 `-o` 可直接指定檔案；多難度時 `-o` 必須是資料夾。

## Run scripts

macOS（建議）：

```bash
chmod +x run-mac.sh
./run-mac.sh --version
./run-mac.sh --ui
./run-mac.sh chart/maidata.txt -o out.ma2
```

`run-mac.sh` 會在啟動前自動：

- `xattr -dr com.apple.quarantine`
- 對 `osx-arm64` / `osx-x64` binary 重新 ad-hoc codesign

Linux：

```bash
chmod +x run.sh
./run.sh --version
./run.sh --ui
```

Windows：

```bat
run-win.bat --version
run-win.bat --ui
run-win.bat chart\maidata.txt -o out.ma2
```

`run.bat` 仍保留為 generic Windows launcher；`run-win.bat` 是 alias。

所有 script 都會：

- 保留目前 working directory（相對 input/output 正常）
- 檢查 Python 3.10+
- 找不到 platform binary 時提示下載 Release wheel 或執行 `--build`

## macOS Gatekeeper

從瀏覽器下載的 zip 會帶 `com.apple.quarantine`；macOS 可能顯示：

```text
“MuConvert” Not Opened
Apple could not verify “MuConvert” is free of malware...
```

建議直接使用 `run-mac.sh`，它會自動清 quarantine 並重新 ad-hoc codesign。

手動處理：

```bash
cd MuGame-ver0.6b-full
xattr -dr com.apple.quarantine .
find muconvert/vendor/muconvert -name MuConvert -exec codesign --force --sign - {} \;
./run.sh --version
```

若仍被阻擋：按 **Done** → System Settings → Privacy & Security → Security →
`MuConvert` → **Open Anyway**。

Windows 沒有這個 Gatekeeper 機制；SmartScreen 只會顯示一般警告。

## Web UI / LAN

```bash
# 本機
python3 -m muconvert --ui
# http://127.0.0.1:8765/

# LAN（同網段手機 / 電腦）
python3 -m muconvert --ui --host 0.0.0.0
```

啟動時會印出 `LAN: http://192.168.x.x:8765/`。

- 預設 Host allowlist：localhost、IP literal、`*.local`
- 自訂 hostname：`--allow-host mymac.lan`
- 完全信任網路才用：`--allow-any-host`
- LAN client 不能開伺服器端檔案 picker，也不能叫伺服器開 Finder / Explorer /
  xdg-open；請用 drag & drop + 下載
- 沒有 TLS；token 嵌在頁面內，只防 CSRF

## Supported outputs

| 輸入 | 預設輸出 | 備註 |
|---|---|---|
| Simai (`maidata.txt` / `.txt`) | `.ma2` | `lv_N.ma2` |
| MA2 | `maidata.txt` | 多檔會合併 |
| C2S | `.ugc` | C2S → SUS 需 `-t sus` |
| UGC / SUS | `.c2s` | SUS experimental |
| OGKR | library only | 無 CLI |

## Companion files

`--package` / UI checkbox 只會**複製**來源資料夾已有的檔案：

- `Music.xml`、`.xml`
- `.dat`
- `.acb` / `.awb`
- 封面、音訊、影片

它**不會生成**這些檔案：

- `Music.xml`：maimai metadata，請用
  [MaiChartManager](https://github.com/MuNET-OSS/MaiChartManager)
- `.acb` / `.awb`：CRIWARE ADX2，請用 MCM / CRI Atom Craft
- `.dat`：MuConvert / MCM 都沒有 parser/generator；需要 sample 才能判斷

## Build

```bash
python3 -m muconvert --build       # host only
python3 -m muconvert --build-all   # 四個平台
```

需要 `.NET 10 SDK`、`git`、`Java`。

參數：

```text
-p:SelfContained=true
-p:UseAppHost=true
-p:PublishSingleFile=true
-p:EnableCompressionInSingleFile=true
```

輸出：

```text
muconvert/vendor/muconvert/osx-arm64/MuConvert
muconvert/vendor/muconvert/osx-x64/MuConvert
muconvert/vendor/muconvert/win-x64/MuConvert.exe
muconvert/vendor/muconvert/linux-x64/MuConvert
```

macOS binary 會做 ad-hoc codesign，避免 `SIGKILL (Killed: 9)`。

## Cache / env

```text
MUCONVERT_CACHE_DIR   # default ~/.cache/muconvert
├── bin/<rid>/         # build / chmod fallback
└── uploads/           # web panel drag & drop
```

- `MUCONVERT_BIN` 可指定 binary
- `UPLOAD_ROOT` 不會寫進 `muconvert/vendor/`
- 若 `site-packages` 唯讀，binary 會 copy 到 cache 再 chmod / codesign

## 限制

- 若譜面含 `<HS*...>`，Lax 模式會忽略它；notes / timing 保留，
  但 high-speed 視覺效果不會進 MA2
- Linux arm64 未 bundled；`host_rid()` 會回 `linux-arm64`，需自行 `--build`
- Windows arm64 使用 `win-x64`（x64 emulation）
- SUS 互轉仍是 experimental
- Universal wheel >100 MB；PyPI 請用 per-platform wheels

## License

本專案包含 MuConvert upstream binary/source，授權為
**LGPL-3.0-or-later**；詳見 `LICENSE`。
