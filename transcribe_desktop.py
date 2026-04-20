import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, List

from openai import OpenAI

DEFAULT_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_API_BASE = "https://api.openai.com/v1"


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_config_path() -> Path:
    return get_app_dir() / "config.json"


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(config_path: Path, cfg: dict) -> None:
    config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_desktop_path() -> Path:
    home = Path.home()
    desktop = home / "Desktop"
    if desktop.exists():
        return desktop

    one_drive_desktop = home / "OneDrive" / "Desktop"
    if one_drive_desktop.exists():
        return one_drive_desktop

    raise FileNotFoundError("未找到桌面目录，请手动指定输入目录。")


def ensure_ffmpeg(ffmpeg_bin: str) -> None:
    if shutil.which(ffmpeg_bin):
        return
    raise FileNotFoundError(
        f"找不到 {ffmpeg_bin}，请先安装 ffmpeg 并加入 PATH，或把 ffmpeg.exe 放在程序同目录。"
    )


def run_ffmpeg_split(ffmpeg_bin: str, input_file: Path, out_dir: Path, segment_seconds: int) -> List[Path]:
    out_pattern = out_dir / "chunk_%04d.m4a"
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_file),
        "-f",
        "segment",
        "-segment_time",
        str(segment_seconds),
        "-c",
        "copy",
        str(out_pattern),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 切分失败: {result.stderr.strip() or result.stdout.strip()}")

    chunks = sorted(out_dir.glob("chunk_*.m4a"))
    if not chunks:
        raise RuntimeError("ffmpeg 未生成分段文件。")
    return chunks


def transcribe_chunk(
    client: OpenAI,
    chunk_path: Path,
    model: str,
    language: str | None,
    retries: int,
    sleep_seconds: float,
) -> str:
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            with chunk_path.open("rb") as f:
                kwargs = {
                    "model": model,
                    "file": f,
                    "response_format": "text",
                }
                if language:
                    kwargs["language"] = language
                return client.audio.transcriptions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                time.sleep(sleep_seconds * attempt)

    raise RuntimeError(f"转录失败（{chunk_path.name}）: {last_error}")


def transcribe_file(
    client: OpenAI,
    ffmpeg_bin: str,
    audio_path: Path,
    output_dir: Path,
    model: str,
    language: str | None,
    segment_seconds: int,
    retries: int,
    sleep_seconds: float,
    keep_chunks: bool,
    logger: Callable[[str], None],
) -> Path:
    logger(f"\n[处理] {audio_path.name}")
    with tempfile.TemporaryDirectory(prefix="audio_chunks_") as tmp:
        tmp_dir = Path(tmp)
        chunks = run_ffmpeg_split(ffmpeg_bin, audio_path, tmp_dir, segment_seconds)
        logger(f"  已切分 {len(chunks)} 段")

        merged_texts: List[str] = []
        for idx, chunk in enumerate(chunks, start=1):
            logger(f"  转录分段 {idx}/{len(chunks)}: {chunk.name}")
            text = transcribe_chunk(
                client=client,
                chunk_path=chunk,
                model=model,
                language=language,
                retries=retries,
                sleep_seconds=sleep_seconds,
            )
            merged_texts.append(text.strip())

        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{audio_path.stem}.txt"
        out_file.write_text("\n\n".join(merged_texts), encoding="utf-8")
        logger(f"  输出完成: {out_file}")

        if keep_chunks:
            chunk_keep_dir = output_dir / f"{audio_path.stem}_chunks"
            chunk_keep_dir.mkdir(parents=True, exist_ok=True)
            for chunk in chunks:
                shutil.copy2(chunk, chunk_keep_dir / chunk.name)
            logger(f"  已保留分段文件到: {chunk_keep_dir}")

        return out_file


def transcribe_files(
    audio_files: List[Path],
    output_dir: Path,
    api_key: str,
    api_base: str,
    model: str,
    language: str | None,
    segment_seconds: int,
    ffmpeg_bin: str,
    retries: int,
    retry_sleep: float,
    keep_chunks: bool,
    logger: Callable[[str], None],
) -> int:
    ensure_ffmpeg(ffmpeg_bin)

    client = OpenAI(api_key=api_key, base_url=api_base)
    ok = 0
    failed = 0
    for audio in audio_files:
        try:
            transcribe_file(
                client=client,
                ffmpeg_bin=ffmpeg_bin,
                audio_path=audio,
                output_dir=output_dir,
                model=model,
                language=language,
                segment_seconds=segment_seconds,
                retries=retries,
                sleep_seconds=retry_sleep,
                keep_chunks=keep_chunks,
                logger=logger,
            )
            ok += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger(f"[失败] {audio.name}: {exc}")

    logger(f"\n完成：成功 {ok} 个，失败 {failed} 个。")
    return 1 if failed else 0


