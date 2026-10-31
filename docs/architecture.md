# OpenAssistant 架构设计

## 1. 系统总览

```
┌──────────────────────────────┐        BLE (GATT)         ┌──────────────────────────┐
│  树莓派 Agent                 │◀─────────────────────────▶│  App（Material Design）   │
│  DJI Mic Mini → 录音          │   WiFi 配置 / 重传 / 暂存  │  待办 / 日程 / 历史 / 会议 │
│  VAD 切片 → 本地队列           │                            │  画像 / 设置 / 设备管理    │
└──────────────┬───────────────┘                            └────────────┬─────────────┘
               │ HTTPS 上传音频片段（含元数据、幂等 ID）                    │ HTTPS + API Key
               ▼                                                        ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  Server（FastAPI）                                                                     │
│                                                                                      │
│  音频入库 → 任务队列 → 处理管道                                                        │
│   ① pyannote 说话人分离 → ② 声纹匹配（CAM++/pyannote + 余弦阈值 60）                    │
│   ③ FunASR 词级时间戳与标点 → ④ 词级/段落级对齐与 [使用者]/[其他人] 标签融合            │
│   ⑤ AI 总结 → 卡片；仅 [使用者] 触发指令/日程/待办                                      │
│   ⑥ 会议识别 → 会议纪要；⑦ 用户画像增量修正（简短、全局注入）                            │
│                                                                                      │
│  指令路由（仅使用者）：加日程 / 查记忆 / 查数据 / Web 搜索 → 钉钉推送                    │
│  APScheduler 定时任务 → 指令 / 提醒 → 钉钉                                              │
│  勘误与删除 → 级联清理卡片/日程/待办/纪要/画像 + 审计                                    │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

## 2. Server

### 2.1 目录结构

```
server/
  app/
    main.py            # FastAPI 应用装配、异常处理、生命周期
    config.py          # pydantic-settings，全部可用环境变量覆盖
    db.py              # SQLAlchemy engine/session，SQLite/PostgreSQL 通用
    models.py          # ORM：User/Voiceprint/Card/TranscriptTurn/ScheduleItem/Todo/
                       #      Meeting/Command/ScheduledJob/AuditLog/Device/AudioSegment
    schemas.py         # Pydantic 请求/响应模型
    deps.py            # 认证、当前用户、DB 会话、服务容器
    api/               # 路由：me, voiceprint, audio, cards, schedule, todos,
                       #       meetings, commands, search, tasks, push, devices, audit, health
    services/
      providers/       # 可替换适配器
        asr.py         # FunASRASR（生产默认）/ FasterWhisperASR（回退）/ MockASR
        diarization.py # PyAnnoteDiarizer / EnergyDiarizer
        speaker.py     # CAM++ / Pyannote embedding / SpectralEmbedding（离线兜底）
        llm.py         # OpenAI 兼容（DeepSeek）LLM / RuleBasedLLM
        search.py      # BuiltinSearchProvider / DeepSeekSearchProvider
        push.py        # DingTalkWebhook（加签、Markdown、重试）/ NullPush
      pipeline.py      # 上传后的完整处理编排（转写 → 等待窗口 → 对话合并总结、幂等、状态机）
      card_merge.py    # AI 定时复盘合并近期卡片（跨卡片去重、重新总结、审计）
      align.py         # 词级时间戳 × 说话人区间 对齐与标签融合
      summarizer.py    # 卡片总结（含日程/待办/指令抽取，只有使用者触发）
      commands.py      # 指令意图解析与执行、结果推送
      profile.py       # 画像初始生成与增量修正（长度受控）
      meetings.py      # 会议检测（多说话人长会话）与纪要生成
      scheduler.py     # APScheduler 装配、cron/一次性任务、执行器
      corrections.py   # 勘误、删除、级联清理、审计
      storage.py       # 本地对象存储（可换 S3），签名 URL
      jobs.py          # 进程内任务执行器（线程池 + DB 队列，可换 Celery）
    prompts/           # 提示词模板（画像、总结、纪要、指令、搜索归纳）
  tests/               # pytest：管道/身份标签/指令触发/级联删除/API 契约
  requirements.txt     # 轻量运行依赖（离线可用 mock providers）
  requirements-ai.txt  # GPU 主机：FunASR、pyannote、torch
