import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from getpass import getpass
from pathlib import Path
from typing import List

from openai import OpenAI


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def load_saved_api_key(config_path: Path) -> str | None:
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    key = data.get("openai_api_key")
    if isinstance(key, str) and key.strip():
        return key.strip()
    return None


def save_api_key(config_path: Path, api_key: str) -> None:
    config_path.write_text(
        json.dumps({"openai_api_key": api_key}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def resolve_api_key(cli_key: str | None, save_key: bool) -> str | None:
    if cli_key:
        key = cli_key.strip()
        if key and save_key:
            save_api_key(get_app_dir() / "config.json", key)
        return key

    env_key = os.getenv("OPENAI_API_KEY")
    if env_key and env_key.strip():
        return env_key.strip()

    config_path = get_app_dir() / "config.json"
    saved_key = load_saved_api_key(config_path)
    if saved_key:
        return saved_key

    if not sys.stdin or not sys.stdin.isatty():
        return None

    print("未检测到 API Key，请输入 OpenAI API Key（输入时不回显）：")
    typed_key = getpass("API Key: ").strip()
    if not typed_key:
        return None

    save_choice = input("是否保存到 exe 同目录配置文件（config.json）？[Y/n]: ").strip().lower()
    should_save = save_choice in ("", "y", "yes")
    if should_save:
        save_api_key(config_path, typed_key)
    return typed_key


def get_desktop_path() -> Path:
    home = Path.home()
    desktop = home / "Desktop"
    if desktop.exists():
        return desktop

    # Some non-English Windows systems use localized desktop folders.
    one_drive_desktop = home / "OneDrive" / "Desktop"
    if one_drive_desktop.exists():
        return one_drive_desktop

    raise FileNotFoundError("未找到桌面目录，请使用 --input-dir 手动指定目录。")


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
) -> Path:
    print(f"\n[处理] {audio_path.name}")
    with tempfile.TemporaryDirectory(prefix="audio_chunks_") as tmp:
        tmp_dir = Path(tmp)
        chunks = run_ffmpeg_split(ffmpeg_bin, audio_path, tmp_dir, segment_seconds)
        print(f"  已切分 {len(chunks)} 段")

        merged_texts: List[str] = []
        for idx, chunk in enumerate(chunks, start=1):
            print(f"  转录分段 {idx}/{len(chunks)}: {chunk.name}")
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
        print(f"  输出完成: {out_file}")

        if keep_chunks:
            chunk_keep_dir = output_dir / f"{audio_path.stem}_chunks"
            chunk_keep_dir.mkdir(parents=True, exist_ok=True)
            for chunk in chunks:
                shutil.copy2(chunk, chunk_keep_dir / chunk.name)
            print(f"  已保留分段文件到: {chunk_keep_dir}")

        return out_file


def main() -> int:
    parser = argparse.ArgumentParser(description="批量转录桌面 m4a 音频到文本（OpenAI API）")
    parser.add_argument("--input-dir", type=Path, help="音频目录，默认桌面")
    parser.add_argument("--output-dir", type=Path, help="输出目录，默认为输入目录/transcripts")
    parser.add_argument("--model", default="gpt-4o-mini-transcribe", help="转录模型")
    parser.add_argument("--language", default=None, help="指定语言，例如 zh")
    parser.add_argument("--segment-seconds", type=int, default=600, help="每段时长（秒）")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 可执行文件名或路径")
    parser.add_argument("--retries", type=int, default=3, help="单段失败重试次数")
    parser.add_argument("--retry-sleep", type=float, default=1.5, help="重试基础等待秒数")
    parser.add_argument("--keep-chunks", action="store_true", help="保留切分片段")
    parser.add_argument("--api-key", default=None, help="直接传入 OpenAI API Key")
    parser.add_argument("--save-api-key", action="store_true", help="与 --api-key 同用，保存到 config.json")

    args = parser.parse_args()

    api_key = resolve_api_key(args.api_key, args.save_api_key)
    if not api_key:
        print("错误: 未提供 OpenAI API Key（可通过 --api-key、OPENAI_API_KEY 或首次运行时输入）。", file=sys.stderr)
        return 2

    try:
        input_dir = args.input_dir if args.input_dir else get_desktop_path()
        if not input_dir.exists():
            print(f"错误: 输入目录不存在: {input_dir}", file=sys.stderr)
            return 2

        output_dir = args.output_dir if args.output_dir else input_dir / "transcripts"

        ensure_ffmpeg(args.ffmpeg)

        audio_files = sorted(input_dir.glob("*.m4a"))
        if not audio_files:
            print(f"未在目录中找到 .m4a 文件: {input_dir}")
            return 0

        client = OpenAI(api_key=api_key)

        ok = 0
        failed = 0
        for audio in audio_files:
            try:
                transcribe_file(
                    client=client,
                    ffmpeg_bin=args.ffmpeg,
                    audio_path=audio,
                    output_dir=output_dir,
                    model=args.model,
                    language=args.language,
                    segment_seconds=args.segment_seconds,
                    retries=args.retries,
                    sleep_seconds=args.retry_sleep,
                    keep_chunks=args.keep_chunks,
                )
                ok += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"[失败] {audio.name}: {exc}", file=sys.stderr)

        print(f"\n完成：成功 {ok} 个，失败 {failed} 个。")
        return 1 if failed else 0

    except Exception as exc:  # noqa: BLE001
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