def run_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except Exception as exc:  # noqa: BLE001
        print(f"GUI 启动失败，请改用命令行参数运行。错误: {exc}", file=sys.stderr)
        return 2

    cfg_path = get_config_path()
    saved = load_config(cfg_path)

    root = tk.Tk()
    root.title("Audio Transcriber")
    root.geometry("840x560")

    selected_files: list[Path] = []

    def add_log(msg: str) -> None:
        log_text.configure(state="normal")
        log_text.insert("end", msg + "\n")
        log_text.see("end")
        log_text.configure(state="disabled")
        root.update_idletasks()

    def choose_files() -> None:
        files = filedialog.askopenfilenames(
            title="选择音频文件",
            filetypes=[("Audio Files", "*.m4a *.mp3 *.wav *.flac"), ("All Files", "*.*")],
        )
        if not files:
            return
        selected_files.clear()
        selected_files.extend(Path(f) for f in files)
        files_var.set("; ".join(str(p) for p in selected_files))

    def choose_output_dir() -> None:
        folder = filedialog.askdirectory(title="选择输出目录")
        if folder:
            output_var.set(folder)

    def save_user_config() -> None:
        cfg = {
            "api_base": api_base_var.get().strip(),
            "api_key": api_key_var.get().strip(),
            "model": model_var.get().strip(),
            "language": language_var.get().strip(),
            "segment_seconds": segment_var.get().strip(),
            "ffmpeg": ffmpeg_var.get().strip(),
            "retries": retries_var.get().strip(),
            "retry_sleep": retry_sleep_var.get().strip(),
            "keep_chunks": keep_chunks_var.get(),
            "output_dir": output_var.get().strip(),
        }
        save_config(cfg_path, cfg)

    def start_transcribe() -> None:
        if not selected_files:
            messagebox.showerror("错误", "请先选择至少一个音频文件。")
            return

        output_dir_raw = output_var.get().strip()
        if not output_dir_raw:
            messagebox.showerror("错误", "请先选择输出目录。")
            return

        api_key = api_key_var.get().strip()
        if not api_key:
            messagebox.showerror("错误", "请填写 API Key。")
            return

        api_base = api_base_var.get().strip()
        if not api_base:
            messagebox.showerror("错误", "请填写 API 地址（Base URL）。")
            return

        model = model_var.get().strip() or DEFAULT_MODEL

        try:
            segment_seconds = int(segment_var.get().strip())
            retries = int(retries_var.get().strip())
            retry_sleep = float(retry_sleep_var.get().strip())
        except ValueError:
            messagebox.showerror("错误", "切分秒数/重试次数/重试等待必须是数字。")
            return

        language = language_var.get().strip() or None
        ffmpeg_bin = ffmpeg_var.get().strip() or "ffmpeg"
        keep_chunks = bool(keep_chunks_var.get())

        run_btn.config(state="disabled")
        try:
            save_user_config()
            code = transcribe_files(
                audio_files=selected_files,
                output_dir=Path(output_dir_raw),
                api_key=api_key,
                api_base=api_base,
                model=model,
                language=language,
                segment_seconds=segment_seconds,
                ffmpeg_bin=ffmpeg_bin,
                retries=retries,
                retry_sleep=retry_sleep,
                keep_chunks=keep_chunks,
                logger=add_log,
            )
            if code == 0:
                messagebox.showinfo("完成", "全部文件转录完成。")
            else:
                messagebox.showwarning("完成", "已完成，但有部分文件失败，请查看日志。")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("失败", str(exc))
        finally:
            run_btn.config(state="normal")

    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)

    files_var = tk.StringVar()
    output_var = tk.StringVar(value=saved.get("output_dir", str(get_desktop_path() / "transcripts")))
    api_base_var = tk.StringVar(value=saved.get("api_base", DEFAULT_API_BASE))
    api_key_var = tk.StringVar(value=saved.get("api_key", ""))
    model_var = tk.StringVar(value=saved.get("model", DEFAULT_MODEL))
    language_var = tk.StringVar(value=saved.get("language", ""))
    segment_var = tk.StringVar(value=str(saved.get("segment_seconds", "600")))
    ffmpeg_var = tk.StringVar(value=saved.get("ffmpeg", "ffmpeg"))
    retries_var = tk.StringVar(value=str(saved.get("retries", "3")))
    retry_sleep_var = tk.StringVar(value=str(saved.get("retry_sleep", "1.5")))
    keep_chunks_var = tk.BooleanVar(value=bool(saved.get("keep_chunks", False)))

    row = 0
    ttk.Label(frm, text="目标音频文件").grid(row=row, column=0, sticky="w")
    ttk.Entry(frm, textvariable=files_var, width=80).grid(row=row, column=1, sticky="ew", padx=6)
    ttk.Button(frm, text="选择文件", command=choose_files).grid(row=row, column=2, sticky="ew")

    row += 1
    ttk.Label(frm, text="输出目录").grid(row=row, column=0, sticky="w")
    ttk.Entry(frm, textvariable=output_var, width=80).grid(row=row, column=1, sticky="ew", padx=6)
    ttk.Button(frm, text="选择目录", command=choose_output_dir).grid(row=row, column=2, sticky="ew")

    row += 1
    ttk.Label(frm, text="API 地址(Base URL)").grid(row=row, column=0, sticky="w")
    ttk.Entry(frm, textvariable=api_base_var, width=80).grid(row=row, column=1, sticky="ew", padx=6, columnspan=2)

    row += 1
    ttk.Label(frm, text="API Key").grid(row=row, column=0, sticky="w")
    ttk.Entry(frm, textvariable=api_key_var, show="*", width=80).grid(row=row, column=1, sticky="ew", padx=6, columnspan=2)

    row += 1
    ttk.Label(frm, text="模型").grid(row=row, column=0, sticky="w")
    ttk.Entry(frm, textvariable=model_var, width=24).grid(row=row, column=1, sticky="w", padx=6)
    ttk.Label(frm, text="语言(可空，如 zh)").grid(row=row, column=1, sticky="e")
    ttk.Entry(frm, textvariable=language_var, width=16).grid(row=row, column=2, sticky="ew")

    row += 1
    ttk.Label(frm, text="切分秒数").grid(row=row, column=0, sticky="w")
    ttk.Entry(frm, textvariable=segment_var, width=10).grid(row=row, column=1, sticky="w", padx=6)
    ttk.Label(frm, text="ffmpeg").grid(row=row, column=1, sticky="e")
    ttk.Entry(frm, textvariable=ffmpeg_var, width=16).grid(row=row, column=2, sticky="ew")

    row += 1
    ttk.Label(frm, text="重试次数").grid(row=row, column=0, sticky="w")
    ttk.Entry(frm, textvariable=retries_var, width=10).grid(row=row, column=1, sticky="w", padx=6)
    ttk.Label(frm, text="重试等待(秒)").grid(row=row, column=1, sticky="e")
    ttk.Entry(frm, textvariable=retry_sleep_var, width=16).grid(row=row, column=2, sticky="ew")

    row += 1
    ttk.Checkbutton(frm, text="保留切分片段", variable=keep_chunks_var).grid(row=row, column=0, sticky="w")
    run_btn = ttk.Button(frm, text="开始转录", command=start_transcribe)
    run_btn.grid(row=row, column=2, sticky="ew")

    row += 1
    ttk.Label(frm, text="日志").grid(row=row, column=0, sticky="w", pady=(10, 0))

    row += 1
    log_text = tk.Text(frm, height=14, state="disabled")
    log_text.grid(row=row, column=0, columnspan=3, sticky="nsew")

    frm.columnconfigure(1, weight=1)
    frm.rowconfigure(row, weight=1)

    root.mainloop()
    return 0


