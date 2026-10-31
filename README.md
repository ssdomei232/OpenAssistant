# OpenAssistant

个人日常语音记录与智能助手系统（**Server / Agent / App 三端**）。

佩戴式设备（树莓派 + DJI Mic Mini）全程录音 → 本地 VAD 切片上传 → Server 说话人分离 +
声纹识别 → ASR 转写 → 按 `[使用者]` / `[其他人]` 打标签 → AI 总结成卡片 → 提取日程/待办 →
指令执行 → 钉钉推送；App 通过 Material Design 手机端呈现卡片、日程、待办、会议纪要、用户画像，
并通过蓝牙管理录音端。

> 需求原文见 [`requirement.md`](requirement.md)，接口契约见 [`docs/api.md`](docs/api.md)，
> 架构设计见 [`docs/architecture.md`](docs/architecture.md)，部署步骤见 [`docs/deployment.md`](docs/deployment.md)。

## 目录结构

```
OpenAssistant/
├── requirement.md            # 需求
├── docs/
│   ├── api.md                # HTTP API 契约（三端共同遵守）
│   ├── architecture.md       # 架构、数据模型、身份标签约束
│   └── deployment.md         # Server / Agent / App 部署指南
├── server/                   # FastAPI 后端（ASR/说话人/总结/日程/指令/定时/钉钉）
│   ├── app/
│   │   ├── api/              # 路由：core/audio/cards/planner/meetings/commands/search/automation
│   │   ├── services/         # 管道、对齐、总结、指令、画像、会议、调度、勘误、推送
│   │   ├── providers/        # ASR / diarization / speaker / LLM / search / push 适配器
│   │   ├── prompts.py        # 提示词（含全局注入的用户画像与身份规则）
│   │   ├── demo.py           # 离线演示数据生成
│   │   └── cli.py            # 建用户、seed-demo、处理队列、启动服务
│   └── tests/                # 53 个 pytest（管道/身份约束/勘误/会议/定时/设备…）
├── agent/                    # 树莓派录音端（VAD 切片、离线队列、BLE 管理）
│   ├── openassistant_agent/  # capture/vad/segmenter/spool/uploader/ble/service/cli
│   ├── tests/                # pytest
│   ├── systemd/              # 开机自启服务
│   └── README.md             # 树莓派部署说明
├── app/                      # Material Design 手机 App（React + MUI + Capacitor）
│   ├── src/pages/            # 首页/日程/历史/会议/我的/设置/设备
│   └── README.md             # 开发、构建、Android/iOS 打包、BLE 说明
└── deploy/                   # Dockerfile、docker-compose、Nginx、systemd
```

## 核心能力（对应需求）

