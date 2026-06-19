
## 16s
python3 /data/yjc/eval_qwen3vl_video.py --input /data/yjc/video/slices/30fps/v09_000.mp4 --fps 1


## 5s
python3 /data/yjc/eval_qwen3vl_video.py --input /data/yjc/video/slice2/v01_000_00.mp4 --fps 1



## 部署参数
qwen3vl8b
部署参数
dtype=bfloat16 gpu-memory-utilization=0.90 max-model-len
显卡参数
3090 24GB

15s 1080p 的 30fps 视频

max-model-len=8k 采样fps=1 爆max lens

max len=16k 部署不起来 显存不够

max len=16000 可以部署 一跑又爆了

max len=12000  一跑又显存 OOM（多模态视频编码阶段）

max len=10000  爆max lens

输入大概有 11883 tokens


- 10000：服务稳，但请求直接超长，不能跑
- 12000：能接住请求，但视频编码阶段 OOM
- 16000：服务能起，但首个视频请求 OOM
- 16384：服务启动阶段就起不来

## 汇报一
代码和视频切片都在/data/yjc
目前测了一下，感觉显卡不太够用

显卡参数
3090 24GB

qwen3vl8b vllm部署参数
dtype=bfloat16 gpu-memory-utilization=0.90 max-model-len调整

对15s 1080p 的 30fps 视频切片做理解

max-model-len=8k 采样fps=1 爆模型上下文
max-len=16k 部署不起来 显存不够
max-len=16000 可以部署 一跑又爆oom
max-len=12000  一跑又显存 OOM（多模态视频编码阶段）
max-len=10000  爆模型上下文
这个实验的输入大概有 11883 tokens 

可以考虑降低视频分辨率（已经是 1080p了），或者换大显存显卡   降低采样帧数（1 已经很低了）






--max-pixels 的作用是：限制视频每一帧送进视觉模型前的最大像素预算，也就是控制单帧分辨率上限。


## max-pixels

python3 /data/yjc/eval_qwen3vl_video.py \
--input /data/yjc/video/slices/30fps/v10_000.mp4 \
--fps 1 \
--max-pixels $((360*420))





