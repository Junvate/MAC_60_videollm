# 实时解说后端接口文档

后端入口：`/data/yjc/backend/api_server.py`  
处理脚本：`/data/yjc/backend/build_commentary_video.py`  
模型请求脚本：`/data/yjc/backend/eval_qwen3vl_video.py`  
默认服务地址：`http://<server-host>:18080`



## 一键启动

项目根目录提供 tmux 启动脚本，会同时启动后端 API 和 Vue3 前端开发服务：

```bash
cd /data/yjc
./start_realtime_app_tmux.sh
```

默认端口：

- 后端 API：`18080`
- 前端 Vite：`5173`
- tmux session：`commentary-app`

常用环境变量：

```bash
SESSION=commentary-demo BACKEND_PORT=18080 FRONTEND_PORT=5173 ./start_realtime_app_tmux.sh
```

脚本会打印本机和局域网访问地址。常用 tmux 命令：

```bash
tmux attach -t commentary-app
tmux kill-session -t commentary-app
```

## 前端 Vue3 项目

前端已经分离为 Vue3 + Vite 项目，目录是 `/data/yjc/frontend`：

- 入口 HTML：`/data/yjc/frontend/index.html`
- 主组件：`/data/yjc/frontend/src/App.vue`
- Vite 配置：`/data/yjc/frontend/vite.config.js`
- 后端托管访问：`http://<server-host>:18080/player`
- Vite 开发访问：`http://<server-host>:5173/frontend/`
- API 请求路径：`/api/...`，开发环境由 Vite proxy 转发到后端
- 根路径 `/` 会自动跳转到 `/player`

前端命令：

```bash
cd /data/yjc/frontend
npm install
npm run dev
npm run build
```

开发时可以运行 `npm run dev`，然后打开 `http://<server-host>:5173/frontend/`。前端代码只请求相对路径 `/api/...`，Vite 会把 `/api` 代理到后端，默认目标是 `http://127.0.0.1:18080`。如需调整后端目标，可设置环境变量 `VITE_BACKEND_TARGET=http://127.0.0.1:18080`。

生产时先执行 `npm run build` 生成 `/data/yjc/frontend/dist`，再启动后端。后端会优先托管 `frontend/dist`；如果还没 build，则托管 `frontend` 源目录里的 `index.html`。

页面能力：

1. 输入服务器视频路径。
2. 可选填写一行队伍线索，帮助模型区分控球方。
3. 点击“开始处理”。
4. 自动调用 `POST /api/jobs`。
5. 自动连接 `GET /api/jobs/{job_id}/events`。
6. 收到 `chunk_ready` 后播放 chunk 视频。
7. 收到 `caption_delta` / `caption_done` 后实时显示字幕。
8. 右侧保留已生成字幕队列，底部显示任务进度。

## 接入流程

1. 前端让用户输入服务器上的视频路径，例如 `/data/yjc/video/commentary_10min/match_10min.mp4`。
2. 如果已知两队信息，同时提交 `team_info` 队伍线索。
3. 调用 `POST /api/jobs` 创建处理任务。
4. 从返回结果里拿到 `job_id` 和 `events_url`。
5. 用 `EventSource` 连接 `GET /api/jobs/{job_id}/events`。
6. 按 SSE 事件增量更新前端：视频 chunk、字幕增量、字幕完成、任务完成。

## 1. 健康检查

### `GET /api/health`

用于确认后端服务是否启动。

#### 响应示例

```json
{
  "ok": true,
  "jobs": 0
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ok` | boolean | 服务是否正常 |
| `jobs` | number | 当前内存中任务数量 |

## 2. 创建处理任务

### `POST /api/jobs`

前端点击“开始处理”时调用。后端收到视频路径后，会后台启动任务：探测视频时长、按 chunk 切片、逐 chunk 调模型生成字幕。

### 请求体

```json
{
  "video_path": "/data/yjc/video/commentary_10min/match_10min.mp4",
  "slice_seconds": 5,
  "sample_fps": 1,
  "max_pixels": 151200,
  "team_info": "Miami/MIA：粉红色；Philadelphia/PHI：黑色"
}
```

