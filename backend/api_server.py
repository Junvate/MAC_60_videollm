#!/usr/bin/env python3
import argparse
import json
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from build_commentary_video import (
    DEFAULT_CAPTION_PROMPT,
    FFMPEG,
    VIDEO_SUFFIXES,
    clean_caption,
    ffprobe_duration,
    segment_times,
    video_encode_args,
    write_caption_json,
    write_srt,
)
from eval_qwen3vl_video import prepare_env, stream_once


DEFAULT_JOBS_DIR = ROOT_DIR / "video" / "realtime_jobs"


class StartJobRequest(BaseModel):
    video_path: str = Field(..., description="服务器上的视频绝对路径或可解析路径")
    slice_seconds: float = 5.0
    min_tail_seconds: float = 0.3
    sample_fps: float = 1.0
    base_url: str = "http://127.0.0.1:18008/v1"
    model: str = "qwen3-vl-8b-instruct"
    min_pixels: Optional[int] = None
    max_pixels: Optional[int] = 151200
    max_tokens: int = 96
    temperature: float = 0.2
    timeout: int = 1800
    prompt: Optional[str] = None


class Job:
    def __init__(self, job_id, source_video, out_dir, request):
        self.job_id = job_id
        self.source_video = source_video
        self.out_dir = out_dir
        self.request = request
        self.status = "queued"
        self.error = None
        self.started_at = time.time()
        self.finished_at = None
        self.events = []
        self.segments = []
        self.condition = threading.Condition()

    def emit(self, event, data):
        payload = {"job_id": self.job_id, **data}
        with self.condition:
            item = {"seq": len(self.events), "event": event, "data": payload}
            self.events.append(item)
            self.condition.notify_all()
        return item

    def finish(self, status, event, data):
        payload = {"job_id": self.job_id, "status": status, **data}
        with self.condition:
            self.status = status
            self.finished_at = time.time()
            item = {"seq": len(self.events), "event": event, "data": payload}
            self.events.append(item)
            self.condition.notify_all()
        return item

    def snapshot(self):
        with self.condition:
            return {
                "job_id": self.job_id,
                "status": self.status,
                "error": self.error,
                "source_video": str(self.source_video),
                "source_url": media_url(self.source_video),
                "out_dir": str(self.out_dir),
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "segments": list(self.segments),
                "events_url": f"/api/jobs/{self.job_id}/events",
            }


app = FastAPI(title="Realtime Commentary API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs = {}


def media_url(path):
    return f"/api/media?path={quote(str(path))}"


def resolve_video_path(path_str):
    path = Path(path_str).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in VIDEO_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"input must be a video file: {path}")
    return path


def run_ffmpeg_slice(source_video, slice_path, start_time, end_time):
    # 每个 chunk 独立切片，切完立刻送模型，前端可以边等边收字幕。
    slice_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG,
        "-y",
        "-ss",
        f"{start_time:.6f}",
        "-i",
        source_video,
        "-t",
        f"{end_time - start_time:.6f}",
        "-map",
        "0:v:0",
        "-an",
        *video_encode_args("1200k"),
        slice_path,
    ]
    import subprocess

    subprocess.run([str(item) for item in cmd], check=True)