| 需求 | 实现 |
|---|---|
| 说话人分离 | `pyannote.audio`（生产）/ 能量 VAD 分段（离线兜底） |
| 声纹识别 | 声纹注册（10–30s）→ embedding 余弦相似度 ×100，阈值默认 **60** 保守判定；未达阈值一律 `[其他人]`/`[未知]` |
| ASR | `FunASR`（Paraformer-zh + FSMN-VAD + CT-Punc，词级时间戳、中文标点恢复），GPU 不可用时自动回退 CPU；依赖缺失时 mock 兜底 |
| 标签融合 | 词级时间戳 × 说话人区间重叠归并成 turn，输出 `[使用者]`/`[其他人]`/`[未知]` |
| AI 总结卡片 | 每段对话一张卡片：标题/摘要/关键信息/人物/类型/时间/原文/原始录音 |
| 对话合并总结 | 转写完成后等待可配置窗口（默认 20s），把时间相近（间隔 ≤45s）的多个音频片段合并成一次对话、只生成一张卡片，避免 VAD 切片把同一段对话切碎 |
| AI 复盘合并 | 后台每 5 分钟让 LLM 复盘近期新增卡片，话题相同/时间相近的卡片自动合并成一张并重新总结（软删除其余卡片、写审计、不重复执行日程/待办/指令） |
| 手动合并对话 | App 历史记录页多选多张卡片，`POST /cards/merge` 合并成一张并重新总结；日程/待办改挂不重复执行，写 `card.merge` 审计 |
| 低价值音频丢弃 | 过于简短/无意义的人声（只有语气词/应答、空语义短片段）由 AI 判断是否丢弃，不生成卡片；含指令/日程/待办或转写信息量足够的音频绝不丢弃 |
| 日程与待办 | 仅从 `[使用者]` 发言抽取；支持中文口语时间（明天下午三点、下周一上午十点、周五之前…） |
| 指令执行 | 仅 `[使用者]` 触发；加日程/查记忆/查数据/Web 搜索 → 钉钉推送 |
| 搜索工具可切换 | Server 自实现搜索（DuckDuckGo HTML）或 DeepSeek 查询，App 内切换 |
| 定时任务 | APScheduler + 数据库持久化定义；`agent`/`push`/`command` 三种动作，日程提醒自动推送 |
| 用户画像 | 首轮生成 + 每 N 张卡片增量修正，≤600 字，注入**所有** AI 提示词；App 可编辑 |
| 多个称呼 | App 编辑 aliases，同步 Server 并进入提示词 |
| 勘误与删除 | 卡片修订历史 + 审计日志；级联清理日程/待办/会议，重算画像；软删除可恢复 |
| 会议纪要 | 多说话人长会话自动识别为会议，生成议题/决议/行动项/Markdown 纪要 |
| Agent 录音端 | DJI Mic Mini USB 采集、VAD 切片、离线暂存、失败重传、BLE 管理通道；采样率支持 8/16/32/48 kHz（48 kHz 保留麦克风原始采样，Server 抗混叠重采样到 16 kHz 供 ASR/声纹使用）；支持配置运行时时区（`agent.toml` 的 `timezone` / `OA_TIMEZONE`），systemd 为 UTC 时时间戳与日志仍为本地时间 |
| App 手机端 | Material Design 3、底部 Tab、待办首页、日程、历史卡片详情（ASR 原文 + 录音下载 + 多选合并对话）、会议、画像、设置、BLE 设备管理 |

## 快速开始（本地演示，无需 GPU / API Key）

### 1. Server

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env

# 生成演示数据（用户 + 声纹 + 5 张卡片 + 2 场会议 + 日程/待办/指令/审计/定时任务）
OA_DEMO_MODE=1 python -m app.cli seed-demo --reset --api-key oa_demo_key

# 启动（http://127.0.0.1:8000，文档 /docs）
OA_DEMO_MODE=1 python -m app
```

演示模式使用离线 mock providers（mock ASR / 能量分离 / 谱特征声纹 / 规则总结 / 静态搜索），
不联网也能跑通「上传音频 → 卡片 → 日程 → 指令 → 推送」全链路。

### 2. App

```bash
cd app
pnpm install
pnpm dev          # 浏览器调试
pnpm build        # 生产构建
pnpm test         # vitest
```

Android / iOS 安装包（`app/android/` 已生成，**已实际构建出调试 APK**：`app/releases/OpenAssistant-1.0.0-debug.apk`）：

```bash
cd app
pnpm build && npx cap sync android
cd android && ./gradlew.bat assembleDebug   # 产物 app/android/app/build/outputs/apk/debug/app-debug.apk
adb install -r app/releases/OpenAssistant-1.0.0-debug.apk
```

需要 JDK 17/21 + Android SDK（`platforms/android-35`、`build-tools/35.0.0`、`build-tools/34.0.0`、`platform-tools`），
详细步骤与签名说明见 [`app/README.md`](app/README.md)。iOS 需在 macOS 上 `npx cap add ios`。

打开 App 后在「设置」里填写 Server 地址与 `oa_demo_key`；不配置则进入演示模式查看内置数据。

### 3. Agent（树莓派）

```bash
pip install -r agent/requirements.txt
python -m openassistant_agent selftest          # 合成音频跑通 VAD→切片→入队→上传
python -m openassistant_agent run --config /etc/openassistant/agent.toml
```

详见 [`agent/README.md`](agent/README.md)。

> Agent 时区：`agent.toml` 顶层 `timezone = "Asia/Shanghai"`（或环境变量 `OA_TIMEZONE`）决定
> `recorded_at` / 片段 ID / 日志使用的时间。systemd 默认以 UTC 启动进程，树莓派上请显式配置本地时区；
> 临时覆盖可用 `--timezone`，修改已有配置可用 `config set timezone Asia/Shanghai`。

## 生产部署（GPU 主机）

```bash
# GPU 主机：先装 CUDA 版 torch（PyPI 默认是 CPU 版；cu 通道按 nvidia-smi 的 CUDA Version 选）
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu132
pip install -r server/requirements-ai.txt   # FunASR + pyannote + torch 依赖
cp server/.env.example server/.env          # 配置 HF token / DeepSeek / 钉钉 / 时区
python -m app.cli create-user --name 小明   # 输出 API Key
python -m app.cli doctor                    # 检查 gpu.cuda_available / providers.asr
python -m app                              # 或 systemd / docker compose
```

> 时区：`server/.env` 里的 `OA_TIMEZONE`（IANA 名称，默认 `Asia/Shanghai`）决定新建用户默认时区，
> 并作为用户时区为空时的兜底；显式配置后重启会把已有用户同步过去，也可用
> `python -m app.cli set-timezone Asia/Shanghai` 单独修改。App 内 `PATCH /me` 同样可改用户时区。

完整步骤（HTTPS、Nginx、钉钉机器人、定时任务、备份）见 [`docs/deployment.md`](docs/deployment.md)。

> **pyannote 模型授权**：说话人分离 / 声纹模型是 HuggingFace gated 仓库，需要先用
> `OA_HF_TOKEN` 对应账号在网页上接受使用条件，否则会 403。即使未授权，服务也会在
> 首次失败后自动降级为 energy/spectral（`/health` 显示 `energy(fallback)`），不会中断录音处理；
> 详见 deployment 文档 1.2.1。

## 测试

```bash
# Server（117 项）
cd server && python -m pytest tests

