#!/usr/bin/env python3
import argparse
import json
import math
import shutil
import shlex
import subprocess
import sys
from pathlib import Path

try:
    from .eval_qwen3vl_video import apply_team_context, prepare_env, stream_once
except ImportError:
    from eval_qwen3vl_video import apply_team_context, prepare_env, stream_once


DEFAULT_CAPTION_PROMPT = (
    "你是一名足球比赛中文解说字幕生成器。"
    "请只描述这 5 秒比赛视频里能看见的动作，输出 1 到 2 句简短中文解说。"
    "优先描述控球、推进、传中、射门、扑救、解围、犯规、角球、任意球、界外球、庆祝或回放。"
    "如果提供了队伍名称、缩写或队服颜色，必须优先用这些线索判断球队。"
    "能确认其中一队时，直接说队名或缩写；不要把已知球队泛称为“持球队员”。"
    "只有完全看不出是哪一队时，才使用“进攻方”“防守方”“门将”等中性称呼。"
    "如果画面主要是慢镜头、庆祝、转播镜头或信息不足，也要直接说明。"
    "不要输出标题、编号、时间码、分析过程、换行或额外说明。"
)

# 允许处理的常见视频格式；当前 pipeline 按单个长视频文件处理。
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi"}


def pick_binary(name):
    # 优先用系统 ffmpeg，当前 conda 版本在本机存在编码器兼容问题。
    for path in (f"/usr/bin/{name}", f"/bin/{name}", shutil.which(name)):
        if path and Path(path).exists():
            return path
    return name


FFMPEG = pick_binary("ffmpeg")
FFPROBE = pick_binary("ffprobe")


def ffmpeg_encoders():
    # 根据本机 ffmpeg 能力选择编码器，避免写死某个环境才支持的参数。
    return subprocess.check_output(
        [FFMPEG, "-hide_banner", "-encoders"], stderr=subprocess.DEVNULL, text=True
    )


def video_encode_args(bitrate="1200k"):
    encoders = ffmpeg_encoders()
    if "libx264" in encoders:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    if "libopenh264" in encoders:
        return ["-c:v", "libopenh264", "-b:v", bitrate, "-pix_fmt", "yuv420p"]
    return ["-c:v", "mpeg4", "-q:v", "5", "-pix_fmt", "yuv420p"]


def run(cmd):
    print("$ " + " ".join(shlex.quote(str(x)) for x in cmd), flush=True)
    subprocess.run([str(x) for x in cmd], check=True)


def ffprobe_duration(path):
    out = subprocess.check_output(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(path),
        ],
        text=True,
    )
    return float(out.strip())


def default_out_dir(video_path):
    return video_path.parent / f"{video_path.stem}_subtitle_slices"


def validate_video_path(path_str):
    path = Path(path_str).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
        raise SystemExit(f"input must be a video file: {path}")
    return path


def segment_times(duration, slice_seconds, min_tail_seconds):
    # 只在这里计算时间轴：后续切片、JSON、SRT 都复用这份浮点时间，避免字幕漂移。
    # 如果最后剩一个极短尾片，就并入上一片，避免 VideoLLM 读不到有效画面。
    segments = []
    start_time = 0.0
    while start_time < duration - 1e-6:
        end_time = min(start_time + slice_seconds, duration)
        tail = duration - end_time
        if 0 < tail < min_tail_seconds:
            end_time = duration
        segments.append((round(start_time, 6), round(end_time, 6)))
        start_time = end_time
    return segments