def write_slice_metadata(path, job, duration, slice_seconds, raw_segments):
    metadata = {
        "source_video": str(job.source_video),
        "duration": float(duration),
        "slice_seconds": float(slice_seconds),
        "segments": raw_segments,
    }
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def run_job(job):
    req = job.request
    prompt = req.prompt or DEFAULT_CAPTION_PROMPT
    slices_dir = job.out_dir / "slices_5s"
    slice_metadata_path = job.out_dir / "slice_times.json"
    captions_path = job.out_dir / "captions.json"
    srt_path = job.out_dir / f"{job.source_video.stem}.srt"

    try:
        # 后端实时 pipeline：探测时长 -> 逐 chunk 切片 -> 流式生成字幕 -> 增量落盘。
        prepare_env()
        job.status = "running"
        duration = round(ffprobe_duration(job.source_video), 6)
        raw_segments = []
        caption_segments = []
        times = segment_times(duration, req.slice_seconds, req.min_tail_seconds)
        job.emit(
            "started",
            {
                "status": job.status,
                "source_video": str(job.source_video),
                "source_url": media_url(job.source_video),
                "duration": duration,
                "total_segments": len(times),
                "slice_seconds": req.slice_seconds,
            },
        )

        for index, (start_time, end_time) in enumerate(times):
            slice_path = slices_dir / f"slice_{index:04d}.mp4"
            segment_base = {
                "index": index,
                "start_time": float(start_time),
                "end_time": float(end_time),
                "video": str(slice_path),
                "video_url": media_url(slice_path),
            }

            job.emit("chunk_started", {**segment_base, "total_segments": len(times)})
            run_ffmpeg_slice(job.source_video, slice_path, start_time, end_time)
            raw_segments.append({k: segment_base[k] for k in ["index", "start_time", "end_time", "video"]})
            write_slice_metadata(slice_metadata_path, job, duration, req.slice_seconds, raw_segments)
            job.emit("chunk_ready", segment_base)

            caption_parts = []

            def on_text_delta(piece):
                caption_parts.append(piece)
                job.emit("caption_delta", {**segment_base, "delta": piece, "text": clean_caption("".join(caption_parts))})

            result = stream_once(
                req.base_url,
                req.model,
                str(slice_path),
                prompt,
                req.sample_fps,
                req.max_tokens,
                req.temperature,
                req.timeout,
                req.min_pixels,
                req.max_pixels,
                on_text_delta=on_text_delta,
            )
            description = clean_caption(result.get("response", ""))
            caption = {
                **segment_base,
                "description": description,
                "latency": {
                    "first_token_latency_s": result.get("first_token_latency_s"),
                    "total_time_s": result.get("total_time_s"),
                },
                "tokens": {
                    "input_tokens": result.get("input_tokens"),
                    "output_tokens": result.get("output_tokens"),
                    "total_tokens": result.get("total_tokens"),
                },
            }
            caption_segments.append(caption)
            with job.condition:
                job.segments = list(caption_segments)
            write_caption_json(captions_path, job.source_video, caption_segments)
            write_srt(captions_path, srt_path)
            job.emit("caption_done", caption)

        job.finish(
            "completed",
            "completed",
            {
                "segments": caption_segments,
                "captions_json": str(captions_path),
                "srt": str(srt_path),
            },
        )
    except Exception as exc:
        job.error = str(exc)
        job.finish("failed", "error", {"error": job.error})


def get_job_or_404(job_id):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.get("/api/health")
def health():
    return {"ok": True, "jobs": len(jobs)}


@app.post("/api/jobs")
def start_job(request: StartJobRequest):
    source_video = resolve_video_path(request.video_path)
    job_id = uuid.uuid4().hex
    out_dir = DEFAULT_JOBS_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    job = Job(job_id, source_video, out_dir, request)
    jobs[job_id] = job
    thread = threading.Thread(target=run_job, args=(job,), daemon=True)
    thread.start()

    return job.snapshot()


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    return get_job_or_404(job_id).snapshot()


@app.get("/api/jobs/{job_id}/captions")
def job_captions(job_id: str):
    job = get_job_or_404(job_id)
    captions_path = job.out_dir / "captions.json"
    if not captions_path.exists():
        return {"source_video": str(job.source_video), "segments": []}
    return json.loads(captions_path.read_text(encoding="utf-8"))


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str, after: int = -1):
    job = get_job_or_404(job_id)

    def event_stream():
        next_seq = after + 1
        while True:
            heartbeat = False
            with job.condition:
                while next_seq >= len(job.events) and job.status not in {"completed", "failed"}:
                    job.condition.wait(timeout=15)
                    if next_seq >= len(job.events):
                        heartbeat = True
                        break
                if next_seq >= len(job.events):
                    if heartbeat:
                        pass
                    else:
                        break

            if heartbeat:
                yield ": keep-alive\n\n"
                continue

            with job.condition:
                if next_seq >= len(job.events):
                    break
                item = job.events[next_seq]
                next_seq += 1

            data = json.dumps({"seq": item["seq"], **item["data"]}, ensure_ascii=False)
            yield f"event: {item['event']}\ndata: {data}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/media")
def media(path: str):
    video_path = resolve_video_path(path)
    return FileResponse(video_path)


def main():
    parser = argparse.ArgumentParser(description="实时视频解说后端接口")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