# Agent（85 项 + 1 skip，跳过项为未安装 webrtcvad 的适配器测试）
python -m pytest agent/tests

# App（11 项 + 构建验证）
cd app && pnpm test && pnpm build
```

Server 测试覆盖关键验收点：身份标签、**其他人的指令绝不触发**、日程只来自使用者、
勘误级联清理、画像重算、会议纪要、定时任务、搜索切换、钉钉推送、设备配置加密。

## API 速览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查与 provider 模式 |
| GET/PATCH | `/api/v1/me` | 账号、称呼、时区、搜索工具 |
| GET/POST/DELETE | `/api/v1/me/voiceprint` | 声纹注册/自检 |
| GET/PUT/POST | `/api/v1/me/profile` | 用户画像（查看/修改/重新生成） |
| POST | `/api/v1/audio/segments` | Agent 上传音频片段（幂等） |
| GET | `/api/v1/cards` / `/cards/{id}` | 总结卡片列表/详情（含 ASR 原文） |
| PATCH/DELETE | `/api/v1/cards/{id}` | 勘误 / 删除（级联 + 审计） |
| GET/POST/PATCH/DELETE | `/api/v1/schedule` `/todos` | 日程与待办 |
| GET | `/api/v1/meetings` `/meetings/{id}` | 会议与纪要 |
| POST | `/api/v1/commands` | 下发指令（等同使用者说话） |
| POST | `/api/v1/search` | Web 搜索（builtin / deepseek） |
| GET/POST/PATCH/DELETE | `/api/v1/tasks/scheduled` | 定时任务 |
| POST | `/api/v1/push/test` | 钉钉推送自检 |
| GET/PUT | `/api/v1/devices/{id}/config` | App BLE 配置回传 Server 备份 |
| GET | `/api/v1/audit` | 勘误/删除/配置审计 |

完整字段见 [`docs/api.md`](docs/api.md)。

## 安全与隐私

- API Key 只存 `sha256(pepper + key)` 摘要；每个用户数据隔离。
- 音频默认存本地对象存储（可替换 S3），删除卡片不自动删除原始音频（便于追溯）。
- 设备 WiFi 密码在服务端加密存储，读取时打码。
- 钉钉机器人支持加签，推送失败进入 outbox 指数退避重试。
- 软删除 + 审计日志，支持 `POST /api/v1/cards/{id}/restore` 恢复误删。
