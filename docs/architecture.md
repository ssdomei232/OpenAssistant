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
│   ③ faster-whisper 词级时间戳 → ④ 词级/段落级对齐与 [使用者]/[其他人] 标签融合            │
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
        asr.py         # FasterWhisperASR / MockASR
        diarization.py # PyAnnoteDiarizer / EnergyDiarizer
        speaker.py     # CAM++ / Pyannote embedding / SpectralEmbedding（离线兜底）
        llm.py         # OpenAI 兼容（DeepSeek）LLM / RuleBasedLLM
        search.py      # BuiltinSearchProvider / DeepSeekSearchProvider
        push.py        # DingTalkWebhook（加签、Markdown、重试）/ NullPush
      pipeline.py      # 上传后的完整处理编排（幂等、失败重试、状态机）
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
  requirements-ai.txt  # GPU 主机：faster-whisper、pyannote、torch
```

### 2.2 处理管道（关键顺序）

1. 入库：校验、幂等（`client_segment_id` 唯一）、落对象存储、状态 `queued`。
2. 分离：pyannote → 匿名区间 `SPEAKER_00..`；离线兜底用能量/静音切分为伪说话人。
3. 声纹：对每个匿名说话人的音频算 embedding，与用户注册声纹余弦相似度 ×100 得 `similarity`；
   `similarity >= threshold(默认 60)` → `user`；有其他人声 → `other`；无法判定 → `unknown`。
   **保守策略**：只有明确超过阈值才标 `user`。
4. ASR：faster-whisper（word timestamps）；离线兜底 MockASR（开发/demo）。
5. 对齐：词级时间戳按说话人区间重叠时长归并成 turn；越界词按最近区间归属；输出
   `[使用者]`/`[其他人]` 标签的转写。
6. 总结：LLM 收到带标签的转写 + 用户画像 + 称呼 + 说话人标签语义说明；产出卡片结构化 JSON。
7. 指令/日程/待办：**只从 `user` turn 抽取**；`other` turn 只作为上下文提供给 LLM，不触发。
8. 会议：满足「时长 ≥ 阈值 且 ≥2 个说话人」时归入会议（按时间窗口聚合），生成纪要。
9. 画像：卡片落库后按策略触发增量修正，结果长度 ≤ 600 字符、注入所有 AI 任务提示词。
10. 推送：指令结果/搜索答案/定时提醒 → 钉钉；失败落 outbox 重试。

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
- BLE GATT 服务：`CONFIG`（JSON 读写）、`STATUS`、`QUEUE`（列表/重传/清空）、`WIFI`（写 SSID/密码）。
  手机 App 用 BLE 连接同名设备（原生包走 `@capacitor-community/bluetooth-le`，浏览器走 Web Bluetooth）。
- 失败重传与无网暂存均由 App 通过 BLE CONFIG 配置，并回传 Server 备份。

## 4. App

- **手机 App**：React 19 + TypeScript + Vite + MUI（Material Design 3 风格 token）+ React Router + TanStack Query，
  通过 **Capacitor** 打包为 Android/iOS 安装包（`@capacitor/android`、`@capacitor/ios`，App ID `com.openassistant.app`）；
  `pnpm dev` 仅用于调试。移动端优先布局：底部 Tab 导航、安全区、下拉刷新、触控友好控件。
- 首页：待办列表（含日程提醒/指令结果形成的关注项），支持完成/稍后。
- 日程：月/周视图 + 未来/过去列表。
- 历史记录：卡片网格 → 详情（总结、关键信息、人物、ASR 原文带身份标签、下载原始录音、勘误/删除）。
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
