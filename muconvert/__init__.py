#!/usr/bin/env python3
"""MuConvert wrapper + local/LAN web panel (macOS / Windows / Linux).

Package entry points:
  python3 -m muconvert --build                     # build host RID
  python3 -m muconvert --build-all                 # build all bundled RIDs
  python3 -m muconvert chart/maidata.txt -o out    # CLI conversion
  python3 -m muconvert --ui                        # http://127.0.0.1:8765
  python3 run.py ...                               # 0.2a-style launcher
  muconvert ...                                    # installed console script

The UI frontend is plain HTML/CSS/vanilla JS served by the Python stdlib.
No third-party Python packages are required at runtime.
"""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WRAPPER_VERSION = "0.6.0b1"
REPO_URL = "https://github.com/MuNET-OSS/MuConvert"
ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
ASSETS_DIR = ROOT
VENDOR = ROOT / "vendor"

CACHE_ROOT = Path(
    os.environ.get("MUCONVERT_CACHE_DIR") or (Path.home() / ".cache" / "muconvert")
)
SRC_DIR = PROJECT_ROOT / "vendor" / "MuConvert-src"
CACHE_SRC_DIR = CACHE_ROOT / "MuConvert-src"
CACHE_BIN_ROOT = CACHE_ROOT / "bin"
UPLOAD_ROOT = CACHE_ROOT / "uploads"


def host_rid() -> str:
    if sys.platform == "darwin":
        return "osx-arm64" if platform.machine() == "arm64" else "osx-x64"
    if sys.platform.startswith("win"):
        return "win-x64"
    if sys.platform.startswith("linux"):
        machine = platform.machine().lower()
        if machine in ("aarch64", "arm64"):
            return "linux-arm64"
        return "linux-x64"
    return "linux-x64"


BUILD_RIDS = ("osx-arm64", "osx-x64", "win-x64", "linux-x64")


def binary_filename(rid: str) -> str:
    return "MuConvert.exe" if rid.startswith("win") else "MuConvert"


def binary_path(rid: str, root: Path | None = None) -> Path:
    return (root or (VENDOR / "muconvert")) / rid / binary_filename(rid)


SUPPORTED_EXT = {".txt", ".ma2", ".c2s", ".ugc", ".sus"}
COMPANION_EXT = {
    ".xml",
    ".dat",
    ".acb",
    ".awb",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".mp4",
    ".mov",
    ".mp3",
    ".wav",
    ".ogg",
    ".m4a",
    ".flac",
    ".aac",
    ".json",
}
PACKAGE_MANIFEST = "muconvert_manifest.json"
TARGETS = {
    "simai": ["ma2"],
    "ma2": ["simai"],
    "c2s": ["ugc", "sus"],
    "ugc": ["c2s", "sus"],
    "sus": ["c2s"],
}
DEFAULT_TARGET = {
    "simai": "ma2",
    "ma2": "simai",
    "c2s": "ugc",
    "ugc": "c2s",
    "sus": "c2s",
}
KIND_LABEL = {
    "simai": "Simai (maidata / inote)",
    "ma2": "MA2",
    "c2s": "CHUNITHM C2S",
    "ugc": "CHUNITHM UGC",
    "sus": "SUS (experimental)",
}


# ---------------------------------------------------------------------------
# Errors / result types
# ---------------------------------------------------------------------------


class MuConvertError(RuntimeError):
    pass


class BinaryNotFoundError(MuConvertError):
    pass


class ConversionError(MuConvertError):
    pass


class BuildError(MuConvertError):
    pass


@dataclass
class Alert:
    level: str
    text: str


@dataclass
class Result:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    outputs: list[Path] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    duration: float = 0.0
    copied: list[Path] = field(default_factory=list)
    manifest: Path | None = None
    fallback_used: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def raise_for_status(self) -> "Result":
        if not self.ok:
            detail = "\n".join(a.text for a in self.alerts if a.level in ("error", "raw"))
            raise ConversionError(detail.strip() or f"MuConvert exited with {self.returncode}")
        return self

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "duration": round(self.duration, 3),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "alerts": [{"level": a.level, "text": a.text} for a in self.alerts],
            "log": [{"level": a.level, "text": a.text} for a in self.alerts],
            "outputs": [str(p) for p in self.outputs],
            "copied": [str(p) for p in self.copied],
            "manifest": str(self.manifest) if self.manifest else None,
            "fallback_used": self.fallback_used,
            "error_summary": self.error_summary,
            "command": self.args,
        }

    @property
    def error_summary(self) -> str | None:
        for alert in self.alerts:
            if alert.level == "error":
                return alert.text
        for alert in self.alerts:
            text = alert.text
            if "转换失败" in text or "轉換失敗" in text or "Conversion failed" in text:
                return text
        return None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def read_text_limited(path: Path, limit: int = 4_000_000) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return fh.read(limit)


def detect_kind(path: Path) -> str:
    """Return one of simai / ma2 / c2s / ugc / sus."""
    path = Path(path)
    if path.is_dir():
        files = sorted(
            p for p in path.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXT
        )
        if not files:
            raise ConversionError("目錄中沒有支援的譜面檔（.txt / .ma2 / .c2s / .ugc / .sus）")
        if all(p.suffix.lower() == ".ma2" for p in files):
            return "ma2"
        if len(files) == 1:
            return detect_kind(files[0])
        raise ConversionError(
            "目錄中存在多種/多個譜面檔，請只留下一個譜面檔，或全部都是 .ma2："
            + ", ".join(p.name for p in files)
        )

    if not path.exists():
        raise ConversionError(f"找不到路徑：{path}")

    ext = path.suffix.lower()
    if ext == ".txt":
        return "simai"
    if ext == ".ma2":
        return "ma2"
    if ext in (".c2s", ".ugc", ".sus"):
        return ext[1:]
    raise ConversionError(f"不支援的副檔名：{ext or '(無)'}")


def inspect_path(path: str | os.PathLike) -> dict:
    """Inspect a chart file or directory for the UI."""
    p = Path(path).expanduser()
    if not p.exists():
        raise ConversionError(f"找不到路徑：{p}")

    kind = detect_kind(p)
    levels: list[int] = []
    title: str | None = None
    hints: list[str] = []

    if p.is_dir():
        files = sorted(
            f.name for f in p.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_EXT
        )
        chart_files = [p / f for f in files]
    else:
        files = [p.name]
        chart_files = [p]

    if kind == "simai":
        text_file = next(
            (f for f in chart_files if f.suffix.lower() == ".txt"), None
        )
        if text_file is not None:
            text = read_text_limited(text_file)
            levels = sorted({int(m) for m in re.findall(r"&inote_(\d+)", text)})
            m = re.search(r"^&title=(.*)$", text, re.MULTILINE)
            if m:
                title = m.group(1).strip()
            if re.search(r"<HS", text, re.IGNORECASE):
                hints.append(
                    "此譜面含 <HS*> high-speed 語法，upstream Simai parser 不支援；"
                    "系統會自動 fallback 到 Lax，也可手動選 Lax。"
                )
            if re.search(r"^\s*E\s*$", text, re.MULTILINE):
                hints.append("譜面含單獨一行的 E token；建議使用 Lax 模式。")

    return {
        "path": str(p.resolve()),
        "kind": kind,
        "label": KIND_LABEL[kind],
        "levels": levels,
        "targets": TARGETS[kind],
        "default_target": DEFAULT_TARGET[kind],
        "files": files,
        "title": title,
        "is_dir": p.is_dir(),
        "hints": hints,
    }


# ---------------------------------------------------------------------------
# Binary discovery / build
# ---------------------------------------------------------------------------


