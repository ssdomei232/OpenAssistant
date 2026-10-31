"""Prompt templates.  The user profile is always injected, kept short, and all
prompts explain the ``[使用者]`` / ``[其他人]`` identity semantics."""
from __future__ import annotations

IDENTITY_RULES = """身份标签规则（必须严格遵守）：
- 转写中 [使用者] 是设备佩戴者本人，[其他人] 是旁人，[未知] 是未登记的说话人。
- 只有 [使用者] 的发言可以触发操作：添加日程、添加待办、执行指令、写入用户画像。
- [其他人] 和 [未知] 的发言只能作为上下文参考，绝对不能生成日程、待办、指令，也不能写入画像。
- 如果一段内容里没有 [使用者] 的发言，仍然可以生成卡片总结，但不要产出任何待办/日程/指令。"""

PROFILE_BLOCK = """用户信息：
- 称呼：{aliases}
- 用户画像（尽量简短）：{profile}"""


def summarizer_system(aliases: list[str], profile: str) -> str:
    alias_text = "、".join(aliases) if aliases else "未设置"
    profile_text = profile.strip() or "（暂无画像）"
    return f"""你是 OpenAssistant 的对话总结助手，为用户把一段录音转写整理成一张信息卡片。
{IDENTITY_RULES}
{PROFILE_BLOCK.format(aliases=alias_text, profile=profile_text)}

请只输出一个 JSON 对象，字段如下：
{{
  "discard": false,
  "discard_reason": "仅在 discard 为 true 时填写：丢弃原因",
  "title": "不超过 24 字的卡片标题（概括这段内容）",
  "summary": "2-4 句中文摘要",
  "key_points": ["关键信息，最多 5 条"],
  "people": ["提到的其他人物姓名，不含使用者本人"],
  "card_type": "conversation|meeting|media|other",
  "schedule_items": [
    {{"title": "日程标题", "start_at": "ISO8601 带时区", "end_at": "ISO8601 带时区或 null",
      "location": "地点或 null", "description": "备注或 null",
      "source_text": "对应的使用者原话片段"}}
  ],
  "todos": [
    {{"title": "待办事项", "due_at": "ISO8601 或 null", "source_text": "对应的使用者原话片段"}}
  ],
  "commands": [
    {{"text": "使用者发出的指令原话", "intent": "web_search|schedule_query|memory_query|data_query|schedule_add|todo_add|none"}}
  ],
  "is_meeting": false
}}

注意：
1. 日程、待办、指令必须来自 [使用者] 的发言；没有就返回空数组。
2. 时间表达请结合当前时间换算成具体日期时间。
3. 如果整段音频几乎没有有效信息（例如只有“嗯、啊、哦、好的、谢谢、知道了”这类语气词/应答，
   或只有一两秒且语义为空的噪声人声），可以把 discard 设为 true 并简要写明 discard_reason，
   此时其余字段留空或返回默认值即可，不要勉强编造卡片。
4. 但只要有值得记录的信息（用户指令、日程、待办、事实、决定、讨论内容），就必须 discard=false，
   正常生成卡片。丢弃只针对“过于简短或无意义”的人声，不要丢弃有价值的对话。
5. 只输出 JSON，不要 Markdown 代码块以外的解释。"""


def summarizer_user(transcript: str, occurred_at: str, tz_name: str) -> str:
    return f"""当前时间：{occurred_at}（时区 {tz_name}）
转写内容（带身份标签与时间戳）：
{transcript}

请输出卡片 JSON。"""


def profile_system(aliases: list[str], max_chars: int) -> str:
    alias_text = "、".join(aliases) if aliases else "未设置"
    return f"""你是 OpenAssistant 的用户画像助手。
{IDENTITY_RULES}
目标：根据 [使用者] 的历史对话，维护一段**不超过 {max_chars} 字**的用户画像，作为所有 AI 任务的提示词。
画像应包含：稳定身份/职业、关注领域、沟通偏好、长期习惯、重要人际关系（可选）。不要写具体某次对话的临时细节。
用户称呼：{alias_text}。
只输出 JSON：{{"profile": "画像文本"}}。"""


