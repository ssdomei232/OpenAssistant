# 部署指南

三端可以分开部署，也可以全部跑在局域网里的一台 GPU 主机 + 树莓派 + 手机上。

```
GPU 主机（Server）        树莓派（Agent）             手机（App）
FastAPI + SQLite/Postgres  DJI Mic Mini USB 麦克风     Android / iOS 安装包
faster-whisper / pyannote  VAD 切片 + SQLite 队列      Bluetooth LE 管理录音端
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
pip install -r requirements.txt        # 轻量模式：无需 GPU，mock providers 可跑通全流程
pip install -r requirements-ai.txt     # 生产模式：faster-whisper + pyannote（需 CUDA）
cp .env.example .env
```

编辑 `.env`：

```ini
OA_PUBLIC_BASE_URL=https://assistant.example.com
OA_API_KEY_PEPPER=<随机长字符串>
OA_HF_TOKEN=<HuggingFace token，pyannote 模型下载用>
OA_LLM_BASE_URL=https://api.deepseek.com/v1
OA_LLM_API_KEY=<DeepSeek API Key>
OA_DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=...
OA_DINGTALK_SECRET=<钉钉加签密钥，可选>
```

> 首次启动 faster-whisper / pyannote 会下载模型（large-v3-turbo ≈ 1.6GB，
> pyannote 相关模型合计 ≈ 1GB）。离线部署请提前在 GPU 主机缓存。

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