```

### 2.2 处理管道（关键顺序）

管道拆成**转写**与**总结**两阶段，中间留出「对话合并等待窗口」，避免把一段连续对话
按 VAD 切片切碎、生成多张卡片；等待期间 worker 继续转写新片段，不会阻塞。

1. 入库：校验、幂等（`client_segment_id` 唯一）、落对象存储、状态 `queued`。
   支持 8/16/32/48 kHz 单声道 wav（Agent 可用 DJI Mic Mini 原生 48 kHz），
   Server 内部统一重采样到 16 kHz（降采样前做窗函数 sinc 低通抗混叠），ASR/声纹模型固定 16 kHz 输入。
2. 分离：pyannote → 匿名区间 `SPEAKER_00..`；离线兜底用能量/静音切分为伪说话人。
   短于 `OA_DIARIZATION_MIN_SECONDS`（默认 1s）的片段跳过 pyannote，直接视为单一说话人区间，
   避免极短输入导致模型数值退化（`std(correction=1)` / 空簇均值告警）。
3. 声纹：对每个匿名说话人的音频算 embedding，与用户注册声纹余弦相似度 ×100 得 `similarity`；
   embedding 输入不足 `OA_EMBEDDING_MIN_SECONDS`（默认 0.5s）时反射填充，避免极短片段声纹失真；
   `similarity >= threshold(默认 60)` → `user`；有其他人声 → `other`；无法判定 → `unknown`。
   **保守策略**：只有明确超过阈值才标 `user`。
4. ASR：FunASR（Paraformer-zh + FSMN-VAD + CT-Punc，词级时间戳与中文标点）；GPU 不可用时自动
   回退 CPU；未安装 FunASR 的老部署可回退 faster-whisper，离线兜底 MockASR（开发/demo）。
5. 对齐：词级时间戳按说话人区间重叠时长归并成 turn；越界词按最近区间归属；输出
   `[使用者]`/`[其他人]` 标签的转写。
6. **转写落库（阶段一）**：转写结果（turns + 说话人身份信息）写入 `AudioSegment.meta.transcript`，
   片段状态 `transcribed`；此时**不生成卡片**。
7. **对话合并与总结（阶段二）**：片段转写完成后等待 `OA_CONVERSATION_MERGE_WINDOW_S`（默认 20s），
   看是否还有时间相近的音频完成转写。把**同一用户**、录制时间相邻
   （相邻片段间隔 ≤ `OA_CONVERSATION_MERGE_GAP_S`，默认 45s）的片段合并为一次对话：
   - 拼接各片段 turns，按录制时间换算到统一时间线；
   - 只调用一次 LLM / 只生成**一张卡片**，日程/待办/指令/会议/画像更新都只执行一次；
   - 单对话最多 `OA_CONVERSATION_MERGE_MAX_SEGMENTS`（12）段 / `OA_CONVERSATION_MERGE_MAX_DURATION_S`（900s），
     超出则拆分为新对话；
   - 若同用户还有时间相邻的片段在 `queued/processing`，会先等它转写完再总结；
   - `OA_CONVERSATION_MERGE_ENABLED=false` 可关闭合并（退化为每段一张卡片）。
   `process_now=true` 的上传/CLI 手动处理不等待窗口，立即总结该片段所在对话。
   片段级合并之外，worker 还会定时执行 **AI 复盘合并**（见 2.7），把仍被拆成多张的同一对话卡片
   再次合并并重新总结。
   总结时 LLM 收到带标签的转写 + 用户画像 + 称呼 + 说话人标签语义说明；产出卡片结构化 JSON。
   若整段音频过于简短或无意义（只有“嗯/啊/好的/谢谢”等语气词应答，或空语义的短片段），
   LLM 可返回 `discard: true` 选择丢弃：**不生成卡片**，也不触发日程/待办/指令，
   片段记为 `done`、`error=discarded_by_ai` 并在 `meta.discard` 写入原因。
   无 LLM 时用保守规则兜底（纯语气词、有效字数 ≤ 阈值且无时间/数字等信号 → `discarded_low_value_audio`）。
   只要抽到了日程/待办/指令，或转写信息量明显足够，即使 LLM 请求丢弃也会保留卡片。
8. 指令/日程/待办：**只从 `user` turn 抽取**；`other` turn 只作为上下文提供给 LLM，不触发。
9. 会议：满足「时长 ≥ 阈值 且 ≥2 个说话人」时归入会议（按时间窗口聚合），生成纪要。
10. 画像：卡片落库后按策略触发增量修正，结果长度 ≤ 600 字符、注入所有 AI 任务提示词。
11. 推送：指令结果/搜索答案/定时提醒 → 钉钉；失败落 outbox 重试。

### 2.3 身份区分硬约束

- `Speaker` 枚举只有 `user/other/unknown`。
- `summarizer`、`commands`、`schedule extraction` 三层都校验来源说话人，且均有单元测试覆盖
  “其他人的指令不得执行”。
- `unknown` 视为其他人处理（不触发操作），但保留在转写中作为上下文。

### 2.4 定时任务

- APScheduler `BackgroundScheduler` + SQLAlchemyJobStore（持久化，重启恢复）。
- 任务类型：`agent`（把 prompt 交给 LLM 执行，如“汇总今天日程”）、`push`（直接推送）、`command`（执行指令）。
- 每次执行结果写 `last_run_at/last_result`；失败重试 3 次后告警日志。

### 2.5 勘误与删除（可追溯）

- `Card` 保留 revision 历史（`AuditLog` + `CardCorrection`）。
- 删除/改类型 → 级联：`ScheduleItem(card_id)`、`Todo(card_id)`、`Meeting(card_ids)` 软删除；
  画像置 dirty 并异步重算；推送给用户“已按勘误更新”。
- 所有操作写审计日志，App 可查询。

### 2.6 时区

- 存储与 API 时间统一用 **UTC**（数据库存 naive UTC，接口返回 `...Z`），App 负责按本地时区展示。
- 服务器默认时区由 `OA_TIMEZONE` 配置（IANA 名称，默认 `Asia/Shanghai`）：
  - 新建用户默认使用它；
  - `user.timezone` 为空或非法时，日程解析、指令执行、画像、会议纪要、定时任务均回落到它；
  - 写在 `.env`/环境变量里即视为显式配置，**启动时会把已有用户的时区同步为该值**
    （之后 App 的 `PATCH /me` 仍可单独修改）。
- 交给 LLM 的“当前时间/会议时间”以及会议纪要里展示的时间都换算成**用户本地时间**，
  避免“明天下午三点”被按 UTC 解析导致 8 小时偏移。
- 修改已有用户：`.env` 设置 `OA_TIMEZONE` 后重启，或
  `python -m app.cli set-timezone Asia/Shanghai [--user-id usr_xxx]`。

### 2.7 AI 复盘合并卡片（定时）

片段级合并只能处理「上传到达时间」相近的片段；网络延迟、重传、合并窗口刚好错过时，
同一场对话仍可能留下多张卡片。为此后台 worker 每隔 `OA_CARD_MERGE_INTERVAL_S`（默认 300s）
执行一次 AI 复盘合并（`services/card_merge.py`）：

1. 取最近 `OA_CARD_MERGE_LOOKBACK_S`（默认 1800s）内**新增**的 `conversation` 卡片
   （最多 `OA_CARD_MERGE_MAX_CARDS` 张，会议/媒体卡片不参与）；
2. 用 LLM 判断哪些卡片属于同一场对话（话题相同/人物相同/时间连续），返回合并分组；
   组内时间跨度超过 `OA_CARD_MERGE_MAX_SPAN_S`（默认 3600s）会被拒绝；
3. 对每个分组：
   - 保留时间最早的主卡片，合并各卡片的 turns 并按时间线重排，重新调用总结生成统一的
     标题/摘要/关键信息/人物；
   - 其余卡片软删除，写 `AuditLog(action=card.merge)` 审计（可追溯、`after.merged_into` 指向主卡片）；
   - 已产生的日程/待办/指令**不重新执行**，仅把 `card_id` 改挂到主卡片，避免重复推送/创建；
   - `source_meta` 记录 `ai_merged/ai_merge_reason/ai_merged_card_ids`，画像置 dirty 后刷新；
4. AI 不可用 / 调用失败时跳过本轮，不做基于规则的跨卡片合并，避免误合并不同话题。

可手动触发补合并：`python -m app.cli merge-cards [--lookback 1800] [--dry-run]`；
`OA_CARD_MERGE_ENABLED=false` 可关闭。worker 状态在 `/health` 的 `worker.card_merge` 中可见。

## 3. Agent（树莓派）

```
agent/
  openassistant_agent/
    config.py     # 配置合并：默认值 < /etc/openassistant/agent.toml < 环境变量
    capture.py    # sounddevice 采集（DJI Mic Mini USB），失败时 file 回放源（开发）
    vad.py        # webrtcvad 优先，能量 VAD 兜底；preroll/postroll
    segmenter.py  # 语音切段、最短/最长片段、静音合并
    spool.py      # SQLite 本地队列：pending/uploading/uploaded/failed，元数据与校验和
    uploader.py   # httpx 上传、指数退避重试、网络探测、断网继续入库
    ble.py        # bless GATT Server（配置/状态/队列操作/测试连接），无 BT 时降级 mock
    service.py    # 常驻主循环：采集→切片→落盘→入队→上传
    cli.py        # init/run/status/flush/config/selftest
  tests/          # pytest：VAD、切片、队列重试、断网暂存、BLE 协议编解码
  systemd/openassistant-agent.service