def run_cli(args: argparse.Namespace) -> int:
    api_key = args.api_key.strip() if args.api_key else os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("错误: CLI 模式请通过 --api-key 或 OPENAI_API_KEY 提供 API Key。", file=sys.stderr)
        return 2

    input_dir = args.input_dir if args.input_dir else get_desktop_path()
    if not input_dir.exists():
        print(f"错误: 输入目录不存在: {input_dir}", file=sys.stderr)
        return 2

    output_dir = args.output_dir if args.output_dir else input_dir / "transcripts"
    audio_files = sorted(input_dir.glob("*.m4a"))
    if not audio_files:
        print(f"未在目录中找到 .m4a 文件: {input_dir}")
        return 0

    return transcribe_files(
        audio_files=audio_files,
        output_dir=output_dir,
        api_key=api_key,
        api_base=args.api_base,
        model=args.model,
        language=args.language,
        segment_seconds=args.segment_seconds,
        ffmpeg_bin=args.ffmpeg,
        retries=args.retries,
        retry_sleep=args.retry_sleep,
        keep_chunks=args.keep_chunks,
        logger=print,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="批量转录音频到文本（OpenAI API）")
    parser.add_argument("--input-dir", type=Path, help="CLI 模式音频目录，默认桌面")
    parser.add_argument("--output-dir", type=Path, help="CLI 模式输出目录，默认输入目录/transcripts")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="转录模型")
    parser.add_argument("--language", default=None, help="指定语言，例如 zh")
    parser.add_argument("--segment-seconds", type=int, default=600, help="每段时长（秒）")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 可执行文件名或路径")
    parser.add_argument("--retries", type=int, default=3, help="单段失败重试次数")
    parser.add_argument("--retry-sleep", type=float, default=1.5, help="重试基础等待秒数")
    parser.add_argument("--keep-chunks", action="store_true", help="保留切分片段")
    parser.add_argument("--api-key", default=None, help="OpenAI API Key")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="API Base URL")
    parser.add_argument("--cli", action="store_true", help="强制使用命令行模式")

    args = parser.parse_args()

    if args.cli:
        return run_cli(args)

    # exe 双击默认打开 GUI
    if len(sys.argv) == 1:
        return run_gui()

    # 传了参数但没 --cli 时，仍按 CLI 跑，兼容旧行为
    return run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
