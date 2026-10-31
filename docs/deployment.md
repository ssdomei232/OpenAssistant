# 部署指南

三端可以分开部署，也可以全部跑在局域网里的一台 GPU 主机 + 树莓派 + 手机上。

```
GPU 主机（Server）        树莓派（Agent）             手机（App）
FastAPI + SQLite/Postgres  DJI Mic Mini USB 麦克风     Android / iOS 安装包
FunASR / pyannote          VAD 切片 + SQLite 队列      Bluetooth LE 管理录音端
钉钉机器人 / 定时任务       BLE GATT 管理通道           API Key 访问 Server
```

## 1. Server（GPU 主机，Ubuntu 22.04 示例）

### 1.1 系统依赖

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv ffmpeg libsndfile1 git curl
```

### 1.2 安装

```bash
sudo mkdir -p /opt/openassistant && sudo chown $USER /opt/openassistant
git clone <your-repo> /opt/openassistant
cd /opt/openassistant/server
python3.11 -m venv /opt/openassistant/venv
source /opt/openassistant/venv/bin/activate
# GPU 主机：先装 CUDA 版 torch（PyPI 的 torch 是 CPU 版，torch.cuda.is_available() 会是 False）
# cu 通道按 `nvidia-smi` 右上角 CUDA Version 选择（不超过它的最新通道，如 cu126/cu130/cu132）
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu132
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
pip install -r requirements.txt        # 轻量模式：无需 GPU，mock providers 可跑通全流程
pip install -r requirements-ai.txt     # 生产模式：FunASR + pyannote（建议 CUDA，CPU 也可跑）
cp .env.example .env
```

编辑 `.env`：

```ini
OA_PUBLIC_BASE_URL=https://assistant.example.com
OA_API_KEY_PEPPER=<随机长字符串>
# 服务器默认时区（IANA 名称，默认 Asia/Shanghai）；显式配置后重启会把已有用户同步为该时区
OA_TIMEZONE=Asia/Shanghai
OA_HF_TOKEN=<HuggingFace token，pyannote 模型下载用>
# ASR（FunASR，默认 Paraformer-zh + FSMN-VAD + CT-Punc；GPU 不可用时自动回退 CPU）
OA_FUNASR_DEVICE=cuda
OA_FUNASR_HUB=ms
OA_LLM_BASE_URL=https://api.deepseek.com/v1
OA_LLM_API_KEY=<DeepSeek API Key>
OA_DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=...
OA_DINGTALK_SECRET=<钉钉加签密钥，可选>
```

> 时区修改两种方式：① `.env` 里设 `OA_TIMEZONE` 后重启 Server（会同步已有用户）；
> ② 不改配置，直接 `python -m app.cli set-timezone Asia/Shanghai` 更新已有用户。
> 若 `/me` 返回的时间不是你期望的时区，先确认这两处之一。

> 首次启动 FunASR 会通过 ModelScope 下载 ASR 模型（Paraformer-zh + FSMN-VAD + CT-Punc ≈ 1GB），
> pyannote 相关模型合计 ≈ 1GB。离线部署请提前在 GPU 主机缓存。

### 1.2.1 HuggingFace 模型授权（pyannote 必读）

pyannote 的说话人分离与声纹模型是 **gated 仓库**，仅有 token 不够，必须用同一个
HuggingFace 账号打开以下页面并接受使用条件：

- https://hf.co/pyannote/speaker-diarization-3.1
- https://hf.co/pyannote/segmentation-3.0
- https://hf.co/pyannote/embedding

授权生效后（通常几分钟内）重启 Server。验证：

```bash
python -c "from app.config import get_settings; from pyannote.audio import Pipeline; s=get_settings(); print(bool(Pipeline.from_pretrained(s.diarization_model, token=s.hf_token)))"
```

若看到 `403 GatedRepoError` / `not in the authorized list`：

1. 确认 `.env` 的 `OA_HF_TOKEN` 属于已授权账号；
2. 或先用离线方案跑通：`OA_DIARIZATION_PROVIDER=energy`、`OA_SPEAKER_PROVIDER=spectral`；
3. 代码内置**运行时降级**：真实 provider 首次失败后自动回退 energy/spectral，
   `/health` 的 mode 变为 `energy(fallback)` / `spectral(fallback)`，不会每段都失败；
   修复授权并重启后自动恢复 pyannote。

若看到 `ModuleNotFoundError: No module named 'omegaconf'`（pyannote.audio 4.x 常见）：

```bash
python -m pip install --user omegaconf
# 或安装完整 AI 依赖：pip install -r server/requirements-ai.txt
```

> embedding 模型默认 `pyannote/embedding`；若它加载失败，代码会自动尝试 pyannote 4
> 推荐的公开模型 `pyannote/wespeaker-voxceleb-resnet34-LM`（可通过 `OA_EMBEDDING_MODEL` 指定）。

pyannote.audio 4.x 也可用新模型（同样需要授权）：
`OA_DIARIZATION_MODEL=pyannote/speaker-diarization-community-1`。

### 1.2.2 模型下载慢 / 卡住（国内网络）

FunASR 默认从 **ModelScope**（`OA_FUNASR_HUB=ms`）下载模型，国内网络通常很快；若换成
`OA_FUNASR_HUB=hf` 或下载 pyannote 模型，则会走 HuggingFace。HuggingFace 直连在国内经常只有
几十 KB/s 甚至卡住，表现为 **第一段音频长时间停在 processing、日志无报错**。

推荐做法：

```ini
# server/.env
OA_FUNASR_HUB=ms                         # FunASR 走 ModelScope（默认值，国内推荐）
OA_MODELSCOPE_CACHE=./data/modelscope    # ASR 模型缓存目录（可选，默认 ~/.cache/modelscope）
OA_HF_ENDPOINT=https://hf-mirror.com      # pyannote/可选 faster-whisper 使用 HF 镜像
# OA_FUNASR_MODEL=paraformer-zh          # 也可换 SenseVoiceSmall 等模型
```

然后**预先下载模型**（失败可重跑）：

```bash
cd server
python -m app.cli download-models --only all
# 或分步：--only asr / --only pyannote
```

下载完成后重启 Server。也可以直接设置系统环境变量：

```powershell
$env:HF_ENDPOINT = "https://hf-mirror.com"
```

> 判断是否卡在下载：FunASR 模型默认缓存在 `%USERPROFILE%\.cache\modelscope\models\iic--...`
>（可用 `OA_MODELSCOPE_CACHE=./data/modelscope` 改到部署目录，便于备份与离线部署）；
> pyannote/faster-whisper 缓存在 `%USERPROFILE%\.cache\huggingface\hub\`，
> 检查其中是否有体积不再增长的 `*.incomplete` 文件。

### 1.2.4 Windows GPU：GPU 不可用 / 显存不足

FunASR 基于 PyTorch；装的是 **CUDA 版 torch** 时（见 1.2.5），轮子已自带 CUDA 运行时，一般无需额外配置 DLL。
若日志出现 GPU 相关错误（如 `CUDA out of memory`、`no CUDA-capable device`），
程序会**自动回退 CPU 并继续转写**（见日志 `FunASR GPU 不可用 ... 自动切换 CPU`）。

三种处理方式：

1. 直接使用 CPU：`OA_FUNASR_DEVICE=cpu`（能跑，但长音频明显变慢）。
2. 减小 GPU 压力：缩短 `OA_FUNASR_BATCH_SIZE_S`，或换更小的模型
   （如 `OA_FUNASR_MODEL=paraformer-zh-streaming`）。
3. 老部署若仍使用 faster-whisper（`OA_ASR_PROVIDER=faster_whisper`），遇到
   `Library cublas64_12.dll is not found` 时，按下面方式补齐 CUDA 12 + cuDNN 9：
   ```ini
   OA_CUDA_DLL_DIRS=C:\Program Files\Blackmagic Design\DaVinci Resolve
   ```
   程序也会扫描 CUDA Toolkit、pip 的 `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` 等目录；
   仍失败会自动回退 CPU int8。

新版本即使 GPU 失败也会**自动回退 CPU 并继续转写**，不会再让整条队列卡死。

### 1.2.5 torch 看不到 GPU（`torch.cuda.is_available() == False`）

`nvidia-smi` 能看到显卡、但日志出现 `FunASR GPU 不可用（torch.cuda.is_available() == False）`，
绝大多数是**装成了 CPU 版 torch**（PyPI 默认 wheel 就是 CPU 版，`torch.version.cuda == None`）。确认：

```bash
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())"
python -m app.cli doctor        # 看 gpu 字段：torch / torch_cuda / cuda_available / devices
```

修复（装与驱动匹配的 CUDA 版 torch，再用 `nvidia-smi` 右上角 CUDA Version 选通道）：

```bash
# 示例：驱动支持 CUDA 13.2 → cu132；也常用 cu126 / cu130
pip uninstall -y torch
pip install torch --index-url https://download.pytorch.org/whl/cu132
# 已装 CPU 版 torchaudio 时一并换掉（版本需与 torch 匹配）
pip install torchaudio --index-url https://download.pytorch.org/whl/cu132
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