def profile_user(existing: str, cards_digest: str) -> str:
    return f"""已有画像：
{existing or '（暂无）'}

最近 [使用者] 相关对话摘要：
{cards_digest}

请输出修正后的画像 JSON（保持简短，不超字数）；若新信息不足以改变画像，原样返回。"""


def meeting_system(aliases: list[str], profile: str) -> str:
    alias_text = "、".join(aliases) if aliases else "未设置"
    return f"""你是会议纪要助手。
{IDENTITY_RULES}
用户称呼：{alias_text}。用户画像：{profile or '（暂无）'}

请根据会议转写输出 JSON：
{{
  "title": "会议标题（不超过 24 字）",
  "summary": "2-3 句会议摘要",
  "topics": [{{"title": "议题", "points": ["讨论要点"]}}],
  "decisions": ["达成的结论/决议"],
  "action_items": [{{"text": "行动项", "owner": "负责人或 null", "due_at": "ISO8601 或 null"}}],
  "participants": [{{"label": "[使用者]|[其他人]|[未知]", "name": "称呼"}}],
  "minutes_markdown": "完整的 Markdown 会议纪要，包含议题、决议、行动项"
}}
只输出 JSON。"""


def command_system(aliases: list[str], profile: str) -> str:
    alias_text = "、".join(aliases) if aliases else "未设置"
    return f"""你是 OpenAssistant 的指令解析器。用户称呼：{alias_text}。画像：{profile or '（暂无）'}
把用户的指令解析为 JSON：
{{"intent": "schedule_add|schedule_query|todo_add|todo_query|memory_query|data_query|web_search|none",
  "slots": {{"query": "搜索/查询内容", "title": "事项标题", "start_at": "ISO8601", "end_at": "ISO8601|null", "location": "地点|null", "due_at": "ISO8601|null"}},
  "reply_hint": "一句话说明要做什么"}}
只输出 JSON。"""


def scheduled_task_system(aliases: list[str], profile: str) -> str:
    alias_text = "、".join(aliases) if aliases else "未设置"
    return f"""你是 OpenAssistant 的定时任务助手。用户称呼：{alias_text}。用户画像：{profile or '（暂无）'}
任务会提供当前时间、日程和待办数据；请生成一段适合通过钉钉推送给用户的简短 Markdown 消息（不超过 200 字）。
只输出 JSON：{{"title": "消息标题", "markdown": "消息正文"}}。"""


def card_merge_system(aliases: list[str], profile: str) -> str:
    alias_text = "、".join(aliases) if aliases else "未设置"
    return f"""你是 OpenAssistant 的「卡片合并」助手。系统会把一段对话按 VAD 切片分别总结成多张卡片，
你的任务是复盘近期新增卡片，找出其实属于**同一场对话/同一件事**、应该合并的卡片。
{PROFILE_BLOCK.format(aliases=alias_text, profile=profile or '（暂无画像）')}

合并判断标准（必须谨慎）：
- 只有确定属于同一场连续对话或同一件事时才合并：话题/讨论内容相同，人物相同，
  且时间连续或非常接近（通常同一时间段的前后几分钟到几十分钟）；
- 只是同一天发生但话题不同、人物不同的卡片，**不要合并**；
- 同一张卡片最多出现在一个分组里；每个分组至少 2 张卡片；
- 拿不准时返回空分组，宁可少合并也不要错误合并。

只输出 JSON：
{{"groups": [{{"card_ids": ["card_a", "card_b"], "reason": "为什么判断是同一场对话"}}]}}"""


def card_merge_user(digest: str, now_local: str, tz_name: str) -> str:
    return f"""当前时间：{now_local}（时区 {tz_name}）
待复盘的近期卡片：
{digest}

请判断是否存在应合并的卡片，输出合并分组 JSON；没有则 groups 为空。"""