def _codesign(path: Path) -> None:
    """Ad-hoc sign macOS binaries; ignore failures (codesign may be absent)."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["codesign", "--force", "--sign", "-", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _ensure_executable(path: Path) -> Path:
    """Fix permissions lost after pip/zip extraction; fall back to cache."""
    if sys.platform.startswith("win"):
        return path
    if os.access(path, os.X_OK):
        return path
    try:
        path.chmod(path.stat().st_mode | 0o111)
        _codesign(path)
        return path
    except OSError:
        dest_dir = CACHE_BIN_ROOT / host_rid()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / path.name
        shutil.copy2(path, dest)
        dest.chmod(dest.stat().st_mode | 0o111)
        _codesign(dest)
        return dest


def resolve_bin(explicit: str | os.PathLike | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env = os.environ.get("MUCONVERT_BIN")
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(binary_path(host_rid()))
    candidates.append(binary_path(host_rid(), CACHE_BIN_ROOT))
    which = shutil.which("MuConvert")
    if which:
        candidates.append(Path(which))

    last_error: Exception | None = None
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            return _ensure_executable(candidate).resolve()
        except OSError as exc:
            last_error = exc

    detail = f"\n（最後錯誤：{last_error}）" if last_error else ""
    raise BinaryNotFoundError(
        "找不到 MuConvert 執行檔。請先執行：\n"
        "  python3 -m muconvert --build      # 只建目前平台\n"
        "  python3 -m muconvert --build-all  # 建四個平台\n"
        f"（預期位置：{binary_path(host_rid())}）{detail}"
    )


def binary_version(bin_path: str | os.PathLike | None = None, timeout: int = 20) -> tuple[str, int]:
    exe = resolve_bin(bin_path)
    proc = subprocess.run(
        [str(exe), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    return ((proc.stdout or proc.stderr or "").strip(), proc.returncode)


def _run_stream(cmd: list[str], log) -> None:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        log(line.rstrip("\n"))
    rc = proc.wait()
    if rc != 0:
        raise BuildError(f"指令失敗 (exit {rc})：{' '.join(cmd)}")


def _source_dir() -> Path:
    if (SRC_DIR / "MuConvert.csproj").exists():
        return SRC_DIR
    return CACHE_SRC_DIR


def _build_output_root() -> tuple[Path, bool]:
    """Prefer the packaged assets dir; fall back to cache when read-only."""
    bundled = VENDOR / "muconvert"
    try:
        bundled.mkdir(parents=True, exist_ok=True)
        probe = bundled / ".write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return bundled, False
    except OSError:
        cached = CACHE_BIN_ROOT
        cached.mkdir(parents=True, exist_ok=True)
        return cached, True


def build_binary(rids: list[str] | None = None, on_line=None) -> str:
    """Clone upstream and publish self-contained binaries for the given RIDs."""
    if not shutil.which("git"):
        raise BuildError("找不到 git，請先安裝 git / Xcode Command Line Tools。")
    if not shutil.which("dotnet"):
        raise BuildError("找不到 dotnet，請先安裝 .NET 10 SDK。")

    rids = list(rids or [host_rid()])
    lines: list[str] = []

    def log(line: str) -> None:
        lines.append(line)
        if on_line is not None:
            try:
                on_line(line)
            except Exception:
                pass

    source = _source_dir()
    if not (source / "MuConvert.csproj").exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        log(f"$ git clone {REPO_URL} {source}")
        # Full clone: MinVer reads git tags to compute the version.
        _run_stream(["git", "clone", "--quiet", REPO_URL, str(source)], log)
    else:
        log(f"# 使用既有 source：{source}")
        try:
            _run_stream(["git", "-C", str(source), "fetch", "--tags", "--quiet"], log)
        except BuildError as exc:
            log(f"# git fetch 失敗（忽略）：{exc}")

    out_root, cached = _build_output_root()
    if cached:
        log(f"# package dir 不可寫，輸出改到：{out_root}")

    for rid in rids:
        out_dir = out_root / rid
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "dotnet",
            "publish",
            str(source / "MuConvert.csproj"),
            "-c",
            "Release",
            "-r",
            rid,
            "-p:SelfContained=true",
            "-p:UseAppHost=true",
            "-p:PublishSingleFile=true",
            "-p:EnableCompressionInSingleFile=true",
            "-p:DebugType=None",
            "-p:DebugSymbols=false",
            "-o",
            str(out_dir),
        ]
        log(f"$ # ---- {rid} ----")
        log("$ " + " ".join(cmd))
        _run_stream(cmd, log)

        target = binary_path(rid, out_root)
        if not target.exists():
            raise BuildError(f"publish 結束但找不到執行檔：{target}")

        if not sys.platform.startswith("win"):
            target.chmod(target.stat().st_mode | 0o111)
        if rid.startswith("osx"):
            _codesign(target)

        size_mb = target.stat().st_size / 1024 / 1024
        if rid == host_rid():
            version, rc = binary_version(target)
            if rc != 0:
                raise BuildError(f"MuConvert --version 失敗 (exit {rc})：{version}")
            log(f"# ok: {target} ({size_mb:.1f} MB, {version})")
        else:
            if target.stat().st_size < 1_000_000:
                raise BuildError(f"cross-build 檔案過小，可能失敗：{target}")
            log(f"# ok: {target} ({size_mb:.1f} MB)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


_SEVERITY_MARKERS = (
    ("error", ("ERROR:", "錯誤：", "错误：", "發生錯誤：", "エラー", "오류")),
    ("warning", ("WARNING:", "警告：", "警告:", "경고")),
    ("info", ("INFO:", "資訊：", "信息：", "情報", "정보")),
    ("debug", ("DEBUG:", "偵錯：", "调试：", "デバッグ", "디버그")),
)


def _classify_line(line: str) -> str:
    s = line.strip()
    s = re.sub(r"^\([^)]*\)\s*", "", s)
    head = s[:240]
    for level, markers in _SEVERITY_MARKERS:
        for marker in markers:
            if marker in head:
                return level
    if "→" in line:
        return "progress"
    return "raw"


def parse_stderr(stderr: str) -> list[Alert]:
    return [Alert(_classify_line(line), line) for line in stderr.splitlines() if line.strip()]


_PARSE_ERROR_MARKERS = (
    "unexpected token",
    "unrecognized character",
    "extraneous token",
    "转换失败",
    "轉換失敗",
    "conversion failed",
)


def _looks_like_parse_failure(stderr: str) -> bool:
    low = stderr.lower()
    return any(marker in low for marker in _PARSE_ERROR_MARKERS)


def _discover_outputs(
    inp: Path,
    kind: str,
    levels: list[int],
    target: str | None,
    out: Path | None,
) -> list[Path]:
    if out is not None:
        if out.exists() and out.is_file():
            return [out.resolve()]
        if out.suffix:
            return [out.resolve()] if out.exists() else []
        base = out
    else:
        base = inp if inp.is_dir() else inp.parent

    candidates: list[Path] = []
    if kind == "simai":
        if levels:
            candidates = [base / f"lv_{lv}.ma2" for lv in levels]
        else:
            candidates = sorted(base.glob("lv_*.ma2"))
    elif kind == "ma2":
        candidates = [base / "maidata.txt"]
    else:
        ext = target or DEFAULT_TARGET[kind]
        if inp.is_file():
            candidates = [base / f"{inp.stem}.{ext}"]
        else:
            chart_files = [
                f for f in inp.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_EXT
            ]
            if len(chart_files) == 1:
                candidates = [base / f"{chart_files[0].stem}.{ext}"]
            else:
                candidates = sorted(base.glob(f"*.{ext}"))

    found: list[Path] = []
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            resolved = candidate.resolve()
            if resolved not in found:
                found.append(resolved)
    return found


def package_outputs(source: Path, out_dir: Path) -> tuple[list[Path], Path]:
    """Copy companion files (xml/dat/acb/awb/assets) next to generated charts.

    Nothing is synthesized: Music.xml / .dat / .acb / .awb are only copied when
    the source folder already contains them. This mirrors the fact that
    MuConvert itself only converts chart formats.
    """
    src_dir = source if source.is_dir() else source.parent
    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    skipped: list[dict] = []

    for entry in sorted(src_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.name == PACKAGE_MANIFEST:
            skipped.append({"name": entry.name, "reason": "manifest"})
            continue
        ext = entry.suffix.lower()
        if ext in SUPPORTED_EXT:
            skipped.append(
                {"name": entry.name, "reason": "chart input; converted chart is in outputs"}
            )
            continue
        if ext not in COMPANION_EXT:
            skipped.append({"name": entry.name, "reason": "unsupported"})
            continue
        dest = out_dir / entry.name
        try:
            if dest.resolve() == entry.resolve():
                skipped.append({"name": entry.name, "reason": "same path"})
                continue
        except OSError:
            pass
        try:
            shutil.copy2(entry, dest)
        except OSError as exc:
            skipped.append({"name": entry.name, "reason": f"copy failed: {exc}"})
            continue
        copied.append(dest)

    manifest = out_dir / PACKAGE_MANIFEST
    payload = {
        "generated_by": f"muconvert {WRAPPER_VERSION}",
        "source": str(src_dir.resolve()),
        "copied": [p.name for p in copied],
        "skipped": skipped,
        "notes": [
            "Companion files are copied from the source folder; they are not generated.",
            "Music.xml is game metadata: use MaiChartManager (MCM) to generate/edit it.",
            ".dat is not handled by MuConvert; it is only copied when already present.",
            ".acb/.awb are CRIWARE containers: MuConvert cannot generate them.",
        ],
    }
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return copied, manifest


def run_convert(
    input_path: str | os.PathLike,
    *,
    levels: list[int] | list[str] | None = None,
    target: str | None = None,
    output: str | os.PathLike | None = None,
    strict: str | None = None,
    package: bool = False,
    auto_lax: bool = True,
    timeout: int = 180,
    bin_path: str | os.PathLike | None = None,
) -> Result:
    """Run MuConvert once. Returns a Result; never raises for a non-zero exit.

    With ``auto_lax`` (default), a failed Simai -> MA2 run in Normal mode is
    retried once with ``--lax``. This is needed because upstream's parser
    rejects common non-standard tokens such as ``<HS*0.4>``; Lax mode swallows
    those tokens and still produces the chart.
    """
    exe = resolve_bin(bin_path)
    inp = Path(input_path).expanduser()
    if not inp.exists():
        raise ConversionError(f"找不到路徑：{inp}")

    kind = detect_kind(inp)
    target = (target or "").strip().lower().lstrip(".") or None
    if target and target not in TARGETS[kind]:
        raise ConversionError(
            f"{KIND_LABEL[kind]} 不支援輸出為「{target}」；可用：{', '.join(TARGETS[kind])}"
        )

    strict = (strict or "normal").strip().lower()
    if strict not in ("normal", "strict", "lax"):
        raise ConversionError("strict 只能是 normal / strict / lax")
    if strict != "normal" and kind != "simai":
        raise ConversionError("--strict / --lax 僅適用於 Simai → MA2")

    parsed_levels: list[int] = []
    for raw in levels or []:
        try:
            value = int(str(raw).strip())
        except ValueError as exc:
            raise ConversionError(f"無效的難度編號：{raw}") from exc
        if value <= 0:
            raise ConversionError(f"難度編號必須大於 0：{raw}")
        if value not in parsed_levels:
            parsed_levels.append(value)

    args: list[str] = [str(exe), str(inp)]
    if parsed_levels:
        args += ["-l", ",".join(str(v) for v in parsed_levels)]
    if target:
        args += ["-t", target]

    out_path: Path | None = None
    if output == "-":
        args += ["-o", "-"]
    elif output:
        out_path = Path(output).expanduser()
        # Mirror upstream OutputSpec: a path with an extension is a file.
        if out_path.suffix:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            out_path.mkdir(parents=True, exist_ok=True)
        args += ["-o", str(out_path)]

    if strict != "normal":
        args.append("--" + strict)

    if package:
        if output == "-" or out_path is None:
            raise ConversionError("--package 需要指定輸出資料夾（-o <dir>）")
        if out_path.suffix:
            raise ConversionError("--package 需要輸出資料夾，不能指定單一檔案")

    def execute(extra: list[str] | None = None):
        cmd = args + (extra or [])
        attempt_started = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise MuConvertError(f"轉換逾時（{timeout}s）") from exc
        return proc, time.perf_counter() - attempt_started, cmd

    proc, duration, final_args = execute()
    extra_alerts: list[Alert] = []
    fallback_used = False

    if (
        proc.returncode != 0
        and auto_lax
        and strict == "normal"
        and kind == "simai"
        and _looks_like_parse_failure(proc.stderr or "")
    ):
        fallback_used = True
        retry, retry_duration, retry_args = execute(["--lax"])
        duration += retry_duration
        proc = retry
        final_args = retry_args
        if retry.returncode == 0:
            extra_alerts.append(
                Alert("info", "Normal 模式解析失敗，已自動改用 Lax 重試並成功。")
            )
        else:
            extra_alerts.append(
                Alert("info", "Normal 模式解析失敗；自動改用 Lax 重試仍失敗。")
            )

    outputs: list[Path] = []
    if output != "-":
        outputs = _discover_outputs(inp, kind, parsed_levels, target, out_path)

    copied: list[Path] = []
    manifest: Path | None = None
    if package and proc.returncode == 0:
        copied, manifest = package_outputs(inp, out_path)

    stderr = proc.stderr or ""
    return Result(
        args=final_args,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=stderr,
        outputs=outputs,
        alerts=extra_alerts + parse_stderr(stderr),
        duration=duration,
        copied=copied,
        manifest=manifest,
        fallback_used=fallback_used,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_version() -> int:
    print(f"muconvert {WRAPPER_VERSION} ({host_rid()})")
    try:
        version, rc = binary_version()
        print(f"MuConvert {version}" + ("" if rc == 0 else f" (exit {rc})"))
    except MuConvertError as exc:
        print(f"MuConvert: 未安裝（{exc}）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="muconvert",
        description="MuConvert wrapper + local/LAN web panel（macOS / Windows / Linux）",
    )
    parser.add_argument("path", nargs="?", help="輸入檔案或目錄")
    parser.add_argument("-l", "--levels", help="只轉換指定難度，例如 5,6")
    parser.add_argument("-t", "--target", help="強制指定輸出格式")
    parser.add_argument("-o", "--output", help="輸出檔案 / 目錄 / -（stdout）")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--strict", action="store_true", help="Simai 嚴格模式")
    group.add_argument("--lax", action="store_true", help="Simai 寬鬆模式")
    parser.add_argument(
        "--no-auto-lax",
        action="store_true",
        help="Normal 解析失敗時不要自動改用 Lax 重試",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="clone 並 build 目前平台的 self-contained MuConvert",
    )
    parser.add_argument(
        "--build-all",
        action="store_true",
        help="build 四個平台：osx-arm64 / osx-x64 / win-x64 / linux-x64",
    )
    parser.add_argument(
        "--package",
        action="store_true",
        help="把來源資料夾的 Music.xml / .dat / .acb / .awb / 素材複製到輸出資料夾",
    )
    parser.add_argument("--ui", action="store_true", help="啟動 local web panel")
    parser.add_argument("--port", type=int, default=8765, help="web panel port（預設 8765）")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address；用 0.0.0.0 開啟 LAN access（預設 127.0.0.1）",
    )
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        help="額外允許的 Host header（可重複），例如 mymac.lan",
    )
    parser.add_argument(
        "--allow-any-host",
        action="store_true",
        help="LAN 模式放行所有 Host header（較不安全，僅在信任網路使用）",
    )
    parser.add_argument("--no-browser", action="store_true", help="不要自動開瀏覽器")
    parser.add_argument("--version", action="store_true", help="顯示版本")
    args = parser.parse_args(argv)

    if args.build and args.build_all:
        parser.error("--build 與 --build-all 不能同時使用")

    if args.version:
        return _print_version()

    if args.build or args.build_all:
        rids = list(BUILD_RIDS) if args.build_all else [host_rid()]
        print(f"Building MuConvert for {', '.join(rids)}…", flush=True)
        try:
            build_binary(rids=rids, on_line=lambda line: print(line, flush=True))
        except (BuildError, MuConvertError) as exc:
            print(f"Build failed: {exc}", file=sys.stderr)
            return 1
        print("Build ok.")
        return 0

    if args.ui:
        return serve(
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
            allow_hosts=args.allow_host,
            allow_any_host=args.allow_any_host,
        )

    if not args.path:
        parser.print_help()
        return 2

    levels = None
    if args.levels:
        levels = [part.strip() for part in args.levels.split(",") if part.strip()]

    strict = "strict" if args.strict else "lax" if args.lax else "normal"

    try:
        result = run_convert(
            args.path,
            levels=levels,
            target=args.target,
            output=args.output,
            strict=strict,
            package=args.package,
            auto_lax=not args.no_auto_lax,
        )
    except MuConvertError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if result.fallback_used:
        print("# Normal 模式失敗，已自動以 Lax 重試。", file=sys.stderr)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.copied:
        print(f"package: copied {len(result.copied)} companion file(s) -> {result.manifest.parent}", file=sys.stderr)
    return result.returncode if result.returncode else 0


# ---------------------------------------------------------------------------
# Web panel (stdlib only)
# ---------------------------------------------------------------------------

PAGE = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MuConvert Panel</title>
<style>
  :root{
    color-scheme: light;
    --bg:#f4f5f7; --card:#ffffff; --text:#181b20; --muted:#687385;
    --line:#e3e6ea; --accent:#4f46e5; --accent-soft:#eef0ff;
    --ok:#0a8f4a; --warn:#b45309; --err:#d92d20;
    --log-bg:#0d1117; --log-fg:#c9d1d9;
    --radius:14px; --shadow:0 1px 2px rgba(16,24,40,.06), 0 8px 24px rgba(16,24,40,.05);
  }
  html[data-theme="dark"]{
    color-scheme: dark;
    --bg:#0d1014; --card:#151a21; --text:#e7eaf0; --muted:#96a0b0;
    --line:#252c37; --accent:#8b84ff; --accent-soft:#1e1f3a;
    --ok:#31c48d; --warn:#f5a524; --err:#f97066;
    --log-bg:#090c11; --log-fg:#c9d1d9;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 12px 30px rgba(0,0,0,.25);
  }
  @media (prefers-color-scheme: dark){
    html:not([data-theme="light"]){
      color-scheme: dark;
      --bg:#0d1014; --card:#151a21; --text:#e7eaf0; --muted:#96a0b0;
      --line:#252c37; --accent:#8b84ff; --accent-soft:#1e1f3a;
      --ok:#31c48d; --warn:#f5a524; --err:#f97066;
      --log-bg:#090c11; --log-fg:#c9d1d9;
      --shadow:0 1px 2px rgba(0,0,0,.4), 0 12px 30px rgba(0,0,0,.25);
    }
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}
  button,input,select{font:inherit;color:inherit}
  code,pre,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  .shell{max-width:1040px;margin:0 auto;padding:26px 20px 80px}
  header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px}
  .brand{display:flex;align-items:center;gap:10px;font-size:20px;font-weight:750;letter-spacing:-.02em}
  .logo{display:grid;place-items:center;width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,var(--accent),#9b8cff);color:#fff;font-size:18px}
  .pill{font-size:11px;font-weight:650;letter-spacing:.04em;text-transform:uppercase;padding:3px 8px;border-radius:999px;background:var(--accent-soft);color:var(--accent)}
  .head-right{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:13px}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}
  .dot.ok{background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb, var(--ok) 18%, transparent)}
  .dot.bad{background:var(--err)}
  .icon-btn{background:transparent;border:1px solid var(--line);border-radius:9px;width:32px;height:32px;cursor:pointer;color:var(--muted)}
  .icon-btn:hover{border-color:var(--accent);color:var(--accent)}
  .stack{display:flex;flex-direction:column;gap:16px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:18px}
  .card-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:14px}
  h2{font-size:15px;margin:0;letter-spacing:-.01em}
  .muted{color:var(--muted)}
  .row{display:flex;gap:8px;align-items:center}
  .text{flex:1;min-width:0;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:10px 12px;outline:none}
  .text:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
  .btn{appearance:none;text-decoration:none;display:inline-flex;align-items:center;gap:7px;border:1px solid var(--line);background:var(--card);border-radius:10px;padding:9px 14px;cursor:pointer;font-weight:600;white-space:nowrap}
  .btn:hover{border-color:var(--accent);color:var(--accent)}
  .btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
  .btn.primary:hover{filter:brightness(1.06);color:#fff}
  .btn.ghost{background:transparent;color:var(--muted)}
  .btn.small{padding:6px 10px;font-size:12px}
  .btn:disabled{opacity:.45;cursor:not-allowed}
  .btn:disabled:hover{border-color:var(--line);color:inherit}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}
  .field{display:flex;flex-direction:column;gap:7px;min-width:0}
  .field>label{font-size:12px;font-weight:650;color:var(--muted);letter-spacing:.02em}
  .dropzone{margin-top:12px;border:1.5px dashed var(--line);border-radius:12px;padding:18px;text-align:center;color:var(--muted);cursor:pointer;transition:.15s}
  .dropzone:hover{border-color:var(--accent);background:var(--accent-soft)}
  .meta-line{margin-top:12px;font-size:12.5px;color:var(--muted);min-height:18px}
  .hints{display:flex;flex-direction:column;gap:6px;margin-top:8px}
  .hintline{display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--warn);background:color-mix(in srgb,var(--warn) 8%,var(--card));border:1px solid color-mix(in srgb,var(--warn) 25%,var(--line));border-radius:9px;padding:8px 10px}
  .hintline span{flex:1}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
  .chip{background:var(--bg);border:1px solid var(--line);border-radius:999px;padding:3px 9px;font-size:12px}
  .levels{display:flex;gap:8px;flex-wrap:wrap}
  .level{display:inline-flex;align-items:center;gap:6px;background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:7px 10px;cursor:pointer;user-select:none}
  .level input{accent-color:var(--accent)}
  .level.on{border-color:var(--accent);background:var(--accent-soft)}
  .seg{display:flex;border:1px solid var(--line);border-radius:10px;overflow:hidden}
  .seg label{flex:1;text-align:center;padding:8px 10px;cursor:pointer;font-size:12.5px;color:var(--muted)}
  .seg input{display:none}
  .seg label.on{background:var(--accent-soft);color:var(--accent);font-weight:650}
  .seg.disabled{opacity:.45;pointer-events:none}
  select{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:10px 12px;outline:none;width:100%}
  select:focus{border-color:var(--accent)}
  .actions{display:flex;align-items:center;gap:10px;margin-top:16px;flex-wrap:wrap}
  .spacer{flex:1}
  .banner{display:flex;align-items:center;justify-content:space-between;gap:14px;border-radius:12px;padding:13px 16px;margin-bottom:14px;border:1px solid}
  .banner.err{background:color-mix(in srgb,var(--err) 8%,var(--card));border-color:color-mix(in srgb,var(--err) 35%,var(--line))}
  .banner strong{display:block;margin-bottom:2px}
  .result-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .result-ok{color:var(--ok);font-weight:700}
  .result-err{color:var(--err);font-weight:700}
  .files{display:flex;flex-direction:column;gap:8px;margin-top:12px}
  .file{display:flex;align-items:center;gap:10px;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:9px 12px}
  .file .name{font-weight:600;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .file .size{color:var(--muted);font-size:12px}
  pre.log{margin:12px 0 0;background:var(--log-bg);color:var(--log-fg);border-radius:10px;padding:13px 14px;max-height:320px;overflow:auto;font-size:12px;line-height:1.55;white-space:pre-wrap}
  pre.log .l-error{color:#ff7b72}
  pre.log .l-warning{color:#e3b341}
  pre.log .l-info{color:#79c0ff}
  pre.log .l-progress{color:#a5d6ff}
  pre.log .l-debug,pre.log .l-raw{color:#8b949e}
  pre.chart{margin:12px 0 0;background:var(--log-bg);color:var(--log-fg);border-radius:10px;padding:13px 14px;max-height:360px;overflow:auto;font-size:11.5px;line-height:1.5;white-space:pre}
  pre.log.small{max-height:190px;font-size:11px}
  .empty{color:var(--muted);padding:8px 0}
  .toast{position:fixed;right:24px;bottom:24px;background:var(--text);color:var(--card);padding:10px 16px;border-radius:999px;font-weight:600;font-size:13px;box-shadow:var(--shadow);z-index:40;max-width:60vw}
  .toast.err{background:var(--err);color:#fff}
  .toast.ok{background:var(--ok);color:#fff}
  .drop-overlay{position:fixed;inset:0;background:color-mix(in srgb,var(--accent) 12%,transparent);backdrop-filter:blur(3px);display:none;place-items:center;z-index:50;pointer-events:none}
  .drop-overlay.show{display:grid}
  .drop-box{background:var(--card);border:2px dashed var(--accent);border-radius:18px;padding:44px 60px;text-align:center;display:flex;flex-direction:column;gap:8px;box-shadow:var(--shadow)}
  .spin{width:14px;height:14px;border:2px solid var(--line);border-top-color:var(--accent);border-radius:50%;display:inline-block;animation:spin .7s linear infinite;vertical-align:-2px}
  @keyframes spin{to{transform:rotate(360deg)}}
  .sub{font-size:12.5px;color:var(--muted);margin-top:3px}
  @media(max-width:640px){.row{flex-wrap:wrap}.row .text{flex-basis:100%}.actions{flex-wrap:wrap}}
</style>
</head>
<body>
<div id="app" class="shell">loading…</div>
<div class="drop-overlay" id="drop-overlay"><div class="drop-box"><strong>放開以載入譜面</strong><span class="muted">maidata.txt / .ma2 / .c2s / .ugc / .sus</span></div></div>
<script>
"use strict";

const TOKEN = "__MUCONVERT_TOKEN__";
const app = document.getElementById("app");
const overlay = document.getElementById("drop-overlay");

const LS = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem("mc." + key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (err) { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem("mc." + key, JSON.stringify(value)); } catch (err) {}
  }
};

const state = {
  status: null,
  input: LS.get("input", ""),
  output: LS.get("output", "out"),
  kind: null,
  label: "",
  levels: [],
  selected: [],
  targets: [],
  target: LS.get("target", ""),
  strict: LS.get("strict", "normal"),
  preview: false,
  package: LS.get("package", false),
  uploadId: null,
  uploadNames: [],
  busy: false,
  busyText: "",
  startedAt: 0,
  result: null,
  inspectError: "",
  hints: [],
  theme: LS.get("theme", "auto"),
  toast: null,
  buildLog: [],
  buildRunning: false,
  buildTimer: null
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));

const sizeFmt = (n) => n < 1024 ? n + " B"
  : n < 1024 * 1024 ? (n / 1024).toFixed(1) + " KB"
  : (n / 1024 / 1024).toFixed(1) + " MB";

function applyTheme() {
  if (state.theme === "dark" || state.theme === "light") {
    document.documentElement.dataset.theme = state.theme;
  } else {
    delete document.documentElement.dataset.theme;
  }
}

async function api(path, opts = {}) {
  const headers = Object.assign({ "X-MuConvert-Token": TOKEN }, opts.headers || {});
  let body = opts.body;
  if (body && !(body instanceof Blob) && typeof body !== "string") {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(body);
  }
  const res = await fetch(path, Object.assign({}, opts, { headers, body }));
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}

function toast(msg, kind) {
  state.toast = { msg, kind: kind || "" };
  render();
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => { state.toast = null; render(); }, 3200);
}

function headerHTML() {
  const s = state.status;
  const ok = !!s?.ok;
  const ver = s?.version ? s.version.split("\n")[0].split("+")[0] : (ok ? "ready" : "not installed");
  return `<header>
    <div class="brand"><span class="logo">♪</span> MuConvert <span class="pill">Panel</span>${state.status?.lan ? '<span class="pill" title="LAN mode">LAN</span>' : ""}</div>
    <div class="head-right">
      <span class="dot ${ok ? "ok" : "bad"}" title="${ok ? "binary ready" : "binary missing"}"></span>
      <span class="mono" title="${esc(s?.version || "")}">${esc(ver)}</span>
      <button class="icon-btn" data-action="theme" title="切換主題">${state.theme === "dark" ? "☀" : "☾"}</button>
    </div>
  </header>`;
}

function buildPanelInner() {
  const s = state.status;
  const running = state.buildRunning || !!s?.build?.running;
  const log = (state.buildLog && state.buildLog.length) ? state.buildLog : (s?.build_log || []);
  if (!running && !log.length) return "";
  const ok = s?.build?.ok;
  return `<section class="card" style="margin-bottom:14px">
    <div class="card-head"><h2>Build</h2><span class="muted">${running ? '<span class="spin"></span> 執行中' : (ok === false ? "失敗" : "完成")}</span></div>
    <pre class="log small">${esc(log.slice(-80).join("\n"))}</pre>
  </section>`;
}

function bannerHTML() {
  const s = state.status;
  let banner = "";
  if (s && !s.ok) {
    const running = state.buildRunning || !!s.build?.running;
    banner = `<div class="banner err">
      <div><strong>找不到 MuConvert 執行檔</strong>
        <div class="sub">按 Build now 會 clone upstream，並用 dotnet publish 建立 ${esc(s.rid || "native")} binary。</div></div>
      <button class="btn" data-action="build" ${running ? "disabled" : ""}>${running ? '<span class="spin"></span> Building…' : "Build now"}</button>
    </div>`;
  }
  return banner + `<div id="build-panel">${buildPanelInner()}</div>`;
}

function inputCardHTML() {
  const uploaded = !!state.uploadId;
  const remote = !!state.status?.remote;
  const meta = state.inspectError
    ? `<span class="result-err">${esc(state.inspectError)}</span>`
    : state.kind
      ? `${esc(state.label)}${state.kind === "simai" && state.levels.length ? " · 難度 " + state.levels.join(", ") : ""}${state.title ? " · " + esc(state.title) : ""}${uploaded ? " · " + state.uploadNames.length + " 個檔案" : ""}`
      : "支援 Simai ↔ MA2、C2S / UGC / SUS 互轉（SUS 為 experimental）";
  const note = state.busyText ? `<span class="muted">${esc(state.busyText)}</span>` : (uploaded ? "已上傳" : state.kind ? "已載入" : "選擇檔案或資料夾");
  return `<section class="card">
    <div class="card-head"><h2>輸入</h2>${note}</div>
    <div class="row">
      <input class="text mono" data-model="input" value="${esc(state.input)}" placeholder="/path/to/maidata.txt 或 chart 資料夾" spellcheck="false" ${uploaded ? "disabled" : ""}>
      <button class="btn" data-action="pick-file" ${remote ? "disabled" : ""} title="${remote ? "遠端連線請使用拖放上傳" : ""}">檔案</button>
      <button class="btn" data-action="pick-folder" ${remote ? "disabled" : ""} title="${remote ? "遠端連線請使用拖放上傳" : ""}">資料夾</button>
      ${uploaded ? '<button class="btn ghost" data-action="clear-upload">清除</button>' : ""}
    </div>
    ${remote ? '<div class="sub" style="margin-top:8px">遠端連線：伺服器端檔案選擇已停用，請使用拖放上傳。</div>' : ""}
    ${uploaded
      ? `<div class="chips">${state.uploadNames.map((n) => `<span class="chip">${esc(n)}</span>`).join("")}</div>`
      : `<div class="dropzone" data-action="${remote ? "" : "pick-file"}">拖放檔案或資料夾到此處<span class="sub" style="display:block">也可以直接貼上路徑，失焦後自動解析</span></div>`}
    <div class="meta-line">${meta}</div>
    ${state.hints.length ? `<div class="hints"><div class="hintline"><span>${state.hints.map((h) => `⚠ ${esc(h)}`).join("<br>")}</span><button class="btn small" data-action="use-lax">改用 Lax</button></div></div>` : ""}
  </section>`;
}

function optionsCardHTML() {
  const kind = state.kind;
  const levels = state.levels;
  const preview = state.preview;
  const remote = !!state.status?.remote;
  const targetOptions = state.targets.map((t) => `<option value="${t}" ${state.target === t ? "selected" : ""}>${t.toUpperCase()}</option>`).join("");
  const strictDisabled = kind !== "simai";
  const seg = (value, label) => `<label class="${state.strict === value ? "on" : ""}"><input type="radio" name="strict" value="${value}" ${state.strict === value ? "checked" : ""} ${strictDisabled ? "disabled" : ""}>${label}</label>`;
  return `<section class="card">
    <div class="card-head"><h2>選項</h2><span class="muted">${kind ? (kind === "simai" ? "Simai → MA2" : "→ " + (state.target || state.targets[0] || "").toUpperCase()) : "先選擇輸入"}</span></div>
    <div class="grid">
      <div class="field" style="grid-column:1/-1">
        <label>輸出位置</label>
        <div class="row">
          <input class="text mono" data-model="output" value="${esc(state.uploadId ? "" : state.output)}" placeholder="${state.uploadId ? "上傳模式：結果提供下載" : "out/"}" spellcheck="false" ${preview || state.uploadId ? "disabled" : ""}>
          <button class="btn" data-action="pick-output" ${preview || state.uploadId || remote ? "disabled" : ""}>資料夾</button>
          <label class="level ${preview ? "on" : ""}" style="white-space:nowrap">
            <input type="checkbox" data-model="preview" ${preview ? "checked" : ""}> 預覽到 stdout
          </label>
        </div>
      </div>
      ${kind === "simai" && levels.length ? `<div class="field" style="grid-column:1/-1">
        <label>難度（&amp;inote_N）</label>
        <div class="levels">${levels.map((n) => `<label class="level ${state.selected.includes(n) ? "on" : ""}"><input type="checkbox" data-level="${n}" ${state.selected.includes(n) ? "checked" : ""}> ${n}</label>`).join("")}</div>
      </div>` : ""}
      <div class="field" style="grid-column:1/-1">
        <label class="level ${state.package ? "on" : ""}" title="${state.uploadId ? "上傳模式僅處理譜面檔；package 請用路徑模式" : ""}">
          <input type="checkbox" data-model="package" ${state.package ? "checked" : ""} ${preview || state.uploadId ? "disabled" : ""}>
          一併複製 companion files
        </label>
        <div class="sub">Music.xml / .dat / .acb / .awb / 封面 / 音訊 / 影片；只複製來源已有的檔案，<b>不會生成</b> acb/awb</div>
      </div>
      <div class="field">
        <label>輸出格式</label>
        <select data-model="target" ${kind ? "" : "disabled"}>${targetOptions || "<option>—</option>"}</select>
      </div>
      <div class="field">
        <label>Simai 容錯</label>
        <div class="seg ${strictDisabled ? "disabled" : ""}">${seg("normal", "Normal")}${seg("strict", "Strict")}${seg("lax", "Lax")}</div>
      </div>
    </div>
    <div class="actions">
      <button class="btn primary" data-action="convert" ${state.busy || !kind ? "disabled" : ""}>
        ${state.busy ? '<span class="spin"></span> 轉換中… <span id="elapsed">0.0s</span>' : "轉換 ⌘↵"}
      </button>
      ${state.result?.output_dir && !state.busy && !remote ? `<button class="btn" data-action="reveal-dir" data-path="${esc(state.result.output_dir)}">開啟輸出資料夾</button>` : ""}
      <span class="spacer"></span>
      <span class="muted">${kind === "c2s" ? "C2S → SUS 請選 SUS" : ""}</span>
    </div>
  </section>`;
}

function resultCardHTML() {
  const r = state.result;
  if (!r) return `<section class="card"><div class="card-head"><h2>結果</h2></div><div class="empty">尚未轉換</div></section>`;
  const ok = !!r.ok;
  const remote = !!state.status?.remote;
  const files = (r.files || []).map((f) => `<div class="file">
      <span class="name mono">${esc(f.name)}</span>${f.kind && f.kind !== "chart" ? `<span class="chip">${esc(f.kind)}</span>` : ""}<span class="size">${sizeFmt(f.size || 0)}</span>
      ${remote ? "" : `<button class="btn small" data-action="reveal" data-id="${esc(f.id)}">在資料夾中顯示</button>`}
      <a class="btn small" href="/api/download?id=${encodeURIComponent(f.id)}&t=${encodeURIComponent(TOKEN)}">下載</a>
    </div>`).join("");
  const logLines = r.log || [];
  const shownLog = logLines.length > 300 ? logLines.slice(-300) : logLines;
  const note = logLines.length > 300 ? `<div class="l-raw">… 只顯示最後 300 / ${logLines.length} 行</div>` : "";
  const log = shownLog.map((e) => `<div class="l-${esc(e.level)}">${esc(e.text)}</div>`).join("");
  const chart = r.stdout ? `<div class="row" style="margin-top:10px"><span class="muted">stdout</span><span class="spacer"></span><button class="btn small" data-action="copy">複製</button></div><pre class="chart">${esc(r.stdout)}</pre>` : "";
  const fallback = r.fallback_used ? `<span class="chip">Normal → Lax</span>` : "";
  const errLine = !ok && r.error_summary ? `<div class="result-err" style="margin-top:6px">${esc(r.error_summary)}</div>` : "";
  return `<section class="card">
    <div class="card-head"><h2>結果</h2><span class="muted">${r.duration ?? 0}s</span></div>
    <div class="result-head">
      <span class="${ok ? "result-ok" : "result-err"}">${ok ? "✓ 成功" : "✗ 失敗"}</span>
      <span class="muted mono">exit ${r.returncode}</span>
      ${fallback}
      <span class="muted mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc((r.command || []).slice(1).join(" "))}</span>
    </div>
    ${errLine}
    ${files ? `<div class="files">${files}</div>` : ""}
    ${chart}
    <pre class="log">${note}${log || '<span class="l-raw">(no output)</span>'}</pre>
  </section>`;
}

function render() {
  app.innerHTML = `${headerHTML()}${bannerHTML()}<main class="stack">${inputCardHTML()}${optionsCardHTML()}${resultCardHTML()}</main>${state.toast ? `<div class="toast ${esc(state.toast.kind)}">${esc(state.toast.msg)}</div>` : ""}`;
  if (state.busy) startElapsed();
}

function startElapsed() {
  clearInterval(state.elapsedTimer);
  state.elapsedTimer = setInterval(() => {
    const el = document.getElementById("elapsed");
    if (!el || !state.busy) { clearInterval(state.elapsedTimer); return; }
    el.textContent = ((performance.now() - state.startedAt) / 1000).toFixed(1) + "s";
  }, 100);
}

let inspectTimer = null;
function scheduleInspect() {
  clearTimeout(inspectTimer);
  inspectTimer = setTimeout(inspectNow, 350);
}

async function inspectNow() {
  const raw = state.input.trim();
  if (!raw || state.uploadId) return;
  try {
    const info = await api("/api/inspect", { method: "POST", body: { path: raw } });
    applyInspect(info);
  } catch (err) {
    state.inspectError = err.message;
    state.kind = null;
    state.levels = [];
    state.targets = [];
    state.selected = [];
    state.hints = [];
    render();
  }
}

function applyInspect(info) {
  const sameKind = state.kind === info.kind;
  state.kind = info.kind;
  state.label = info.label || "";
  state.levels = info.levels || [];
  state.targets = info.targets || [];
  state.title = info.title || "";
  state.hints = info.hints || [];
  state.inspectError = "";
  if (!state.targets.includes(state.target)) state.target = info.default_target || state.targets[0] || "";
  if (state.levels.length) {
    state.selected = sameKind && state.selected.some((x) => state.levels.includes(x))
      ? state.selected.filter((x) => state.levels.includes(x))
      : state.levels.slice();
  } else {
    state.selected = [];
  }
  render();
}

async function pick(mode) {
  const r = await api("/api/pick", { method: "POST", body: { mode } });
  if (r.error) throw new Error(r.error);
  return r.canceled ? null : (r.path || null);
}

async function startBuild() {
  try {
    await api("/api/build", { method: "POST" });
    state.buildRunning = true;
    state.buildLog = [];
    render();
    pollBuild();
  } catch (err) {
    toast(err.message, "err");
  }
}

function pollBuild() {
  clearInterval(state.buildTimer);
  state.buildTimer = setInterval(async () => {
    try {
      const s = await api("/api/build/status");
      state.buildRunning = !!s.running;
      state.buildLog = s.log || [];
      const host = document.getElementById("build-panel");
      if (host) host.innerHTML = buildPanelInner();
      if (!s.running) {
        clearInterval(state.buildTimer);
        state.status = await api("/api/status");
        render();
        toast(s.ok ? "Build 完成" : "Build 失敗", s.ok ? "ok" : "err");
      }
    } catch (err) {
      clearInterval(state.buildTimer);
      state.buildRunning = false;
      render();
    }
  }, 700);
}

async function convert() {
  if (state.busy) return;
  if (!state.kind && state.input.trim()) await inspectNow();
  if (!state.kind) return;
  if (state.kind === "simai" && state.levels.length && !state.selected.length) {
    toast("請至少選一個難度", "err");
    return;
  }
  state.busy = true;
  state.startedAt = performance.now();
  state.result = null;
  render();
  const body = {
    levels: state.selected,
    target: state.target || null,
    strict: state.strict,
    preview: state.preview,
    package: state.package && !state.preview && !state.uploadId
  };
  if (state.uploadId) {
    body.upload_id = state.uploadId;
  } else {
    body.input = state.input.trim();
    body.output = state.preview ? null : (state.output.trim() || "out");
  }
  try {
    const r = await api("/api/convert", { method: "POST", body });
    state.result = r;
    toast(r.ok ? `完成 · ${(r.files || []).length} 個輸出` : "轉換失敗，請看 log", r.ok ? "ok" : "err");
  } catch (err) {
    state.result = {
      ok: false, returncode: -1, duration: 0, stdout: "", stderr: err.message,
      log: [{ level: "error", text: err.message }], files: [], command: [], output_dir: null
    };
    toast(err.message, "err");
  } finally {
    state.busy = false;
    render();
  }
}

async function uploadFiles(files) {
  const supported = files.filter((f) => /\.(txt|ma2|c2s|ugc|sus)$/i.test(f.file.name));
  if (!supported.length) { toast("沒有支援的譜面檔", "err"); return; }
  if (supported.length > 200) { toast("一次最多 200 個檔案", "err"); return; }

  const uid = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)).replace(/-/g, "");
  state.uploadId = uid;
  state.uploadNames = [];
  state.busy = true;
  state.result = null;
  render();
  try {
    for (let i = 0; i < supported.length; i++) {
      const item = supported[i];
      state.busyText = `上傳中 ${i + 1}/${supported.length}`;
      render();
      const res = await fetch("/api/upload", {
        method: "POST",
        headers: {
          "X-MuConvert-Token": TOKEN,
          "X-Upload-Id": uid,
          "X-Filename": encodeURIComponent(item.path || item.file.name)
        },
        body: item.file
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "upload failed");
      state.uploadNames.push(data.name || item.file.name);
    }
    const info = await api("/api/upload/finish", { method: "POST", body: { upload_id: uid } });
    state.input = "";
    applyInspect(info);
    toast(`已載入 ${state.uploadNames.length} 個檔案`, "ok");
  } catch (err) {
    state.uploadId = null;
    state.uploadNames = [];
    toast(err.message, "err");
  } finally {
    state.busy = false;
    state.busyText = "";
    render();
  }
}

async function walkEntry(entry) {
  if (entry.isFile) {
    const file = await new Promise((res, rej) => entry.file(res, rej));
    return [{ file, path: entry.name }];
  }
  if (!entry.isDirectory) return [];
  const reader = entry.createReader();
  const entries = [];
  for (;;) {
    const batch = await new Promise((res, rej) => reader.readEntries(res, rej));
    if (!batch.length) break;
    entries.push(...batch);
  }
  const out = [];
  for (const child of entries) out.push(...await walkEntry(child));
  return out;
}

async function handleDrop(e) {
  const collected = [];
  const items = Array.from(e.dataTransfer.items || []);
  if (!items.length) {
    for (const file of Array.from(e.dataTransfer.files || [])) {
      collected.push({ file, path: file.name });
    }
  } else {
    for (const item of items) {
      if (item.kind !== "file") continue;
      const entry = item.webkitGetAsEntry && item.webkitGetAsEntry();
      if (entry) {
        collected.push(...await walkEntry(entry));
      } else {
        const file = item.getAsFile();
        if (file) collected.push({ file, path: file.name });
      }
    }
  }
  if (collected.length) await uploadFiles(collected);
}

window.addEventListener("dragenter", (e) => {
  if (Array.from(e.dataTransfer?.types || []).includes("Files")) {
    e.preventDefault();
    overlay.classList.add("show");
  }
});
window.addEventListener("dragover", (e) => {
  if (Array.from(e.dataTransfer?.types || []).includes("Files")) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }
});
window.addEventListener("dragleave", (e) => {
  if (e.relatedTarget === null) overlay.classList.remove("show");
});
window.addEventListener("drop", async (e) => {
  e.preventDefault();
  overlay.classList.remove("show");
  await handleDrop(e);
});

document.addEventListener("input", (e) => {
  const el = e.target.closest("[data-model]");
  if (!el) return;
  const key = el.dataset.model;
  state[key] = el.type === "checkbox" ? el.checked : el.value;
  if (key === "input") { LS.set("input", state.input); scheduleInspect(); }
  if (key === "output") LS.set("output", state.output);
  if (key === "package") { LS.set("package", state.package); render(); }
  if (key === "preview") {
    if (state.preview) { state.package = false; LS.set("package", false); }
    render();
  }
});

document.addEventListener("change", (e) => {
  const el = e.target;
  if (el.dataset.model === "target") {
    state.target = el.value;
    LS.set("target", state.target);
    render();
    return;
  }
  if (el.name === "strict") {
    state.strict = el.value;
    LS.set("strict", state.strict);
    render();
    return;
  }
  if (el.dataset.level) {
    const n = Number(el.dataset.level);
    state.selected = el.checked
      ? Array.from(new Set([...state.selected, n])).sort((a, b) => a - b)
      : state.selected.filter((x) => x !== n);
    render();
  }
});

document.addEventListener("focusout", (e) => {
  if (e.target.matches('[data-model="input"]')) inspectNow();
});

document.addEventListener("keydown", (e) => {
  if (e.target.matches('[data-model="input"]') && e.key === "Enter") {
    e.preventDefault();
    inspectNow();
  }
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
    e.preventDefault();
    convert();
  }
});

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  const action = btn.dataset.action;
  try {
    if (action === "theme") {
      state.theme = state.theme === "dark" ? "light" : "dark";
      LS.set("theme", state.theme);
      applyTheme();
      render();
    } else if (action === "pick-file" || action === "pick-folder") {
      const p = await pick(action === "pick-folder" ? "folder" : "file");
      if (p) {
        state.uploadId = null;
        state.uploadNames = [];
        state.input = p;
        LS.set("input", p);
        const info = await api("/api/inspect", { method: "POST", body: { path: p } });
        applyInspect(info);
      }
    } else if (action === "pick-output") {
      const p = await pick("folder");
      if (p) { state.output = p; LS.set("output", p); render(); }
    } else if (action === "clear-upload") {
      state.uploadId = null;
      state.uploadNames = [];
      state.kind = null;
      state.levels = [];
      state.targets = [];
      state.selected = [];
      state.hints = [];
      state.result = null;
      render();
    } else if (action === "use-lax") {
      state.strict = "lax";
      LS.set("strict", state.strict);
      render();
    } else if (action === "convert") {
      await convert();
    } else if (action === "build") {
      await startBuild();
    } else if (action === "reveal") {
      await api("/api/reveal", { method: "POST", body: { id: btn.dataset.id } });
    } else if (action === "reveal-dir") {
      await api("/api/reveal", { method: "POST", body: { path: btn.dataset.path } });
    } else if (action === "copy") {
      await navigator.clipboard.writeText(state.result?.stdout || "");
      toast("已複製", "ok");
    }
  } catch (err) {
    toast(err.message, "err");
  }
});

applyTheme();
render();
(async function init() {
  try {
    state.status = await api("/api/status");
  } catch (err) {
    state.status = { ok: false, error: err.message };
  }
  if (state.status?.build?.running) {
    state.buildRunning = true;
    pollBuild();
  }
  render();
  if (state.input.trim() && !state.uploadId) inspectNow();
})();
</script>
</body>
</html>
"""


