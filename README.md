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
| ASR | `faster-whisper`（large-v3 / large-v3-turbo，词级时间戳），无 GPU 时 mock 兜底 |
| 标签融合 | 词级时间戳 × 说话人区间重叠归并成 turn，输出 `[使用者]`/`[其他人]`/`[未知]` |
| AI 总结卡片 | 每段对话一张卡片：标题/摘要/关键信息/人物/类型/时间/原文/原始录音 |
| 日程与待办 | 仅从 `[使用者]` 发言抽取；支持中文口语时间（明天下午三点、下周一上午十点、周五之前…） |
| 指令执行 | 仅 `[使用者]` 触发；加日程/查记忆/查数据/Web 搜索 → 钉钉推送 |
| 搜索工具可切换 | Server 自实现搜索（DuckDuckGo HTML）或 DeepSeek 查询，App 内切换 |
| 定时任务 | APScheduler + 数据库持久化定义；`agent`/`push`/`command` 三种动作，日程提醒自动推送 |
| 用户画像 | 首轮生成 + 每 N 张卡片增量修正，≤600 字，注入**所有** AI 提示词；App 可编辑 |
| 多个称呼 | App 编辑 aliases，同步 Server 并进入提示词 |
| 勘误与删除 | 卡片修订历史 + 审计日志；级联清理日程/待办/会议，重算画像；软删除可恢复 |
| 会议纪要 | 多说话人长会话自动识别为会议，生成议题/决议/行动项/Markdown 纪要 |
| Agent 录音端 | DJI Mic Mini USB 采集、VAD 切片、离线暂存、失败重传、BLE 管理通道 |
| App 手机端 | Material Design 3、底部 Tab、待办首页、日程、历史卡片详情（ASR 原文 + 录音下载）、会议、画像、设置、BLE 设备管理 |

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

## 生产部署（GPU 主机）

```bash
pip install -r server/requirements-ai.txt   # faster-whisper + pyannote + torch
cp server/.env.example server/.env          # 配置 HF token / DeepSeek / 钉钉
python -m app.cli create-user --name 小明   # 输出 API Key
python -m app                              # 或 systemd / docker compose
```

完整步骤（HTTPS、Nginx、钉钉机器人、定时任务、备份）见 [`docs/deployment.md`](docs/deployment.md)。

## 测试

```bash
# Server（53 项）
cd server && python -m pytest tests

# Agent（76 项 + 1 skip，跳过项为未安装 webrtcvad 的适配器测试）
python -m pytest agent/tests

# App（10 项 + 构建验证）
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