国内下载慢可换 PyTorch 轮子镜像（pip 同上，直接给镜像 URL 下载 whl 后 `pip install ./xxx.whl`），
例如 SJTU、阿里云镜像。装好后重启 Server，`/health` 或 `doctor` 中 ASR 不再出现 CPU fallback。

> 注意：不要在一个环境里混用不同 CUDA 通道的 torch/torchaudio；升级 torch 后
> 重跑 `python -m app.cli doctor` 确认 GPU 与 provider 都正常。

### 1.2.3 录音正常但没有卡片（队列没被处理）

按顺序检查：

1. `GET /api/v1/health`：确认 `worker.enabled=true`、`worker.alive=true`；
   `audio.queued` 应逐渐下降、`audio.transcribed`（等待合并窗后总结）→ `audio.done` 上升。
   若 `worker.alive=false`，检查启动日志里的 `background worker started` 与 `OA_WORKER_ENABLED`；
   新版本会在第一个 HTTP 请求时自愈启动 worker。
2. Server 重启会自动把上次遗留的 `processing` 重新入队（`recovered_after_restart`）；
   已 `transcribed` 的片段会在下次 tick 直接总结（无需重新 ASR）。
3. 修复模型/依赖后，把历史失败片段重新入队：
   ```bash
   python -m app.cli requeue-failed
   python -m app.cli process-pending        # 或等 worker 自动处理
   ```
