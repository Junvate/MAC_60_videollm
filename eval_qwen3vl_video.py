#!/usr/bin/env python3
import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import requests


DEFAULT_PROMPT = (
    "你是一名足球比赛中文解说员。"
    "请基于这段比赛视频，按时间顺序输出接近实时的简洁解说。"
    "优先描述真正发生的比赛过程：哪一方在控球、如何推进、是否形成反击、传中、射门、扑救、解围、犯规、角球、任意球、界外球、庆祝或回放。"
    "如果一个片段里有连续动作，要把动作链说清楚，比如“后场出球-中场推进-禁区前传递-完成射门”。"
    "如果队名、球员名、比分或结果看不清，不要编造，统一用“进攻方”“防守方”“持球队员”“门将”等中性称呼。"
    "如果画面主要是慢镜头、庆祝、转播镜头或信息不足，也要直接说明，不要硬编比赛细节。"
    "输出 3 到 6 句中文短句，口语化，适合直播延迟 5 到 10 秒的自动解说。"
    "不要输出标题、编号、分析过程或额外说明。"
)


def now():
    return time.perf_counter()


def prepare_env():
    # 调本地 vLLM 前先清掉代理，避免 127.0.0.1 请求被代理接管。
    for key in [
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "all_proxy",
    ]:
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
    os.environ["no_proxy"] = "127.0.0.1,localhost,::1"


def sse_text(content):
    # 流式响应里增量文本的结构可能不完全一样，这里统一抽成纯文本。
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") in {"text", "output_text"} and item.get("text"):
                    parts.append(item["text"])
        return "".join(parts)
    return ""


def stream_once(
    base_url,
    model,
    video_path,
    prompt,
    fps,
    max_tokens,
    temperature,
    timeout,
    min_pixels,
    max_pixels,
    on_text_delta=None,
):
    # 单次评测 pipeline：
    # 1. 组装“文本 + 视频”的请求
    # 2. 按指定 fps 抽帧后送给模型
    # 3. 流式读取输出，统计首字延迟和总耗时
    # 4. 返回 token 统计和最终描述文本
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "video_url",
                        "video_url": {"url": f"file://{video_path}"},
                    },
                ],
            }
        ],
    }
    # Qwen3-VL 的视频时间戳由 HF processor 生成。
    # fps / max_pixels 都放在 mm_processor_kwargs，避免 vLLM 先抽帧后时间戳错位。
    mm_processor_kwargs = {"fps": fps}
    if min_pixels or max_pixels:
        # 官方的 min_pixels / max_pixels 在 HF video processor 里对应
        # size.shortest_edge / size.longest_edge，且两个键都必须存在。
        mm_processor_kwargs["size"] = {
            "shortest_edge": min_pixels or 4096,
            "longest_edge": max_pixels or 25165824,
        }
    payload["mm_processor_kwargs"] = mm_processor_kwargs

    text = []
    usage = {}
    started = now()
    first_token_latency = None

    with requests.post(url, json=payload, stream=True, timeout=timeout) as resp:
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            detail = resp.text.strip()
            if detail:
                raise requests.HTTPError(f"{e}; response={detail}") from e
            raise
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data:"):
                continue
            data = raw_line[5:].strip()
            if data == "[DONE]":
                break
            item = json.loads(data)
            if item.get("usage"):
                usage = item["usage"]
            for choice in item.get("choices", []):
                delta = choice.get("delta", {})
                piece = sse_text(delta.get("content"))
                if piece:
                    if first_token_latency is None:
                        first_token_latency = now() - started
                    text.append(piece)
                    if on_text_delta:
                        on_text_delta(piece)

    total_time = now() - started
    return {
        "fps": fps,
        "min_pixels": min_pixels,
        "max_pixels": max_pixels,
        "first_token_latency_s": round(first_token_latency or total_time, 4),
        "total_time_s": round(total_time, 4),
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "response": "".join(text).strip(),
    }


def iter_videos(path_str):
    # 输入既可以是单个视频，也可以是一个切片目录。
    path = Path(path_str).expanduser().resolve()
    if path.is_file():
        return [path]
    return sorted(p for p in path.iterdir() if p.suffix.lower() == ".mp4")