class PanelState:
    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        self.downloads: dict[str, Path] = {}
        self.uploads: dict[str, Path] = {}
        self.build = {"running": False, "ok": None, "log": [], "version": None}
        self.build_lock = threading.Lock()
        self.version_cache = {"at": 0.0, "version": None, "error": None, "binary": None}
        self.allow_lan = False
        self.bind_host = "127.0.0.1"
        self.allow_any_host = False
        self.allowed_hosts: set[str] = set()
        self.last_output_dir: Path | None = None

    def status(self) -> dict:
        now = time.time()
        cache = self.version_cache
        if now - cache["at"] > 2.0:
            version = None
            error = None
            binary = None
            try:
                binary = str(resolve_bin())
                version, rc = binary_version(binary)
                if rc != 0:
                    error = f"--version exit {rc}"
                    version = None
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                error = str(exc)
            cache.update(at=now, version=version, error=error, binary=binary)
        return {
            "ok": cache["version"] is not None,
            "version": cache["version"],
            "error": cache["error"],
            "binary": cache["binary"],
            "rid": host_rid(),
            "lan": self.allow_lan,
            "bind_host": self.bind_host,
            "allow_any_host": self.allow_any_host,
            "build": {
                "running": self.build["running"],
                "ok": self.build["ok"],
                "version": self.build["version"],
            },
            "build_log": self.build["log"][-400:],
            "targets": TARGETS,
            "default_target": DEFAULT_TARGET,
        }