4. 也可临时手动跑一遍队列：`python -m app.cli process-pending`
   （会先转写，再按时间相邻关系立即合并总结，不等待合并窗口）。
5. `python -m app.cli doctor` 可打印队列状态、provider 模式与卡片数量。

> **卡片为什么延迟 20 秒左右出现？** 这是对话合并策略：片段转写后会等待
> `OA_CONVERSATION_MERGE_WINDOW_S`（默认 20s），把时间相近的多个片段合并成一张卡片统一总结，
> 避免同一段对话被 VAD 切片切成多张卡。片段此时 `status=transcribed` 属正常状态；
> 可通过 `OA_CONVERSATION_MERGE_ENABLED=false` 关闭合并（退化为每段一张卡）。

> **AI 定时合并卡片**：worker 每隔 `OA_CARD_MERGE_INTERVAL_S`（默认 300s）让 LLM 复盘最近
> `OA_CARD_MERGE_LOOKBACK_S`（默认 1800s）新增的卡片，话题相同/时间相近的卡片会合并成一张并重新总结
> （软删除其余卡片，写 `card.merge` 审计，不重复执行日程/待办/指令）。需要配置 LLM，否则自动跳过；
> 可手动补合并：`python -m app.cli merge-cards --dry-run` 先看 AI 的分组建议，去掉 `--dry-run` 执行。
> `/health` 的 `worker.card_merge` 可查看上次运行时间与合并数量。

> 新版本内置两道音频质量门控，避免生成"字幕组"之类的假卡片：
>
> - 音频 RMS 低于 `OA_MIN_SPEECH_RMS`（默认 0.004）的片段直接标记 done、不做 ASR；
> - ASR 在静音/噪声上常见的幻觉文本（中文字幕、字幕志愿者、请不吝点赞、感谢观看等）会被丢弃。
>
> 如果树莓派上传了大量 1–2 秒的底噪片段，建议在端上把 `vad.absolute_floor`
> 调到 250~300、`min_silence_ms` 调到 800，减少无效上传。