### 参数说明

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `video_path` | string | 是 | - | 服务器上的视频路径，必须是后端机器能读到的文件 |
| `slice_seconds` | number | 否 | `5.0` | 每个 chunk 的秒数 |
| `min_tail_seconds` | number | 否 | `0.3` | 末尾短片段小于该值时并入上一段 |
| `sample_fps` | number | 否 | `1.0` | 送入 VideoLLM 的采样帧率 |
| `base_url` | string | 否 | `http://127.0.0.1:18008/v1` | 本地 vLLM OpenAI-compatible API 地址 |
| `model` | string | 否 | `qwen3-vl-8b-instruct` | vLLM served model name |
| `min_pixels` | number/null | 否 | `null` | 视频帧最小像素预算 |
| `max_pixels` | number/null | 否 | `151200` | 视频帧最大像素预算 |
| `max_tokens` | number | 否 | `96` | 每个 chunk 字幕最大输出 token |
| `temperature` | number | 否 | `0.2` | 模型采样温度 |
| `timeout` | number | 否 | `1800` | 单个模型请求超时时间，单位秒 |
| `prompt` | string/null | 否 | `null` | 自定义字幕 prompt；为空时使用默认足球解说 prompt |
| `team_info` | string | 否 | `""` | 队伍、缩写和队服颜色线索，例如 `Miami/MIA：粉红色；Philadelphia/PHI：黑色` |

### 响应示例

```json
{
  "job_id": "8a6d1d8f0b7d4d82a4b9b5f53e2f4e8a",
  "status": "queued",
  "error": null,
  "source_video": "/data/yjc/video/commentary_10min/match_10min.mp4",
  "source_url": "/api/media?path=/data/yjc/video/commentary_10min/match_10min.mp4",
  "out_dir": "/data/yjc/video/realtime_jobs/8a6d1d8f0b7d4d82a4b9b5f53e2f4e8a",
  "started_at": 1782180000.123,
  "finished_at": null,
  "segments": [],
  "events_url": "/api/jobs/8a6d1d8f0b7d4d82a4b9b5f53e2f4e8a/events"
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `job_id` | string | 任务 ID，后续查询和 SSE 都用它 |
| `status` | string | `queued` / `running` / `completed` / `failed` |
| `error` | string/null | 失败原因，正常时为空 |
| `source_video` | string | 原视频服务器路径 |
| `source_url` | string | 原视频可播放 URL，前端需要拼接后端 host |
| `out_dir` | string | 本次任务输出目录 |
| `started_at` | number | 任务创建时间戳，单位秒 |
| `finished_at` | number/null | 任务结束时间戳 |
| `segments` | array | 已完成字幕的 chunk 列表 |
| `events_url` | string | SSE 事件流地址 |

### 错误响应示例

如果视频不存在或格式不支持：

```json
{
  "detail": "input must be a video file: /not/exist.mp4"
}
```

## 3. 查询任务状态

### `GET /api/jobs/{job_id}`

用于刷新页面后恢复任务状态，或者不用 SSE 时轮询。

### 响应示例

```json
{
  "job_id": "8a6d1d8f0b7d4d82a4b9b5f53e2f4e8a",
  "status": "running",
  "error": null,
  "source_video": "/data/yjc/video/commentary_10min/match_10min.mp4",
  "source_url": "/api/media?path=/data/yjc/video/commentary_10min/match_10min.mp4",
  "out_dir": "/data/yjc/video/realtime_jobs/8a6d1d8f0b7d4d82a4b9b5f53e2f4e8a",
  "started_at": 1782180000.123,
  "finished_at": null,
  "segments": [
    {
      "index": 0,
      "start_time": 0,
      "end_time": 5,
      "video": "/data/yjc/video/realtime_jobs/8a6d1d8f0b7d4d82a4b9b5f53e2f4e8a/slices_5s/slice_0000.mp4",
      "video_url": "/api/media?path=/data/yjc/video/realtime_jobs/8a6d1d8f0b7d4d82a4b9b5f53e2f4e8a/slices_5s/slice_0000.mp4",
      "description": "Miami 在中场拿球推进，Philadelphia 迅速上前逼抢。"
    }
  ],
  "events_url": "/api/jobs/8a6d1d8f0b7d4d82a4b9b5f53e2f4e8a/events"
}
```

## 4. 获取字幕 JSON

### `GET /api/jobs/{job_id}/captions`

返回当前已经完成的字幕片段，格式接近原来的 `captions.json`。

### 响应示例

```json
{
  "source_video": "/data/yjc/video/commentary_10min/match_10min.mp4",
  "segments": [
    {
      "index": 0,
      "start_time": 0,
      "end_time": 5,
      "video": "/data/yjc/video/realtime_jobs/<job_id>/slices_5s/slice_0000.mp4",
      "video_url": "/api/media?path=/data/yjc/video/realtime_jobs/<job_id>/slices_5s/slice_0000.mp4",
      "description": "Miami 在中场拿球推进，Philadelphia 迅速上前逼抢。",
      "latency": {
        "first_token_latency_s": 1.23,
        "total_time_s": 4.56
      },
      "tokens": {
        "input_tokens": 1234,
        "output_tokens": 32,
        "total_tokens": 1266
      }
    }
  ]
}
```

## 5. SSE 实时事件流

### `GET /api/jobs/{job_id}/events`

前端使用 `EventSource` 连接该接口。后端会按处理进度推送事件。

支持可选参数：

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `after` | number | `-1` | 从哪个事件序号之后开始推送，用于断线重连 |

### 前端示例

```js
async function startJob(videoPath) {
  const res = await fetch('/api/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ video_path: videoPath })
  })

  if (!res.ok) {
    throw new Error(await res.text())
  }

  const job = await res.json()
  subscribeJob(job.job_id)
  return job
}