STATE = PanelState()


def _build_worker() -> None:
    def on_line(line: str) -> None:
        STATE.build["log"].append(line)
        if len(STATE.build["log"]) > 3000:
            del STATE.build["log"][:1000]

    try:
        build_binary(on_line=on_line)
        version, _ = binary_version()
        STATE.build.update(ok=True, version=version)
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        STATE.build["log"].append(f"[build error] {exc}")
        STATE.build.update(ok=False)
    finally:
        STATE.build["running"] = False
        STATE.version_cache["at"] = 0.0


def _content_disposition(name: str) -> str:
    ascii_name = name.encode("ascii", "ignore").decode("ascii") or "download.bin"
    encoded = urllib.parse.quote(name)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


def _clean_uploads() -> None:
    if not UPLOAD_ROOT.exists():
        return
    cutoff = time.time() - 24 * 3600
    for entry in UPLOAD_ROOT.iterdir():
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass


def _host_name(host_header: str) -> str:
    value = (host_header or "").strip().lower()
    if not value:
        return ""
    if value.startswith("["):
        return value.split("]")[0].lstrip("[")
    return value.split(":")[0]


def _is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return address in {"", "localhost"}


_TK_PICK_SCRIPT = r"""
import sys
mode, prompt, default_name = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    import tkinter as tk
    from tkinter import filedialog
except Exception as exc:
    print("TK_IMPORT_ERROR: %s" % exc, file=sys.stderr)
    raise SystemExit(2)
root = tk.Tk()
root.withdraw()
try:
    root.attributes("-topmost", True)
except Exception:
    pass
if mode == "folder":
    path = filedialog.askdirectory(title=prompt)
elif mode == "save":
    path = filedialog.asksaveasfilename(title=prompt, initialfile=default_name or "maidata.txt")
else:
    path = filedialog.askopenfilename(title=prompt)
print(path or "")
root.destroy()
"""