### 1.3 创建用户与 API Key

```bash
source /opt/openassistant/venv/bin/activate
cd /opt/openassistant/server
python -m app.cli create-user --name 小明 --aliases 小明 明明 "Xiao Ming"
# 输出 api_key: oa_xxxxxxxx —— 只显示一次，填入 App
```

### 1.4 启动

```bash
# 前台调试
python -m app                              # 0.0.0.0:8000
# 或 uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1

# systemd
sudo cp ../deploy/openassistant-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openassistant-server
curl http://127.0.0.1:8000/api/v1/health
```

> **单进程运行**：内置 APScheduler 与后台 worker 都是进程内组件，请保持 1 个 worker
> （`--workers 1`）。需要横向扩展时把 `OA_WORKER_ENABLED=false`，单独跑一个 worker 进程。

### 1.5 Docker（可选）

```bash
cd /opt/openassistant
cp server/.env.example server/.env && vim server/.env
docker compose -f deploy/docker-compose.yml up -d --build
docker exec openassistant-server python -m app.cli create-user --name 小明
```

### 1.6 反向代理 + HTTPS

手机 App 与 Agent 都通过 HTTPS 访问 Server（Android 明文 HTTP 需要额外配置，生产请用 HTTPS）。
`deploy/nginx.conf` 提供 Nginx 示例（同时托管 App 的 Web 构建产物）。

### 1.7 声纹注册

在 App 的「我的」页录制/上传 10–30 秒干净人声即可；也可以直接调 API：

```bash
curl -X POST https://assistant.example.com/api/v1/me/voiceprint \
  -H "X-API-Key: $OA_KEY" -F "file=@voice.wav" -F "threshold=60"
```

阈值 60 是保守值：宁可把使用者标成 `[其他人]`（漏触发），也不要把旁人误判成使用者（误触发）。

## 2. Agent（树莓派）

详细步骤见 [`agent/README.md`](../agent/README.md)，要点：

```bash
sudo apt install -y python3-venv python3-pip bluez alsa-utils ffmpeg libsndfile1
python3 -m venv ~/oa-agent && source ~/oa-agent/bin/activate
pip install -r agent/requirements.txt        # 可选依赖失败不影响核心功能
arecord -l                                    # 确认 DJI Mic Mini 作为 USB 声卡出现
sudo cp agent/systemd/openassistant-agent.service /etc/systemd/system/
sudo systemctl enable --now openassistant-agent
```

- 设备名、采样率、VAD 参数、Server 地址与 API Key 写在 `/etc/openassistant/agent.toml`。
- 时区也在该文件顶层配置：`timezone = "Asia/Shanghai"`。systemd 默认以 UTC 启动进程，
  不配置时 `recorded_at` / 日志会显示 UTC；可在 `agent.env` 用 `OA_TIMEZONE` 覆盖。
- App 通过 BLE（`OpenAssistant-*`）连接设备后，可以修改 WiFi、失败重传、无网暂存等配置；
  配置会同时回传 Server 备份（`PUT /devices/{id}/config`）。
- 断网时音频片段写入本地 SQLite 队列，恢复网络后按指数退避自动补传。

## 3. App（手机）

```bash
cd app
pnpm install
pnpm build                # 生产构建 → dist/
pnpm dev                  # 本地调试（浏览器）
```

### 3.1 生成 Android / iOS 安装包

环境要求：**JDK 17 或 21**（Gradle 8.11.1 不支持 JDK 25）、Android SDK
（`platforms/android-35`、`build-tools/35.0.0` 与 `34.0.0`、`platform-tools`）。
未装 Android Studio 时可用 Android CLI：`android sdk install platforms/android-35 build-tools/35.0.0`
（每个包一条命令）。

```bash
cd app
pnpm build
npx cap sync android
cd android
./gradlew.bat assembleDebug     # Linux/macOS: ./gradlew assembleDebug
# 产物：app/android/app/build/outputs/apk/debug/app-debug.apk
adb install -r app/releases/OpenAssistant-1.0.0-debug.apk
```