function subscribeJob(jobId) {
  const es = new EventSource(`/api/jobs/${jobId}/events`)

  es.addEventListener('started', event => {
    const data = JSON.parse(event.data)
    console.log('任务开始', data)
  })

  es.addEventListener('chunk_ready', event => {
    const data = JSON.parse(event.data)
    const chunkUrl = data.video_url
    console.log('chunk 视频可播放', chunkUrl)
  })

  es.addEventListener('caption_delta', event => {
    const data = JSON.parse(event.data)
    console.log('字幕增量', data.index, data.text)
  })

  es.addEventListener('caption_done', event => {
    const data = JSON.parse(event.data)
    console.log('字幕完成', data.index, data.description)
  })

  es.addEventListener('completed', event => {
    const data = JSON.parse(event.data)
    console.log('任务完成', data)
    es.close()
  })

  es.addEventListener('error', event => {
    if (event.data) {
      console.error('任务失败', JSON.parse(event.data))
    }
    es.close()
  })
}
```

### 事件：`started`

任务开始，已完成视频时长探测和 chunk 数计算。

```json
{
  "seq": 0,
  "job_id": "<job_id>",
  "status": "running",
  "source_video": "/data/yjc/video/commentary_10min/match_10min.mp4",
  "source_url": "/api/media?path=/data/yjc/video/commentary_10min/match_10min.mp4",
  "duration": 600,
  "total_segments": 120,
  "slice_seconds": 5
}
```

### 事件：`chunk_started`

某个 chunk 开始处理。

```json
{
  "seq": 1,
  "job_id": "<job_id>",
  "index": 0,
  "start_time": 0,
  "end_time": 5,
  "video": "/data/yjc/video/realtime_jobs/<job_id>/slices_5s/slice_0000.mp4",
  "video_url": "/api/media?path=/data/yjc/video/realtime_jobs/<job_id>/slices_5s/slice_0000.mp4",
  "total_segments": 120
}
```

### 事件：`chunk_ready`

某个 chunk 已切好，前端可以播放该 chunk 视频。

```json
{
  "seq": 2,
  "job_id": "<job_id>",
  "index": 0,
  "start_time": 0,
  "end_time": 5,
  "video": "/data/yjc/video/realtime_jobs/<job_id>/slices_5s/slice_0000.mp4",
  "video_url": "/api/media?path=/data/yjc/video/realtime_jobs/<job_id>/slices_5s/slice_0000.mp4"
}
```

前端播放地址：

```js
const playableUrl = data.video_url
```

### 事件：`caption_delta`

模型流式输出字幕增量。适合前端做打字机效果。

```json
{
  "seq": 3,
  "job_id": "<job_id>",
  "index": 0,
  "start_time": 0,
  "end_time": 5,
  "video": "/data/yjc/video/realtime_jobs/<job_id>/slices_5s/slice_0000.mp4",
  "video_url": "/api/media?path=/data/yjc/video/realtime_jobs/<job_id>/slices_5s/slice_0000.mp4",
  "delta": "Miami",
  "text": "Miami"
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `delta` | string | 本次新增文本 |
| `text` | string | 当前 chunk 已累计字幕文本 |

### 事件：`caption_done`

某个 chunk 的字幕完成。前端应以这个事件里的 `description` 作为最终字幕。

```json
{
  "seq": 8,
  "job_id": "<job_id>",
  "index": 0,
  "start_time": 0,
  "end_time": 5,
  "video": "/data/yjc/video/realtime_jobs/<job_id>/slices_5s/slice_0000.mp4",
  "video_url": "/api/media?path=/data/yjc/video/realtime_jobs/<job_id>/slices_5s/slice_0000.mp4",
  "description": "Miami 在中场拿球推进，Philadelphia 迅速上前逼抢。",
  "latency": {
    "first_token_latency_s": 1.23,
    "total_time_s": 4.56
  },
  "tokens": {
    "input_tokens": 1234,
    "output_tokens": 32,
    "total_tokens": 1266
  }
}
```

### 事件：`completed`

全部 chunk 处理完成。

```json
{
  "seq": 999,
  "job_id": "<job_id>",
  "status": "completed",
  "segments": [],
  "captions_json": "/data/yjc/video/realtime_jobs/<job_id>/captions.json",
  "srt": "/data/yjc/video/realtime_jobs/<job_id>/match_10min.srt"
}
```

### 事件：`error`

任务失败。

```json
{
  "seq": 12,
  "job_id": "<job_id>",
  "status": "failed",
  "error": "具体错误信息"
}
```

## 6. 视频文件访问

### `GET /api/media?path=<server-video-path>`

用于前端播放原视频或 chunk 视频。接口会校验路径存在且后缀是常见视频格式。

### 示例

```text
GET /api/media?path=/data/yjc/video/commentary_10min/match_10min.mp4
```

前端使用：

```html
<video controls :src="videoUrl"></video>
```

其中 `videoUrl` 来自 `source_url` 或 `chunk_ready.video_url`，开发环境会由 Vite proxy 转发到后端。

## 前端状态建议

| 后端事件 | 前端建议动作 |
| --- | --- |
| `started` | 展示总时长、总 chunk 数，状态改为处理中 |
| `chunk_started` | 高亮当前 chunk，显示“切片中/分析中” |
| `chunk_ready` | 可以把当前 `<video>` 切到该 chunk 的 `video_url` |
| `caption_delta` | 更新当前 chunk 字幕打字机文本 |
| `caption_done` | 保存该 chunk 最终字幕，更新进度 |
| `completed` | 关闭 SSE，显示处理完成 |
| `error` | 关闭 SSE，显示错误信息 |

## 注意事项

- `video_path` 是服务器路径，不是浏览器本地路径。
- 返回的 `source_url`、`video_url` 都是相对路径，前端需要拼上后端地址。
- 当前任务状态保存在后端内存中，重启 `api_server.py` 后历史 `job_id` 会丢失。
- 输出文件会写到 `/data/yjc/video/realtime_jobs/<job_id>/`。
- 模型服务需要先启动，默认后端会请求 `http://127.0.0.1:18008/v1/chat/completions`。
