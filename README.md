# MuConvert Panel v0.6.0b1

Pre-built wrapper + local/LAN web panel for
[MuConvert](https://github.com/MuNET-OSS/MuConvert).

版本：`0.6.0b1`（archive label: `ver0.6b`）  
Release notes：[RELEASE.md](RELEASE.md)  
授權與 binary 再散佈：[License](#license)、[NOTICE](NOTICE)  
使用範圍與無擔保：[免責聲明 / Disclaimer](#disclaimer)

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

跟朋友一起用時，優先用 CLI，或只開本機 UI。LAN 是進階用法，只給信任的家用區網。

```bash
# 本機（預設，建議）
python3 -m muconvert --ui
# http://127.0.0.1:8765/

# LAN（進階：信任的家用區網）
python3 -m muconvert --ui --host 0.0.0.0
```

`--host 0.0.0.0` 啟動時會印出 `LAN: http://192.168.x.x:8765/`。

Host allowlist 只檢查瀏覽器送來的 `Host` header，不是登入：

- 預設允許：localhost、IP literal、`*.local`
- 自訂 hostname：`--allow-host mymac.lan`
- 完全信任該網路才用：`--allow-any-host`
- LAN client 不能開伺服器端檔案 picker，也不能叫伺服器開 Finder / Explorer /
  xdg-open；請用 drag & drop 上傳，再下載結果

### Security / network notes

- **預設是 localhost**（`127.0.0.1`）。跟朋友分享轉換結果時，請在自己的電腦開這個位址，或直接用 CLI，再把轉好的檔案傳出去。
- `--host 0.0.0.0` 會聽所有介面。同網段任何人只要打開頁面，就會拿到嵌在 HTML 裡的 CSRF token，接著可以上傳、轉換、下載，以及觸發 build。這不是 authentication，連線也沒有 TLS。Token 只用來擋跨站請求，擋不住看得到這個頁面的人。
- 不要在咖啡廳、學校或其他不信任的 Wi‑Fi 上開 LAN mode。不要做 port-forward，也不要把這個服務暴露到網際網路。
- 請優先用 CLI 或 localhost UI。LAN 只適合你信任的家用區網。
- 在 LAN 上，以伺服器路徑呼叫的 inspect / convert（`/api/inspect`、`/api/convert`）以及 `/api/build` 權限很大：前者會讀取並轉換那台電腦上的路徑，後者會在那台電腦 clone upstream 並執行 `dotnet publish`。Host allowlist 擋不住已經打開頁面的人。

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

MuConvert Panel 是 [MuConvert](https://github.com/MuNET-OSS/MuConvert) 的 Python wrapper，並在 Release wheel / zip 裡再散佈它的 pre-built self-contained binary。實務說明也寫在 repo 根目錄的 [NOTICE](NOTICE)，讓 PyPI 與 GitHub Release 的使用者不用另外翻授權檔。

- **MuConvert**（upstream：<https://github.com/MuNET-OSS/MuConvert>）  
  Copyright (C) 2026 Starrah & MuNet Team。授權為 **LGPL-3.0-or-later**。
- **本 panel**（wrapper、web UI、launch scripts）同樣以 **LGPL-3.0-or-later** 提供。完整條文在 `LICENSE`（LGPL-3.0，以及它所引用的 GPL-3.0 全文）。

### 再散佈含 binary 的 wheel / zip

GitHub Release 與 per-platform wheel 會帶上預先建好的 MuConvert 執行檔。再散佈這些檔案時請：

1. **保留 `LICENSE`**（LGPL 與 GPL 全文）和 **`NOTICE`**，跟 wheel / zip 放在一起。
2. **提供或要約提供 Corresponding Source**（該 MuConvert binary 對應的原始碼）。實務上擇一或併用：
   - 寫明這次 binary 用的 upstream tag / commit，並附上連結，例如  
     `https://github.com/MuNET-OSS/MuConvert/commit/<sha>`
   - 附上重建方式。`python3 -m muconvert --build` 會 `git clone`  
     <https://github.com/MuNET-OSS/MuConvert>，再用 `dotnet publish` 做出目前平台的 self-contained binary。也可以自己 clone 同一個 commit，然後照上方 [Build](#build) 的參數發布。
3. **保留 copyright / license notices**（`LICENSE` 開頭的 Starrah & MuNet Team 聲明、`NOTICE`，以及 binary 若自帶的授權標示）。

**v0.6.0b1 的程式沒有 pin 某個 upstream tag 或 commit。** `--build` 第一次會 clone 預設分支的當下 HEAD；若本機已經有 source tree（repo 的 `vendor/MuConvert-src`，或 `~/.cache/muconvert/MuConvert-src`），之後只 `git fetch --tags`，沿用該 checkout，不會自動切到某個 tag。打包 Release 的人要自己記錄那次的 commit，並寫進該次 Release 說明：

```bash
git -C vendor/MuConvert-src rev-parse HEAD
# 從 cache 建的話：
git -C ~/.cache/muconvert/MuConvert-src rev-parse HEAD
muconvert --version
```

收到 binary 的人要用同一個 commit checkout 後再 `--build`，才會對到同一份 Corresponding Source。Panel 自己的原始碼就是這個 repo（wrapper 以原始碼形式散佈）。

遊戲媒體、使用責任與無擔保見下方 [免責聲明 / Disclaimer](#disclaimer)。LAN 的信任界線在 [Security / network notes](#security--network-notes)，與授權分開。

<a id="disclaimer"></a>

## 免責聲明 / Disclaimer

MuConvert Panel 只是譜面格式轉換工具，加上一個本機 / 區網 web panel。

- 本專案不提供、不代為存放、也不鼓勵散佈受著作權保護的遊戲音樂、官方譜面，或其他專有遊戲媒體，包括 SEGA、maimai、CHUNITHM、Ongeki 音樂、譜面的相關資產。
- 使用者要自行確認：對所轉換或用 `--package` 打包的每一個檔案，自己有合法使用權利。`--package` 只複製使用者電腦上已經存在的 companion files（見 [Companion files](#companion-files)），不會生成或附帶遊戲媒體。
- 使用風險自負。本軟體依 LGPL-3.0-or-later，在適用法律允許的範圍內以「as is」提供，沒有任何明示或默示擔保，包括適銷性（merchantability）與特定用途適用性（fitness for a particular purpose）。品質與效能的風險由使用者承擔；若需要保固，必須另有書面約定。完整條文見 `LICENSE`（GPL-3.0 第 15、16 節，由 LGPL-3.0 引用）。

區網怎麼開、誰打得開頁面，見 [Security / network notes](#security--network-notes)。