本仓库已在 Windows + JDK 21 + Android SDK 35 实际构建通过，调试包副本为
`app/releases/OpenAssistant-1.0.0-debug.apk`（4.2 MB，debug 签名）。iOS 需在 macOS 上
`npx cap add ios && npx cap open ios`。

- 手机与 Server 在同一网络时，Server 地址填 `https://assistant.example.com`；
- API Key 在 App「设置 → 服务器与 API Key」中填入；
- 原生端蓝牙使用 `@capacitor-community/bluetooth-le`（已在 AndroidManifest 声明 BLE 权限），
  浏览器调试时降级到 Web Bluetooth（Chrome/Edge）；
- 未配置 Server 时 App 自动进入**演示模式**，使用内置示例数据，方便先看 UI。

### 3.2 给 App 配 HTTPS 证书

Android 9+ 默认禁止明文 HTTP，建议使用自签/内网证书或通过 Nginx 提供 HTTPS；
调试阶段可在 `android/app/src/main/AndroidManifest.xml` 临时加
`android:usesCleartextTraffic="true"`。

## 4. 钉钉机器人

1. 钉钉群 → 群设置 → 机器人 → 添加机器人 → 自定义（Webhook）。
2. 安全设置选择「加签」，把 `secret` 填到 `OA_DINGTALK_SECRET`。
3. 把 Webhook 填到 `OA_DINGTALK_WEBHOOK`，重启 Server。
4. App「设置 → 钉钉推送自检」发送测试消息验证。

推送覆盖：指令执行结果、资料查询结果、日程提醒、定时任务、勘误/删除通知。

## 5. 定时任务

通过 App/API 创建，例如每晚 21:00 汇总次日日程与待办并推送：

```bash
curl -X POST https://assistant.example.com/api/v1/tasks/scheduled \
  -H "X-API-Key: $OA_KEY" -H "Content-Type: application/json" \
  -d '{"name":"晚间日程提醒","cron":"0 21 * * *","timezone":"Asia/Shanghai",
       "prompt":"汇总明天的日程和未完成待办，用钉钉提醒我。","action":"agent"}'
```

- `action=agent`：LLM 结合日程/待办生成推送文案；
- `action=push`：直接把 `prompt` 当 Markdown 推送；
- `action=command`：把 `prompt` 当指令执行（例如“提醒我周五交报告”）。
- 一次性任务用 `run_at` 代替 `cron`。

## 6. 演示数据

```bash
cd server
OA_DEMO_MODE=1 python -m app.cli seed-demo --reset --api-key oa_demo_key
```

会生成：用户 + 声纹 + 画像、5 张卡片（2 场会议、1 条勘误过的视频、删除示例）、
日程/待办、指令记录、审计日志、设备与定时任务。App 配置 Server 地址与
`oa_demo_key` 后即可看到完整数据。

## 7. 备份与恢复

- SQLite：备份 `server/data/openassistant.db`（WAL 模式下同时复制 `-wal`/`-shm`，或先 `sqlite3 db ".backup"`）。
- 音频：备份 `server/data/storage/`。
- 恢复：停服 → 覆盖 db 与 storage → 启动。

```bash
# 简易备份（cron 每日 3:00）
sqlite3 /opt/openassistant/server/data/openassistant.db ".backup '/backup/oa-$(date +%F).db'"
tar czf /backup/oa-storage-$(date +%F).tar.gz -C /opt/openassistant/server/data/storage .
```

## 8. 安全建议

- `OA_API_KEY_PEPPER` 使用随机值；API Key 只保存 sha256 摘要。
- 只在内网/HTTPS 暴露 Server；Nginx 限制请求体大小（音频片段通常 < 2MB）。
- 钉钉/LLM/HF Token 使用环境变量或 secret manager，不要提交到 git。
- 数据库中的设备 WiFi 密码使用轻量加密存储（`app.utils.encrypt_secret`），
  生产环境建议替换为 KMS/AES-GCM。
- 删除操作是软删除并保留审计日志，误删可通过 `POST /cards/{id}/restore` 恢复。