def _tk_pick(mode: str, prompt: str, default_name: str = "") -> str | None:
    """Open a native Tk dialog in a subprocess (Windows/Linux)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _TK_PICK_SCRIPT, mode, prompt, default_name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise MuConvertError("檔案選擇逾時，請改用拖放上傳。") from exc
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        low = err.lower()
        if (
            "tk_import_error" in low
            or "no module named" in low
            or "no display" in low
            or "tclerror" in low
            or "couldn't connect to display" in low
        ):
            raise MuConvertError(
                "此環境無法開啟系統檔案選擇視窗（缺少 tkinter 或 DISPLAY）；請使用拖放上傳。"
            )
        raise MuConvertError(err or "檔案選擇失敗；請使用拖放上傳。")
    return (proc.stdout or "").strip() or None


def _allowed_reveal_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for base in (ROOT.resolve(), Path.cwd().resolve(), UPLOAD_ROOT.resolve()):
        try:
            if resolved == base or resolved.is_relative_to(base):
                return True
        except (OSError, ValueError):
            pass
    if STATE.last_output_dir is not None:
        try:
            base = STATE.last_output_dir.resolve()
            if resolved == base or resolved.is_relative_to(base):
                return True
        except (OSError, ValueError):
            pass
    for registered in STATE.downloads.values():
        try:
            if resolved == registered.resolve():
                return True
        except OSError:
            continue
    return False


def _reveal_command(path: Path) -> list[str]:
    if sys.platform == "darwin":
        return ["open", "-R", str(path)]
    if sys.platform.startswith("win"):
        native = os.path.normpath(str(path))
        return ["explorer", f"/select,{native}"]
    return ["xdg-open", str(path.parent if path.is_file() else path)]


def _host_allowed(host_header: str) -> bool:
    name = _host_name(host_header)
    if not name:
        return False
    if name in {"localhost", "127.0.0.1", "::1"}:
        return True
    if STATE.allow_any_host:
        return True
    if name in STATE.allowed_hosts:
        return True
    if not STATE.allow_lan:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return name.endswith(".local")


def _lan_ipv4_addresses() -> list[str]:
    ips: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ips.add(sock.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    if STATE.bind_host not in {"0.0.0.0", "::", "127.0.0.1", "localhost"}:
        ips.add(STATE.bind_host)
    return sorted(
        ip
        for ip in ips
        if not ip.startswith("127.") and not ip.startswith("169.254.")
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MuConvertPanel"

    def log_message(self, fmt: str, *args) -> None:
        return

    # -- responses ---------------------------------------------------------

    def _send(self, body: bytes, content_type: str, status: int = 200, extra=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(data, "application/json; charset=utf-8", status)

    def _error(self, message, status: int = 400) -> None:
        self._json({"error": str(message)}, status)

    def _local_only(self) -> bool:
        if _host_allowed(self.headers.get("Host") or ""):
            return True
        self._error("forbidden", 403)
        return False

    def _authorized(self, allow_query: bool = False) -> bool:
        supplied = self.headers.get("X-MuConvert-Token") or ""
        if not supplied and allow_query:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            supplied = (qs.get("t") or [""])[0]
        if not hmac.compare_digest(supplied, STATE.token):
            self._error("forbidden", 403)
            return False
        return True

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > 2_000_000:
            raise ConversionError("request 太大")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConversionError("invalid JSON") from exc
        return value if isinstance(value, dict) else {}

    # -- HTTP --------------------------------------------------------------

    def do_GET(self) -> None:
        if not self._local_only():
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/":
                html = PAGE.replace("__MUCONVERT_TOKEN__", STATE.token).encode("utf-8")
                self._send(html, "text/html; charset=utf-8")
                return
            if path == "/api/status":
                if self._authorized():
                    payload = STATE.status()
                    payload["remote"] = not _is_loopback(self.client_address[0])
                    self._json(payload)
                return
            if path == "/api/build/status":
                if self._authorized():
                    self._json(
                        {
                            "running": STATE.build["running"],
                            "ok": STATE.build["ok"],
                            "version": STATE.build["version"],
                            "log": STATE.build["log"][-400:],
                        }
                    )
                return
            if path == "/api/download":
                if not self._authorized(allow_query=True):
                    return
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                file_id = (qs.get("id") or [""])[0]
                target = STATE.downloads.get(file_id)
                if target is None or not target.exists():
                    self._error("找不到檔案", 404)
                    return
                self._send(
                    target.read_bytes(),
                    "application/octet-stream",
                    extra={"Content-Disposition": _content_disposition(target.name)},
                )
                return
            self._error("not found", 404)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            self._error(f"{type(exc).__name__}: {exc}", 500)

    def do_POST(self) -> None:
        if not self._local_only():
            return
        if not self._authorized():
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/inspect":
                return self._handle_inspect()
            if path == "/api/pick":
                return self._handle_pick()
            if path == "/api/upload":
                return self._handle_upload()
            if path == "/api/upload/finish":
                return self._handle_upload_finish()
            if path == "/api/convert":
                return self._handle_convert()
            if path == "/api/reveal":
                return self._handle_reveal()
            if path == "/api/build":
                return self._handle_build()
            self._error("not found", 404)
        except MuConvertError as exc:
            self._error(str(exc), 400)
        except subprocess.TimeoutExpired:
            self._error("timeout", 504)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            self._error(f"{type(exc).__name__}: {exc}", 500)

    # -- handlers ----------------------------------------------------------

    def _handle_inspect(self) -> None:
        body = self._read_json()
        upload_id = str(body.get("upload_id") or "")
        if upload_id:
            root = STATE.uploads.get(upload_id)
            if root is None or not root.exists():
                raise ConversionError("上傳已過期，請重新拖放")
            info = inspect_path(root)
            info["upload_id"] = upload_id
            info["path"] = str(root)
        else:
            raw = str(body.get("path") or "").strip()
            if not raw:
                raise ConversionError("請先指定輸入路徑")
            target_path = Path(raw).expanduser()
            if not target_path.is_absolute():
                target_path = Path.cwd() / target_path
            info = inspect_path(target_path)
        self._json(info)

    def _handle_pick(self) -> None:
        if not _is_loopback(self.client_address[0]):
            self._error(
                "遠端連線不支援伺服器端檔案選擇，請使用拖放上傳。", 403
            )
            return

        body = self._read_json()
        mode = str(body.get("mode") or "file")
        prompt = str(
            body.get("title")
            or {
                "file": "選擇譜面檔",
                "folder": "選擇資料夾",
                "save": "輸出檔名",
            }.get(mode, "選擇")
        )
        default_name = str(body.get("name") or "maidata.txt")

        if sys.platform == "darwin":
            escaped_prompt = prompt.replace("\\", "\\\\").replace('"', '\\"')
            escaped_name = default_name.replace('"', "")
            if mode == "folder":
                script = f'POSIX path of (choose folder with prompt "{escaped_prompt}")'
            elif mode == "save":
                script = (
                    f'POSIX path of (choose file name with prompt "{escaped_prompt}" '
                    f'default name "{escaped_name}")'
                )
            else:
                script = f'POSIX path of (choose file with prompt "{escaped_prompt}")'

            proc = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True
            )
            if proc.returncode != 0:
                err = (proc.stderr or "").strip()
                if "-128" in err or "User canceled" in err:
                    self._json({"ok": False, "canceled": True})
                    return
                raise MuConvertError(err or "osascript 失敗")

            result = (proc.stdout or "").strip()
            if mode == "folder":
                result = result.rstrip("/")
            self._json({"ok": True, "path": result})
            return

        result = _tk_pick(mode, prompt, default_name)
        if not result:
            self._json({"ok": False, "canceled": True})
            return
        self._json({"ok": True, "path": result})

    def _handle_upload(self) -> None:
        upload_id = self.headers.get("X-Upload-Id") or ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,80}", upload_id):
            raise ConversionError("invalid upload id")

        name = urllib.parse.unquote(self.headers.get("X-Filename") or "")
        safe = Path(name).name
        if not safe or safe in (".", "..") or safe.startswith("."):
            raise ConversionError("invalid filename")

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            raise ConversionError("empty upload")
        if length > 64 * 1024 * 1024:
            raise ConversionError("檔案超過 64 MB")

        data = self.rfile.read(length)
        folder = UPLOAD_ROOT / upload_id
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / safe
        if target.exists():
            target = folder / f"{target.stem}_{uuid.uuid4().hex[:6]}{target.suffix}"
        target.write_bytes(data)
        STATE.uploads[upload_id] = folder
        self._json({"ok": True, "name": target.name, "size": len(data)})

    def _handle_upload_finish(self) -> None:
        body = self._read_json()
        upload_id = str(body.get("upload_id") or "")
        folder = STATE.uploads.get(upload_id)
        if folder is None or not folder.exists():
            raise ConversionError("上傳已過期，請重新拖放")
        info = inspect_path(folder)
        info["upload_id"] = upload_id
        info["path"] = str(folder)
        self._json(info)

    def _handle_convert(self) -> None:
        body = self._read_json()
        preview = bool(body.get("preview"))
        package = bool(body.get("package"))
        levels = body.get("levels") or []
        target = body.get("target") or None
        strict = str(body.get("strict") or "normal").lower()
        upload_id = str(body.get("upload_id") or "")

        if upload_id:
            root = STATE.uploads.get(upload_id)
            if root is None or not root.exists():
                raise ConversionError("上傳已過期，請重新拖放")
            inp = root
            output = "-" if preview else str(root / "out")
        else:
            raw = str(body.get("input") or "").strip()
            if not raw:
                raise ConversionError("請先指定輸入路徑")
            inp = Path(raw).expanduser()
            if not inp.is_absolute():
                inp = Path.cwd() / inp
            raw_output = str(body.get("output") or "out").strip() or "out"
            out_path = Path(raw_output).expanduser()
            if not out_path.is_absolute():
                out_path = Path.cwd() / out_path
            output = "-" if preview else str(out_path)

        if preview:
            info = inspect_path(inp)
            try:
                effective = [int(x) for x in levels] if levels else info["levels"]
            except (TypeError, ValueError):
                effective = info["levels"]
            if info["kind"] == "simai" and len(effective) > 1:
                raise ConversionError("預覽模式一次只能輸出一個難度，請只勾選一個")

        with STATE.lock:
            result = run_convert(
                inp,
                levels=levels,
                target=target,
                output=output,
                strict=strict,
                package=package,
            )

        payload = result.to_dict()
        files = []

        def add_file(path: Path, kind: str) -> None:
            file_id = uuid.uuid4().hex
            STATE.downloads[file_id] = path
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            files.append(
                {
                    "id": file_id,
                    "path": str(path),
                    "name": path.name,
                    "size": size,
                    "kind": kind,
                }
            )

        for path in result.outputs:
            add_file(path, "chart")
        for path in result.copied:
            add_file(path, "companion")
        if result.manifest is not None:
            add_file(result.manifest, "manifest")

        if len(STATE.downloads) > 500:
            for key in list(STATE.downloads)[:100]:
                STATE.downloads.pop(key, None)

        payload["files"] = files
        payload["package"] = {
            "enabled": package,
            "copied": len(result.copied),
            "manifest": str(result.manifest) if result.manifest else None,
        }
        payload["input"] = str(inp)
        payload["output_dir"] = None
        if result.ok and not preview and output and output != "-":
            out_path = Path(output).expanduser()
            if out_path.is_dir() or not out_path.suffix:
                out_dir = out_path.resolve()
            else:
                out_dir = out_path.parent.resolve()
            payload["output_dir"] = str(out_dir)
            STATE.last_output_dir = out_dir
        self._json(payload)

    def _handle_reveal(self) -> None:
        if not _is_loopback(self.client_address[0]):
            self._error(
                "遠端連線不支援開啟伺服器端資料夾，請使用下載。", 403
            )
            return

        body = self._read_json()
        file_id = str(body.get("id") or "")
        target = STATE.downloads.get(file_id)
        if target is None:
            raw = str(body.get("path") or "").strip()
            if not raw:
                raise ConversionError("找不到檔案")
            target = Path(raw).expanduser()
        if not target.exists():
            raise ConversionError("找不到檔案")
        resolved = target.resolve()
        if not _allowed_reveal_path(resolved):
            raise ConversionError("不允許的路徑")

        cmd = _reveal_command(resolved)
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise MuConvertError(
                f"找不到 {cmd[0]}，請手動開啟資料夾。"
            ) from exc
        self._json({"ok": True, "path": str(resolved)})

    def _handle_build(self) -> None:
        with STATE.build_lock:
            if not STATE.build["running"]:
                STATE.build = {"running": True, "ok": None, "log": [], "version": None}
                threading.Thread(target=_build_worker, daemon=True).start()
        self._json({"running": True})


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    allow_hosts: list[str] | None = None,
    allow_any_host: bool = False,
) -> int:
    STATE.bind_host = (host or "127.0.0.1").strip()
    STATE.allow_lan = STATE.bind_host not in {"127.0.0.1", "localhost", "::1"}
    STATE.allow_any_host = allow_any_host
    STATE.allowed_hosts = {
        name for name in (_host_name(h) for h in (allow_hosts or [])) if name
    }
    STATE.last_output_dir = None

    _clean_uploads()
    httpd = None
    chosen = port
    last_error: Exception | None = None
    for candidate in range(port, port + 10):
        try:
            httpd = ThreadingHTTPServer((STATE.bind_host, candidate), Handler)
            chosen = candidate
            break
        except OSError as exc:
            last_error = exc
    if httpd is None:
        print(
            f"無法綁定 {STATE.bind_host}:{port}..{port + 9}：{last_error}",
            file=sys.stderr,
        )
        return 1

    if STATE.bind_host in {"0.0.0.0", "::"}:
        local_url = f"http://127.0.0.1:{chosen}/"
    else:
        local_url = f"http://{STATE.bind_host}:{chosen}/"

    print(f"MuConvert Panel: {local_url}", flush=True)
    if STATE.allow_lan:
        lan_ips = _lan_ipv4_addresses()
        if lan_ips:
            for ip in lan_ips:
                print(f"LAN:             http://{ip}:{chosen}/", flush=True)
        else:
            print(
                "LAN:             找不到 LAN IPv4，請確認 Wi-Fi / Ethernet 已連線",
                flush=True,
            )
        print(
            "WARNING: LAN mode 沒有 TLS；同網段能連到這個 port 的人都可以使用 panel。",
            flush=True,
        )
        if STATE.allow_any_host:
            print(
                "WARNING: --allow-any-host 已開啟，Host header 不設白名單。",
                flush=True,
            )
    print(f"Cache:           {CACHE_ROOT}", flush=True)
    print("Ctrl+C 停止", flush=True)

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(local_url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