def slice_video(video_path, slices_dir, metadata_path, slice_seconds, min_tail_seconds, force):
    # 切片阶段：把长视频切成独立 mp4，并同步写出每个切片对应的原视频时间。
    # 输出的 slice_times.json 是后面生成 captions.json 和 srt 的唯一时间来源。
    duration = round(ffprobe_duration(video_path), 6)
    slices_dir.mkdir(parents=True, exist_ok=True)

    if force:
        for old_slice in slices_dir.glob("*.mp4"):
            old_slice.unlink()

    segments = []
    for index, (start_time, end_time) in enumerate(segment_times(duration, slice_seconds, min_tail_seconds)):
        slice_path = slices_dir / f"slice_{index:04d}.mp4"
        segment_duration = round(end_time - start_time, 6)
        if force or not slice_path.exists():
            run(
                [
                    FFMPEG,
                    "-y",
                    "-ss",
                    f"{start_time:.6f}",
                    "-i",
                    video_path,
                    "-t",
                    f"{segment_duration:.6f}",
                    "-map",
                    "0:v:0",
                    "-an",
                    *video_encode_args("1200k"),
                    slice_path,
                ]
            )

        segments.append(
            {
                "index": index,
                "start_time": float(start_time),
                "end_time": float(end_time),
                "video": str(slice_path),
            }
        )

    metadata = {
        "source_video": str(video_path),
        "duration": float(duration),
        "slice_seconds": float(slice_seconds),
        "segments": segments,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_caption(text):
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


def load_existing_captions(path):
    # 支持断点续跑：已有字幕会直接复用，避免长视频中途失败后从头请求模型。
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {int(item["index"]): item for item in data.get("segments", [])}


def write_caption_json(path, source_video, segments):
    data = {"source_video": str(source_video), "segments": segments}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def generate_captions(
    slice_metadata_path,
    captions_path,
    base_url,
    model,
    prompt,
    sample_fps,
    max_tokens,
    temperature,
    timeout,
    min_pixels,
    max_pixels,
    force,
):
    # 字幕生成阶段：逐个读取切片视频，调用 eval_qwen3vl_video.py 中已有的 VideoLLM 请求函数。
    # 每完成一条就落盘 captions.json，长视频处理时更安全，也便于观察进度。
    metadata = json.loads(slice_metadata_path.read_text(encoding="utf-8"))
    existing = {} if force else load_existing_captions(captions_path)
    caption_segments = []

    for segment in metadata["segments"]:
        index = int(segment["index"])
        if index in existing and existing[index].get("description"):
            caption_segments.append(existing[index])
            continue

        print(f"caption {index + 1}/{len(metadata['segments'])}: {segment['video']}", flush=True)
        result = stream_once(
            base_url,
            model,
            segment["video"],
            prompt,
            sample_fps,
            max_tokens,
            temperature,
            timeout,
            min_pixels,
            max_pixels,
        )
        caption = {
            "index": index,
            "start_time": float(segment["start_time"]),
            "end_time": float(segment["end_time"]),
            "description": clean_caption(result.get("response", "")),
        }
        caption_segments.append(caption)
        write_caption_json(captions_path, metadata["source_video"], caption_segments)

    caption_segments.sort(key=lambda item: item["index"])
    write_caption_json(captions_path, metadata["source_video"], caption_segments)


def srt_time(seconds):
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_seconds = total_ms // 1000
    sec = total_seconds % 60
    total_minutes = total_seconds // 60
    minute = total_minutes % 60
    hour = total_minutes // 60
    return f"{hour:02d}:{minute:02d}:{sec:02d},{ms:03d}"


def write_srt(captions_path, srt_path):
    # SRT 阶段：只做格式转换，不重新计算时间，保证和 slice_times.json/captions.json 一致。
    data = json.loads(captions_path.read_text(encoding="utf-8"))
    lines = []
    srt_index = 1
    for segment in sorted(data["segments"], key=lambda item: item["index"]):
        text = segment.get("description", "").strip()
        if not text:
            continue
        lines.extend(
            [
                str(srt_index),
                f"{srt_time(segment['start_time'])} --> {srt_time(segment['end_time'])}",
                text,
                "",
            ]
        )
        srt_index += 1
    srt_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser(
        description="输入一个长视频，按 5 秒切片，用 Qwen3-VL 生成字幕 JSON 和 SRT。"
    )
    parser.add_argument("--input", required=True, help="输入长视频文件，建议放在 /data/yjc/video 下")
    parser.add_argument("--out-dir", help="输出目录；默认在输入视频旁边创建 <视频名>_subtitle_slices")
    parser.add_argument("--slice-seconds", type=float, default=5.0)
    parser.add_argument("--min-tail-seconds", type=float, default=0.3, help="小于该时长的尾巴会并入上一片")
    parser.add_argument("--sample-fps", type=float, default=1.0, help="送入 VideoLLM 的视频采样 fps")
    parser.add_argument("--base-url", default="http://127.0.0.1:18008/v1")
    parser.add_argument("--model", default="qwen3-vl-8b-instruct")
    parser.add_argument("--min-pixels", type=int)
    parser.add_argument("--max-pixels", type=int, default=151200)
    parser.add_argument("--max-tokens", type=int, default=96)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--prompt-file", help="可选：自定义字幕生成 prompt")
    parser.add_argument("--team-info", default="", help="队伍线索，例如 Miami/MIA 粉红色；Philadelphia/PHI 黑色")
    parser.add_argument("--skip-captions", action="store_true", help="只切片并生成 slice_times.json")
    parser.add_argument("--force", action="store_true", help="覆盖已有切片和字幕结果")
    return parser.parse_args()


def main():
    # 总 pipeline：
    # 1. 读取一个长视频输入
    # 2. 按固定间隔切成独立视频片段，并记录浮点时间轴
    # 3. 对每个切片调用 VideoLLM 生成描述文本，写入 captions.json
    # 4. 基于同一份 JSON 导出 srt 字幕文件
    args = parse_args()
    prepare_env()

    source_video = validate_video_path(args.input)
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else default_out_dir(source_video)
    slices_dir = out_dir / "slices_5s"
    slice_metadata = out_dir / "slice_times.json"
    captions_json = out_dir / "captions.json"
    srt_path = out_dir / f"{source_video.stem}.srt"
    out_dir.mkdir(parents=True, exist_ok=True)

    slice_video(source_video, slices_dir, slice_metadata, args.slice_seconds, args.min_tail_seconds, args.force)

    if not args.skip_captions:
        prompt = DEFAULT_CAPTION_PROMPT
        if args.prompt_file:
            prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8").strip()
        prompt = apply_team_context(prompt, args.team_info)

        generate_captions(
            slice_metadata,
            captions_json,
            args.base_url,
            args.model,
            prompt,
            args.sample_fps,
            args.max_tokens,
            args.temperature,
            args.timeout,
            args.min_pixels,
            args.max_pixels,
            args.force,
        )
        write_srt(captions_json, srt_path)

    print(
        json.dumps(
            {
                "source_video": str(source_video),
                "sliced_videos": str(slices_dir),
                "slice_times": str(slice_metadata),
                "captions_json": None if args.skip_captions else str(captions_json),
                "srt": None if args.skip_captions else str(srt_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"command failed with code {exc.returncode}", file=sys.stderr)
        raise
