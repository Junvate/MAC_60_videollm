<template>
  <div class="page-shell">
    <section class="control-panel">
      <div class="title-row">
        <h1>实时赛事解说字幕</h1>
        <span class="pill" :class="statusClass">{{ statusText }}</span>
      </div>

      <div class="form-row api-row">
        <input
          v-model.trim="apiBase"
          class="video-path-input"
          placeholder="后端地址，例如 http://127.0.0.1:18080"
        />
      </div>

      <div class="form-row">
        <input
          v-model.trim="videoPath"
          class="video-path-input"
          placeholder="输入服务器视频路径，例如 /data/yjc/video/commentary_10min/match_10min.mp4"
          @keyup.enter="startJob"
        />
        <input
          v-model.number="sliceSeconds"
          class="small-input"
          type="number"
          min="1"
          step="1"
          title="chunk 秒数"
        />
        <button
          class="start-btn"
          :disabled="isStarting || isProcessing || !videoPath || !apiBase"
          @click="startJob"
        >
          {{ isStarting ? '启动中...' : '开始处理' }}
        </button>
      </div>

      <div class="hint-row">
        <span>后端：{{ normalizedApiBase }}</span>
        <span v-if="jobId">任务：{{ jobId }}</span>
        <span>chunk：{{ sliceSeconds }}s</span>
      </div>
    </section>

    <main class="main-card">
      <section class="player-card">
        <div class="video-container">
          <video
            v-if="currentVideoUrl"
            ref="videoEl"
            controls
            autoplay
            muted
            preload="metadata"
            :src="currentVideoUrl"
            @timeupdate="onTimeUpdate"
            @loadedmetadata="onLoadedMetadata"
            @ended="onChunkEnded"
            @play="onPlay"
            @pause="onPause"
          >
            您的浏览器不支持 video 标签。
          </video>

          <div v-else class="empty-state">
            <div>输入服务器视频路径后点击开始处理</div>
            <div>后端会按 chunk 切片，前端实时显示视频和字幕</div>
          </div>

          <div v-if="displayText || isTyping" class="subtitle-overlay">
            <span>{{ displayText }}</span>
            <span v-if="isTyping" class="cursor"></span>
          </div>
        </div>

        <div class="status-bar">
          <div class="item">
            <span class="label">切片</span>
            <span class="value">#{{ currentSegmentIndex }} / {{ totalSegments || '-' }}</span>
          </div>
          <div class="item">
            <span class="label">完成</span>
            <span class="value">{{ completedSegments }}</span>
          </div>
          <div class="progress-container">
            <span class="time-display">{{ currentTimeDisplay }} / {{ durationDisplay }}</span>
            <div class="progress-track">
              <div class="bar" :style="{ width: progressPercent + '%' }"></div>
            </div>
          </div>
          <div class="item">
            <span class="label">播放</span>
            <span class="value">{{ isPlaying ? '播放中' : '暂停' }}</span>
          </div>
          <button class="fullscreen-btn" @click="toggleFullscreen">⛶ 全屏</button>
        </div>
      </section>

      <aside class="side-card">
        <div class="side-header">
          <h2>字幕队列</h2>
          <span class="time-display">{{ progressLabel }}</span>
        </div>

        <div class="caption-list">
          <div v-if="!captions.length" class="empty-state">等待字幕生成...</div>
          <div
            v-for="item in captions"
            :key="item.index"
            class="caption-item"
            :class="{ active: item.index === currentIndex, playable: canReplayChunk(item) }"
            @click="playHistoryChunk(item)"
          >
            <div class="caption-meta">
              <span>#{{ item.index + 1 }}</span>
              <span>{{ formatClock(item.start_time) }} - {{ formatClock(item.end_time) }}</span>
            </div>
            <div class="caption-text">{{ item.description || item.text || '生成中...' }}</div>
            <div v-if="canReplayChunk(item)" class="caption-action">点击回放这个 chunk</div>
          </div>
        </div>
      </aside>
    </main>

    <div class="log-bar">
      <span :class="{ 'error-text': status === 'failed' }">{{ message }}</span>
      <span>{{ lastEvent }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'

const apiBase = ref(new URLSearchParams(window.location.search).get('api') || window.location.origin)
const videoEl = ref(null)
const videoPath = ref('/data/yjc/video/commentary_10min/match_10min.mp4')
const sliceSeconds = ref(5)
const jobId = ref('')
const status = ref('idle')
const message = ref('准备就绪')
const lastEvent = ref('')
const captions = ref([])
const pendingChunks = ref([])
const currentIndex = ref(-1)
const currentVideoUrl = ref('')
const currentChunk = ref(null)
const isHistoryPlayback = ref(false)
const displayText = ref('')
const isTyping = ref(false)
const isPlaying = ref(false)
const currentTime = ref(0)
const duration = ref(0)
const totalSegments = ref(0)
const isStarting = ref(false)
const ENABLE_HISTORY_REPLAY = false

let eventSource = null
let typingTimer = null

function cleanApiBase() {
  return String(apiBase.value || '').replace(/\/$/, '')
}

function absoluteUrl(url) {
  if (!url) return ''
  return url.startsWith('http') ? url : cleanApiBase() + url
}

function closeEventSource() {
  if (!eventSource) return
  eventSource.close()
  eventSource = null
}

function clearTypingTimer() {
  if (!typingTimer) return
  clearInterval(typingTimer)
  typingTimer = null
}

function resetState() {
  closeEventSource()
  clearTypingTimer()
  jobId.value = ''
  status.value = 'idle'
  message.value = '准备就绪'
  lastEvent.value = ''
  captions.value = []
  pendingChunks.value = []
  currentIndex.value = -1
  currentVideoUrl.value = ''
  currentChunk.value = null
  isHistoryPlayback.value = false
  displayText.value = ''
  isTyping.value = false
  isPlaying.value = false
  currentTime.value = 0
  duration.value = 0
  totalSegments.value = 0
}

async function startJob() {
  if (!videoPath.value || isStarting.value || isProcessing.value) return

  resetState()
  isStarting.value = true
  status.value = 'queued'
  message.value = '正在创建任务...'

  try {
    const response = await fetch(`${cleanApiBase()}/api/jobs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_path: videoPath.value,
        slice_seconds: Number(sliceSeconds.value) || 5,
      }),
    })

    if (!response.ok) throw new Error(await response.text())

    const job = await response.json()
    jobId.value = job.job_id
    status.value = job.status
    message.value = '任务已创建，等待后端开始处理'
    subscribe(job.events_url)
  } catch (error) {
    status.value = 'failed'
    message.value = error.message || String(error)
  } finally {
    isStarting.value = false
  }
}

function subscribe(eventsUrl) {
  eventSource = new EventSource(absoluteUrl(eventsUrl))
  eventSource.addEventListener('started', event => handleStarted(JSON.parse(event.data)))
  eventSource.addEventListener('chunk_started', event => handleEventName('chunk_started', JSON.parse(event.data)))
  eventSource.addEventListener('chunk_ready', event => handleChunkReady(JSON.parse(event.data)))
  eventSource.addEventListener('caption_delta', event => handleCaptionDelta(JSON.parse(event.data)))
  eventSource.addEventListener('caption_done', event => handleCaptionDone(JSON.parse(event.data)))
  eventSource.addEventListener('completed', event => handleCompleted(JSON.parse(event.data)))
  eventSource.addEventListener('error', event => {
    if (event.data) handleFailed(JSON.parse(event.data))
  })
  eventSource.onerror = () => {
    if (status.value !== 'completed' && status.value !== 'failed') {
      message.value = 'SSE 连接中断，浏览器会自动重连'
    }
  }
}

function handleEventName(name, data) {
  lastEvent.value = `${name} #${(data.index ?? -1) + 1}`
}

function handleStarted(data) {
  status.value = data.status || 'running'
  totalSegments.value = data.total_segments || 0
  message.value = `开始处理，共 ${totalSegments.value} 个 chunk`
  lastEvent.value = 'started'
}

function handleChunkReady(data) {
  handleEventName('chunk_ready', data)
  upsertCaption(data)
  pendingChunks.value.push(data)
  if (!currentVideoUrl.value) playNextChunk()
}

function handleCaptionDelta(data) {
  handleEventName('caption_delta', data)
  upsertCaption({ ...data, description: data.text })
  if (data.index === currentIndex.value) {
    displayText.value = data.text || ''
    isTyping.value = true
  }
}

function handleCaptionDone(data) {
  handleEventName('caption_done', data)
  upsertCaption(data)
  if (data.index === currentIndex.value) {
    startTyping(data.description || '')
  }
  message.value = `已完成第 ${data.index + 1} 个 chunk`
}

function handleCompleted() {
  status.value = 'completed'
  message.value = '全部处理完成'
  lastEvent.value = 'completed'
  closeEventSource()
}

function handleFailed(data) {
  status.value = 'failed'
  message.value = data.error || '任务失败'
  lastEvent.value = 'error'
  closeEventSource()
}

function upsertCaption(item) {
  const next = captions.value.slice()
  const index = next.findIndex(row => row.index === item.index)
  if (index >= 0) next[index] = { ...next[index], ...item }
  else next.push(item)
  next.sort((a, b) => a.index - b.index)
  captions.value = next
}

async function playNextChunk() {
  const next = pendingChunks.value.shift()
  if (!next) return
  await playChunk(next, false)
}

async function playHistoryChunk(item) {
  if (!canReplayChunk(item)) return
  await playChunk(item, true)
  message.value = `正在回放第 ${item.index + 1} 个 chunk，后台处理仍在继续`
}

function canReplayChunk(item) {
  return ENABLE_HISTORY_REPLAY && Boolean(item.video_url)
}

async function playChunk(chunk, historyPlayback) {
  currentChunk.value = chunk
  currentIndex.value = chunk.index
  currentTime.value = chunk.start_time || 0
  duration.value = chunk.end_time || 0
  isHistoryPlayback.value = historyPlayback
  displayText.value = chunk.description || chunk.text || findCaptionText(chunk.index)
  currentVideoUrl.value = absoluteUrl(chunk.video_url)

  await nextTick()
  if (!videoEl.value) return

  videoEl.value.muted = true
  videoEl.value.currentTime = 0
  videoEl.value.play().catch(() => {
    message.value = '浏览器阻止自动播放，可手动点击播放'
  })
}

function onChunkEnded() {
  if (isHistoryPlayback.value) {
    isHistoryPlayback.value = false
    return
  }
  playNextChunk()
}

function findCaptionText(index) {
  const item = captions.value.find(row => row.index === index)
  return item?.description || item?.text || ''
}

function startTyping(text) {
  clearTypingTimer()
  displayText.value = ''
  isTyping.value = true

  if (!text) {
    isTyping.value = false
    return
  }

  let index = 0
  typingTimer = setInterval(() => {
    if (index < text.length) {
      displayText.value += text[index]
      index += 1
      return
    }

    clearTypingTimer()
    isTyping.value = false
  }, 45)
}

function onTimeUpdate() {
  if (!videoEl.value || !currentChunk.value) return
  currentTime.value = (currentChunk.value.start_time || 0) + videoEl.value.currentTime
}

function onLoadedMetadata() {
  if (!videoEl.value) return
  const chunkStart = currentChunk.value?.start_time || 0
  duration.value = chunkStart + (videoEl.value.duration || sliceSeconds.value)
}

function onPlay() {
  isPlaying.value = true
}

function onPause() {
  isPlaying.value = false
}

function toggleFullscreen() {
  const container = document.querySelector('.video-container')
  if (!document.fullscreenElement) {
    container.requestFullscreen?.() || container.webkitRequestFullscreen?.()
  } else {
    document.exitFullscreen?.() || document.webkitExitFullscreen?.()
  }
}

function formatClock(value) {
  const time = Math.max(0, Number(value) || 0)
  const minutes = Math.floor(time / 60)
  const seconds = Math.floor(time % 60)
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

const completedSegments = computed(() => captions.value.filter(item => item.description && !item.delta).length)
const currentSegmentIndex = computed(() => currentIndex.value < 0 ? 0 : currentIndex.value + 1)
const progressPercent = computed(() => {
  if (!totalSegments.value) return 0
  return Math.min(100, (completedSegments.value / totalSegments.value) * 100)
})
const currentTimeDisplay = computed(() => formatClock(currentTime.value))
const durationDisplay = computed(() => {
  const total = totalSegments.value ? totalSegments.value * Number(sliceSeconds.value || 5) : duration.value
  return formatClock(total || 0)
})
const progressLabel = computed(() => `${completedSegments.value}/${totalSegments.value || '-'}`)
const isProcessing = computed(() => ['queued', 'running'].includes(status.value))
const normalizedApiBase = computed(() => cleanApiBase() || '-')
const statusText = computed(() => {
  const map = {
    idle: '未开始',
    queued: '排队中',
    running: '处理中',
    completed: '已完成',
    failed: '失败',
  }
  return map[status.value] || status.value
})
const statusClass = computed(() => ({
  running: isProcessing.value,
  completed: status.value === 'completed',
  failed: status.value === 'failed',
}))

onBeforeUnmount(() => {
  closeEventSource()
  clearTypingTimer()
})
</script>

<style scoped>
.page-shell {
  min-height: calc(100vh - 28px);
  display: grid;
  grid-template-rows: auto 1fr auto;
  gap: 12px;
}

.control-panel {
  background: linear-gradient(135deg, #252525, #1f1f1f);
  border: 1px solid #363636;
  border-radius: 12px;
  padding: 14px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
}

.title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.title-row h1 {
  font-size: 18px;
  font-weight: 650;
  letter-spacing: 0.2px;
}

.pill {
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 13px;
  color: #aaa;
  background: #333;
  border: 1px solid #444;
  white-space: nowrap;
}

.pill.running {
  color: #b0f0d0;
  background: #244434;
  border-color: #3a6b4f;
}

.pill.completed {
  color: #ffe28a;
  background: #4a3b15;
  border-color: #7a6427;
}

.pill.failed {
  color: #ffb8b8;
  background: #4a2424;
  border-color: #743737;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 10px;
}

.api-row {
  grid-template-columns: 1fr;
  margin-bottom: 10px;
}

.video-path-input {
  width: 100%;
  background: #171717;
  color: #f0f0f0;
  border: 1px solid #444;
  border-radius: 8px;
  padding: 11px 12px;
  font-size: 14px;
  outline: none;
}

.video-path-input:focus {
  border-color: #f0c040;
  box-shadow: 0 0 0 3px rgba(240, 192, 64, 0.12);
}

.small-input {
  width: 92px;
  background: #171717;
  color: #f0f0f0;
  border: 1px solid #444;
  border-radius: 8px;
  padding: 11px 10px;
  font-size: 14px;
  outline: none;
}

.start-btn {
  border: none;
  border-radius: 8px;
  background: #f0c040;
  color: #181818;
  padding: 0 18px;
  font-weight: 700;
  cursor: pointer;
  transition: transform 0.15s, opacity 0.15s;
}

.start-btn:hover:not(:disabled) {
  transform: translateY(-1px);
}

.start-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.hint-row {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  color: #8f8f8f;
  font-size: 12px;
}

.main-card {
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 12px;
  align-self: start;
}

.player-card {
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #262626;
  border: 1px solid #363636;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
}

.video-container {
  aspect-ratio: 16 / 9;
  width: 100%;
  max-height: 58vh;
  min-height: 260px;
  position: relative;
  background: #000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.empty-state {
  color: #777;
  text-align: center;
  line-height: 1.8;
  padding: 24px;
}

.video-container video {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
  background: #000;
}

.subtitle-overlay {
  position: absolute;
  bottom: 12%;
  left: 5%;
  right: 5%;
  text-align: center;
  color: #fff;
  font-size: clamp(20px, 3.5vh, 40px);
  font-weight: 550;
  background: rgba(0, 0, 0, 0.72);
  padding: 12px 24px;
  border-radius: 8px;
  pointer-events: none;
  text-shadow: 0 1px 3px rgba(0, 0, 0, 0.8);
  letter-spacing: 0.4px;
  line-height: 1.4;
  border: 1px solid rgba(255, 255, 255, 0.1);
}

.subtitle-overlay .cursor {
  display: inline-block;
  width: 2px;
  height: 1.1em;
  background: #f0c040;
  margin-left: 4px;
  vertical-align: text-bottom;
  animation: blink 0.9s step-end infinite;
}

@keyframes blink {
  0%,
  100% {
    opacity: 1;
  }

  50% {
    opacity: 0;
  }
}

.status-bar {
  background: #2f2f2f;
  padding: 10px 18px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px 18px;
  border-top: 1px solid #3a3a3a;
  font-size: 14px;
  color: #b0b0b0;
  flex-shrink: 0;
}

.item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.label {
  color: #888;
}

.value {
  color: #eee;
  font-weight: 550;
  font-variant-numeric: tabular-nums;
}

.progress-container {
  flex: 1;
  min-width: 160px;
  display: flex;
  align-items: center;
  gap: 10px;
}

.progress-track {
  flex: 1;
  height: 4px;
  background: #444;
  border-radius: 2px;
  overflow: hidden;
}

.progress-track .bar {
  height: 100%;
  width: 0%;
  background: #f0c040;
  border-radius: 2px;
  transition: width 0.2s;
}

.time-display {
  font-family: monospace;
  color: #ccc;
  font-size: 13px;
  white-space: nowrap;
}

.fullscreen-btn {
  background: none;
  border: 1px solid #555;
  color: #ccc;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  transition: 0.2s;
}

.fullscreen-btn:hover {
  background: #444;
  border-color: #888;
}

.side-card {
  min-height: 0;
  max-height: calc(58vh + 44px);
  background: #232323;
  border: 1px solid #363636;
  border-radius: 12px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.side-header {
  padding: 13px 14px;
  border-bottom: 1px solid #353535;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.side-header h2 {
  font-size: 15px;
  font-weight: 650;
}

.caption-list {
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 10px;
}

.caption-item {
  padding: 10px;
  border-radius: 8px;
  background: #2b2b2b;
  border: 1px solid #363636;
  margin-bottom: 8px;
}

.caption-item.playable {
  cursor: pointer;
}

.caption-item.playable:hover {
  border-color: #8f7a39;
  background: #302b20;
}

.caption-item.active {
  border-color: #f0c040;
  background: #332d1c;
}

.caption-meta {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  color: #999;
  font-size: 12px;
  margin-bottom: 6px;
}

.caption-text {
  color: #e8e8e8;
  line-height: 1.55;
  font-size: 13px;
}

.caption-action {
  margin-top: 6px;
  color: #c9a640;
  font-size: 12px;
}

.log-bar {
  background: #202020;
  border: 1px solid #363636;
  border-radius: 10px;
  padding: 10px 12px;
  color: #aaa;
  font-size: 13px;
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.error-text {
  color: #ffaaaa;
}

@media (max-width: 980px) {
  .page-shell {
    grid-template-rows: auto auto auto;
  }

  .main-card {
    grid-template-columns: 1fr;
  }

  .side-card {
    max-height: 260px;
  }
}

@media (max-width: 640px) {
  .form-row {
    grid-template-columns: 1fr;
  }

  .small-input,
  .start-btn {
    width: 100%;
    min-height: 42px;
  }

  .video-container {
    min-height: 0;
  }

  .status-bar {
    flex-direction: column;
    align-items: stretch;
  }

  .subtitle-overlay {
    font-size: 18px;
    bottom: 15%;
    padding: 8px 16px;
  }
}
</style>