def fmt_fps(fps):
    return f"{fps:g}fps"


def output_stem(row):
    parts = [Path(row["video"]).stem, fmt_fps(row["fps"])]
    if row.get("max_pixels"):
        parts.append(f"maxpix{row['max_pixels']}")
    return "_".join(parts)


def write_outputs(rows, out_dir):
    # 每条评测记录按“视频编号 + 采样 fps”落盘。
    # jsonl 用缩进格式方便直接看，csv 保留给表格工具比对。
    json_dir = out_dir / "json"
    csv_dir = out_dir / "csv"
    json_dir.mkdir(parents=True, exist_ok=True)
    csv_dir.mkdir(parents=True, exist_ok=True)

    fields = [
        "video",  # 视频文件路径。
        "fps",  # 视频采样帧率，比如 1 表示每秒抽 1 帧。
        "min_pixels",  # 视频单帧最小像素预算，对应 Qwen3-VL 的 min_pixels。
        "max_pixels",  # 视频单帧最大像素预算，对应 Qwen3-VL 的 max_pixels。
        "first_token_latency_s",  # 从发请求到模型开始输出的时间，单位秒。
        "total_time_s",  # 从发请求到完整输出结束的时间，单位秒。
        "input_tokens",  # 输入 token 数，包含文本 prompt 和视频视觉 token。
        "output_tokens",  # 输出 token 数，也就是模型回答消耗的 token。
        "total_tokens",  # 总 token 数，通常是输入和输出 token 相加。
        "response",  # 模型生成的视频解说文本。
        "error",  # 失败时记录错误信息，成功时为空。
    ]

    for row in rows:
        stem = output_stem(row)
        jsonl_path = json_dir / f"{stem}.jsonl"
        csv_path = csv_dir / f"{stem}.csv"

        with jsonl_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, indent=2) + "\n")

        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerow({k: row.get(k) for k in fields})

        print(jsonl_path)
        print(csv_path)


def main():
    # 主流程：
    # 1. 读取参数
    # 2. 枚举视频和 fps 组合
    # 3. 逐个调用模型
    # 4. 打印简要进度并把完整结果落盘
    prepare_env()

    p = argparse.ArgumentParser()
    p.add_argument("--input", default="/data/yjc/video/slices")
    p.add_argument("--base-url", default="http://127.0.0.1:18008/v1")
    p.add_argument("--model", default="qwen3-vl-8b-instruct")
    p.add_argument("--fps", default="1,2,4")
    p.add_argument("--min-pixels", type=int)
    p.add_argument("--max-pixels", type=int)
    p.add_argument("--max-tokens", type=int, default=256)
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--out-dir", default="/data/yjc/results")
    args = p.parse_args()

    prompt = DEFAULT_PROMPT
    fps_list = [float(x) for x in args.fps.split(",") if x.strip()]
    videos = iter_videos(args.input)
    if not videos:
        print("no mp4 found", file=sys.stderr)
        sys.exit(1)

    rows = []
    for video in videos:
        for fps in fps_list:
            row = {
                "video": str(video),
                "fps": fps,
                "min_pixels": args.min_pixels,
                "max_pixels": args.max_pixels,
            }
            try:
                row.update(
                    stream_once(
                        args.base_url,
                        args.model,
                        str(video),
                        prompt,
                        fps,
                        args.max_tokens,
                        args.temperature,
                        args.timeout,
                        args.min_pixels,
                        args.max_pixels,
                    )
                )
            except Exception as e:
                row["error"] = str(e)
            rows.append(row)
            print(
                json.dumps(
                    {
                        "video": video.name,
                        "fps": fps,
                        "min_pixels": row.get("min_pixels"),
                        "max_pixels": row.get("max_pixels"),
                        "ttft_s": row.get("first_token_latency_s"),
                        "total_s": row.get("total_time_s"),
                        "input_tokens": row.get("input_tokens"),
                        "output_tokens": row.get("output_tokens"),
                        "error": row.get("error"),
                    },
                    ensure_ascii=False,
                )
            )

    write_outputs(rows, Path(args.out_dir).expanduser().resolve())


if __name__ == "__main__":
    main()
