# OpenAssistant Server API（v1，契约文档）

> 本文件是 Server / App / Agent 三端共同遵守的接口契约。任何一端的实现都必须与本文一致。
> 基础路径：`/api/v1`。除 `/health`、`/auth/*` 外，所有接口都需要认证头。

## 0. 认证

```
X-API-Key: <api_key>
Authorization: Bearer <api_key>     # 二者等价，任选其一
```

- API Key 与用户账号一一关联，Server 只保存 `sha256` 摘要。
- 缺少/无效 Key → `401 {"detail": "invalid_api_key"}`。
- 每个用户的数据互相隔离；所有查询都隐式限定当前用户。

## 1. 通用约定

- 时间：ISO-8601，带时区（例：`2026-03-05T09:30:00+08:00`）。服务端存 UTC，按用户时区返回。
- 分页：`?limit=20&cursor=<opaque>`，响应 `{"items": [...], "next_cursor": "..."}`。
- 错误：`{"detail": "machine_readable_code", "message": "人类可读说明", "extra": {...}}`。
- 身份标签统一为 `user`（使用者）/ `other`（其他人）/ `unknown`（未知）；展示层分别渲染 `[使用者]` / `[其他人]` / `[未知]`。
- 卡片类型 `type`：`conversation`（对话）、`meeting`（会议）、`media`（流媒体/视频声）、`other`。
- 所有软删除对象返回 `deleted_at`；默认列表不返回已删除项（`?include_deleted=true` 可含）。

## 2. 健康检查

### GET `/health`
无需认证。除 provider 模式外，还返回后台 worker 与音频处理队列状态（用于排查"有录音但没总结"）：
```json
{"status": "ok", "version": "1.0.0",
 "mode": {"asr": "faster_whisper", "diarization": "pyannote", "llm": "deepseek-chat"},
 "worker": {"enabled": true, "alive": true, "ticks": 120, "processed_total": 18,
            "last_tick_at": "2026-03-05T01:00:00Z", "last_tick_age_s": 0.4, "last_error": null},
 "audio": {"queued": 3, "processing": 1, "failed": 2, "done": 18},
 "time": "2026-03-05T01:00:00Z"}
```

### GET `/ready`
检查数据库与调度器；`{"status": "ready"|"degraded", "checks": {...}}`。

## 3. 账号与配置

### GET `/me`
```json
{
  "id": "usr_01H...",
  "display_name": "小明",
  "aliases": ["小明", "明明", "Xiao Ming"],
  "timezone": "Asia/Shanghai",
  "search_provider": "builtin",
  "voiceprint": {"enrolled": true, "updated_at": "...", "threshold": 60, "quality": 0.82},
  "profile": {"text": "…", "updated_at": "...", "version": 3},
  "created_at": "..."
}
```

### PATCH `/me`
请求（全部可选）：`{"display_name": "...", "aliases": ["..."], "timezone": "Asia/Shanghai", "search_provider": "builtin|deepseek"}`
- `aliases` 最多 20 个、每个 1–64 字符；用于 AI 提示词中识别对用户的称呼。
- 返回同 `GET /me`。

### GET `/me/settings`
```json
{"search_provider": "builtin", "push_enabled": true, "auto_schedule": true, "auto_profile_update": true, "correction_notice": true}
```

### PATCH `/me/settings`
请求：`{"search_provider": "builtin"|"deepseek", "push_enabled": true, "auto_schedule": true, "auto_profile_update": true, "correction_notice": true}` → 返回完整设置。

## 4. 声纹注册

### GET `/me/voiceprint`
```json
{"enrolled": false, "threshold": 60, "updated_at": null, "quality": null, "sample_seconds": null}
```

### POST `/me/voiceprint`
`multipart/form-data`：
- `file`: 音频文件（wav/mp3/m4a/flac，建议 10–30 秒干净人声）
- `threshold`: 可选，0–100，默认 60

响应：
```json
{"enrolled": true, "threshold": 60, "quality": 0.78, "duration_s": 14.2, "updated_at": "..."}
```
错误：`no_speech`（未检测到人声）、`too_short`（<3s）、`too_long`（>120s）、`unsupported_format`。