```

- 断网：`uploader` 探测失败即停止上传但继续写 spool；恢复后按序重传，指数退避 1s→2s→…→300s。
- 时区：`agent.toml` 顶层 `timezone`（或 `OA_TIMEZONE`）决定 `recorded_at` / `client_segment_id` / 日志的时区；
  启动时设置进程 `TZ` 并 `tzset`，因此 systemd 以 UTC 启动时仍按本地时区记录（`recorded_at` 带偏移，Server 统一转 UTC）。
- BLE GATT 服务：`CONFIG`（JSON 读写）、`STATUS`、`QUEUE`（列表/重传/清空）、`WIFI`（写 SSID/密码）。
  手机 App 用 BLE 连接同名设备（原生包走 `@capacitor-community/bluetooth-le`，浏览器走 Web Bluetooth）。
- 失败重传与无网暂存均由 App 通过 BLE CONFIG 配置，并回传 Server 备份。

## 4. App

- **手机 App**：React 19 + TypeScript + Vite + MUI（Material Design 3 风格 token）+ React Router + TanStack Query，
  通过 **Capacitor** 打包为 Android/iOS 安装包（`@capacitor/android`、`@capacitor/ios`，App ID `com.openassistant.app`）；
  `pnpm dev` 仅用于调试。移动端优先布局：底部 Tab 导航、安全区、下拉刷新、触控友好控件。
- 首页：待办列表（含日程提醒/指令结果形成的关注项），支持完成/稍后。
- 日程：月/周视图 + 未来/过去列表。
- 历史记录：卡片网格 → 详情（总结、关键信息、人物、ASR 原文带身份标签、下载原始录音、勘误/删除）；
  支持**多选手动合并**多个对话卡片并重新总结（`POST /cards/merge`，会议卡片不可合并）。
- 会议纪要：会议列表 → 纪要详情（议题/决议/行动项）。
- 用户画像：查看/编辑/重新生成；多个姓名称呼管理并同步 Server。
- 设置：服务器地址/API Key、搜索工具选择、钉钉推送自检、设备（BLE）管理、演示数据开关。
- 设备页：BLE 扫描/连接、WiFi 配置、失败重传参数、无网暂存开关、队列状态与手动重传。
- `demo mode`：未配置服务器时使用内置 fixtures，保证 UI 可独立演示；发现配置后走真实 API。
- 采用移动优先布局，适配桌面。

## 5. 部署

- Server：Docker 或 systemd；`.env` 配置 API Key、LLM/钉钉/搜索；GPU 主机安装 `requirements-ai.txt`。
- Agent：树莓派 systemd 服务，配置文件 `/etc/openassistant/agent.toml`。
- App：`pnpm build` 后 `npx cap sync` 生成 Android/iOS 工程并打包安装包；Web 产物也可由 Nginx 托管为 PWA。
  原生端 BLE 使用 Capacitor 插件（Android 已声明 `BLUETOOTH_SCAN`/`BLUETOOTH_CONNECT`），浏览器端降级 Web Bluetooth。
- 附 `deploy/docker-compose.yml`、Nginx、systemd、备份脚本。

## 6. 数据模型（核心表）

| 表 | 关键字段 |
|---|---|
| users | id, display_name, aliases(JSON), timezone, api_key_hash, search_provider, settings(JSON) |
| voiceprints | user_id, embedding(JSON), threshold, quality, sample_seconds |
| audio_segments | id, user_id, client_segment_id(uniq), path, sha256, status, meta(JSON), card_id |
| cards | id, user_id, title, summary, key_points(JSON), people(JSON), type, occurred_at, duration_s, audio_segment_id, meeting_id, deleted_at |
| transcript_turns | card_id, seq, start, end, speaker, text |
| schedule_items | id, user_id, title, start_at, end_at, location, source, card_id, deleted_at |
| todos | id, user_id, title, status, due_at, source, card_id, deleted_at |
| meetings | id, user_id, title, started_at, ended_at, minutes, participants(JSON), action_items(JSON), deleted_at |
| commands | id, user_id, text, intent, status, result, pushed, card_id |
| scheduled_jobs | id, user_id, name, cron, run_at, prompt, action, enabled, last_run_at |
| audit_logs | id, user_id, action, entity, entity_id, before(JSON), after(JSON) |
| devices | id, user_id, name, config(JSON), last_seen |
| push_outbox | id, user_id, text, status, attempts, next_attempt_at |