### DELETE `/me/voiceprint`
`{"deleted": true}`；之后所有说话人识别结果降级为 `unknown`。

### POST `/me/voiceprint/verify`
`multipart/form-data` 同上字段 `file` → `{"matched": true, "similarity": 73.4, "threshold": 60}`（用于 App 调试/自检）。

## 5. 用户画像

### GET `/me/profile`
```json
{"text": "30 岁软件工程师，常住上海；关注 AI 与开源；偏好简短直接的沟通；周三晚上有羽毛球活动。",
 "version": 3, "updated_at": "...", "source_card_count": 12}
```

### PUT `/me/profile`
请求 `{"text": "..."}`（≤ 600 字符，过长返回 `profile_too_long`）。用户修改后 `version+1`、`source: "user"`。

### POST `/me/profile/refresh`
根据最近的卡片重新总结画像（异步也可同步返回），响应同 `GET /me/profile`。

## 6. 音频上传（Agent → Server）

### POST `/audio/segments`
`multipart/form-data`：
- `file`: 音频片段（wav，单声道；支持 8/16/32/**48 kHz**，Server 内部统一重采样到 16 kHz 并做抗混叠滤波）
- `meta`: JSON 字符串
```json
{
  "client_segment_id": "seg-20260305T093000-0001",
  "device_id": "pi-livingroom",
  "recorded_at": "2026-03-05T09:30:00+08:00",
  "duration_s": 42.5,
  "sample_rate": 48000,
  "channels": 1,
  "sha256": "…",
  "vad": {"speech_ratio": 0.61, "segments": [[0.3, 8.2], [9.1, 40.0]]},
  "trigger": "auto|manual"
}
```
- 幂等：同一 `client_segment_id` 重复上传返回已存在记录（HTTP 200，`duplicate: true`），不重复处理。
- 响应 `202`：
```json
{"id": "seg_01H...", "client_segment_id": "...", "status": "queued", "duplicate": false,
 "duration_s": 42.5, "sample_rate": 48000, "received_at": "..."}
```

### GET `/audio/segments/{id}`
```json
{"id": "...", "status": "queued|processing|done|failed", "error": null, "card_id": "card_...",
 "duration_s": 42.5, "sample_rate": 48000, "channels": 1, "device_id": "pi-livingroom",
 "received_at": "...", "processed_at": "..."}
```

### GET `/audio/segments`
`?limit&cursor&status` → 分页列表（Agent 用于对账）。

### GET `/audio/{segment_id}/file`
返回原始音频流（`Content-Type: audio/wav`，`Content-Disposition: attachment`）。App 详情页下载原始录音使用。

### POST `/audio/segments/{id}/reprocess`
重新跑管道（用于换模型/修复失败），响应同 `GET /audio/segments/{id}`。

### POST `/audio/segments/requeue-failed`
把当前用户所有 `failed` 片段重置为 `queued`（模型/依赖修复后恢复历史失败数据）。
响应 `{"requeued": 58, "status": "queued"}`。

### POST `/audio/segments/requeue-stuck`
把卡在 `processing` 的片段重新入队（Server 重启/崩溃后遗留）。响应 `{"requeued": 4, "status": "queued"}`。

### GET `/audio/segments/pending/count`
`{"queued": 12, "pending_ids": ["seg_..."]}`。

## 7. 总结卡片

### GET `/cards`
查询参数：`from`、`to`（occurred_at 范围）、`q`（全文关键词）、`type`、`speaker`（`user`/`other`）、`limit`、`cursor`、`include_deleted`。
```json
{
  "items": [{
    "id": "card_01H...",
    "title": "和产品经理讨论 Q3 排期",
    "summary": "…",
    "key_points": ["…"],
    "people": ["张伟", "李娜"],
    "type": "conversation",
    "occurred_at": "2026-03-05T09:30:00+08:00",
    "duration_s": 42.5,
    "audio_url": "/api/v1/audio/seg_.../file",
    "audio_available": true,
    "labels": {"user_turns": 12, "other_turns": 9},
    "meeting_id": null,
    "has_correction": false,
    "deleted_at": null
  }],
  "next_cursor": null
}
```

### GET `/cards/{id}`
完整详情：
```json
{
  "id": "...", "title": "...", "summary": "...", "key_points": ["..."],
  "people": ["..."], "type": "conversation", "occurred_at": "...", "duration_s": 42.5,
  "audio_url": "/api/v1/audio/seg_.../file", "audio_available": true,
  "labels": {"user_turns": 12, "other_turns": 9},
  "transcript": [
    {"start": 0.4, "end": 6.2, "speaker": "user", "speaker_label": "[使用者]", "text": "帮我记一下明天下午三点和周磊开会。"},
    {"start": 6.5, "end": 9.8, "speaker": "other", "speaker_label": "[其他人]", "text": "好的，你们在哪个会议室？"}
  ],
  "schedule_items": [{"id": "evt_...", "title": "...", "start_at": "...", "end_at": "...", "location": "...", "source": "card"}],
  "todos": [{"id": "todo_...", "title": "...", "status": "pending", "due_at": "..."}],
  "commands": [{"id": "cmd_...", "text": "...", "status": "done", "result": "...", "created_at": "..."}],
  "meeting_id": null,
  "corrections": [{"id": "corr_...", "at": "...", "field": "summary", "before": "...", "after": "...", "note": "..."}],
  "deleted_at": null,
  "created_at": "...", "updated_at": "..."
}
```

### PATCH `/cards/{id}`（勘误）
请求（任选）：`{"title": "...", "summary": "...", "key_points": ["..."], "type": "media", "note": "其实是电视节目声音"}`
- 记录 revision（`corrections`），重算派生数据：日程/待办中 `source=card` 的条目按新内容更新；画像重算标记 dirty。
- 响应同 `GET /cards/{id}`。

### DELETE `/cards/{id}`（删除）
- 软删除卡片；级联软删除由该卡片派生的日程、待办、会议纪要、指令记录；画像重算标记 dirty；写审计日志。
- 响应：`{"deleted": true, "cascaded": {"schedule": 2, "todos": 1, "meetings": 1}, "profile_dirty": true}`

### POST `/cards/{id}/restore`
`{"restored": true}`。

### GET `/cards/{id}/transcript.txt`
`text/plain` 带身份标签的原文，便于导出。

## 8. 日程

### GET `/schedule`
`?from&to&include_deleted` → `{"items": [ScheduleItem]}`。
```json
{"id": "evt_01H...", "title": "和周磊开会", "start_at": "...", "end_at": "...", "location": "3 号会议室",
 "description": "...", "source": "card|command|manual", "card_id": "card_...", "status": "confirmed",
 "remind_at": "...", "reminded": false, "created_at": "..."}
```

### POST `/schedule`
`{"title": "...", "start_at": "...", "end_at": "...", "location": "...", "description": "...", "remind_minutes_before": 30}`

### PATCH `/schedule/{id}`、DELETE `/schedule/{id}`
PATCH 字段同 POST；DELETE 返回 `{"deleted": true}`。

## 9. 待办

### GET `/todos`
`?status=pending|done|all&limit&cursor`
```json
{"id": "todo_01H...", "title": "给张伟发 Q3 排期表", "status": "pending",
 "due_at": "...", "source": "card|command|manual", "card_id": "...", "priority": "normal",
 "created_at": "...", "completed_at": null}
```

### POST `/todos`、PATCH `/todos/{id}`、DELETE `/todos/{id}`
PATCH 可改 `title/status/due_at/priority`；`status=done` 时写入 `completed_at`。

## 10. 会议纪要

### GET `/meetings`
`?from&to&limit&cursor` →
```json
{"id": "mtg_01H...", "title": "Q3 排期评审", "started_at": "...", "ended_at": "...",
 "participants": [{"label": "[使用者]", "name": "小明"}, {"label": "[其他人]", "name": "张伟"}],
 "summary": "...", "action_items": [{"text": "...", "owner": "张伟", "due_at": "..."}],
 "card_ids": ["card_..."], "status": "ready|processing", "audio_url": "...", "created_at": "..."}
```

### GET `/meetings/{id}`
返回含 `minutes`（Markdown 文本）、`decisions`、`topics`、`card_ids` 的完整纪要。

### POST `/meetings/{id}/regenerate`
重新调用 AI 生成纪要，返回同 `GET /meetings/{id}`。

### PATCH `/meetings/{id}`、DELETE `/meetings/{id}`
PATCH：`title/summary/minutes/action_items`；DELETE 软删除（不删除来源卡片）。

## 11. 指令

### POST `/commands`
App 手动下发指令（等同于 `[使用者]` 说话触发）。请求：
```json
{"text": "帮我查一下明天上海的天气", "execute": true}
```
响应：
```json
{"id": "cmd_01H...", "text": "...", "intent": "search|schedule_query|memory_query|todo|none",
 "status": "done|failed|ignored", "result": "…", "pushed": true, "card_id": null, "created_at": "..."}
```
- `execute=false` 时仅解析意图不执行。

### GET `/commands`
`?limit&cursor&status` → 历史指令列表。

### POST `/commands/{id}/retry`
重新执行（例如钉钉推送失败）。

## 12. 搜索

### POST `/search`
```json
{"query": "上海明天天气", "provider": "builtin|deepseek", "limit": 5}
```
响应：
```json
{"query": "...", "provider": "builtin", "answer": "…AI 归纳…",
 "results": [{"title": "...", "url": "https://...", "snippet": "..."}], "took_ms": 812}
```
`provider` 省略时使用用户设置。

## 13. 定时任务

### GET `/tasks/scheduled`
```json
{"id": "job_01H...", "name": "晚间日程提醒", "cron": "0 21 * * *", "timezone": "Asia/Shanghai",
 "prompt": "汇总今天的日程和待办，用钉钉提醒我", "action": "agent|push|command",
 "enabled": true, "next_run_at": "...", "last_run_at": "...", "last_result": "..."}
```

### POST `/tasks/scheduled`
`{"name": "...", "cron": "0 21 * * *", "timezone": "Asia/Shanghai", "prompt": "...", "action": "agent", "enabled": true}`
（`cron` 或 `run_at` 二选一；`run_at` 为一次性任务）

### PATCH `/tasks/scheduled/{id}`、DELETE `/tasks/scheduled/{id}`

### POST `/tasks/scheduled/{id}/run`
立即执行一次，返回执行结果。

## 14. 推送与设备

### POST `/push/test`
`{"text": "测试消息"}` → `{"sent": true, "provider": "dingtalk"}`（未配置 Webhook 时 `sent:false, reason:"not_configured"`）。

### GET `/devices`
Agent/App 注册的设备列表：`{"items":[{"id":"pi-livingroom","name":"客厅树莓派","last_seen":"...","config":{...},"status":"online"}]}`

### PUT `/devices/{device_id}/config`
App 通过蓝牙写入的配置回传 Server 备份：
```json
{"wifi": {"ssid": "home", "password": "******"}, "retry_max": 5,
 "offline_buffer": true, "upload_endpoint": "https://server/api/v1", "vad": {"aggressiveness": 2, "min_speech_ms": 300}}
```
（也接受 `{"name": "客厅树莓派", "config": {…}}` 的包装形式。）
响应 `{"saved": true, "updated_at": "...", "device": {...}}`（密码字段服务端加密存储，读取时打码）。

## 15. 管理/审计

### GET `/audit`
`?limit&cursor` → 勘误/删除/声纹/配置变更记录。

### 卡片的异步处理状态
上传后管道异步执行；Server 提供 `GET /audio/segments/{id}` 轮询。处理完成后生成卡片，
并通过 `GET /events`（Server-Sent Events，可选实现）推送 `{"type":"card.created","card_id":"..."}`。
SSE 为增强能力，App 不可依赖它作为唯一数据源。
