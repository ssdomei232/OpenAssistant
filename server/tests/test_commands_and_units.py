from __future__ import annotations

from app.providers.push import DingTalkPush
from app.providers.llm import extract_json
from app.providers.search import StaticSearchProvider
from app.services.timeparse import parse_deadline, parse_time_mentions, to_local, to_utc
from app.services.align import Turn, align
from app.providers.types import ASRWord
from datetime import datetime

from tests.conftest import AUTH


def test_command_parse_only(client):
    response = client.post("/api/v1/commands", headers=AUTH, json={"text": "帮我查一下明天上海的天气", "execute": False})
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "web_search"
    assert body["status"] == "parsed"
    assert body["result"] == ""


def test_command_execute_search(client):
    response = client.post("/api/v1/commands", headers=AUTH, json={"text": "帮我查一下明天上海的天气"})
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "web_search"
    assert body["status"] == "done"
    assert "示例" in body["result"]
    assert body["pushed"] is True

    history = client.get("/api/v1/commands", headers=AUTH).json()["items"]
    assert len(history) == 1


def test_command_add_schedule_and_todo(client):
    scheduled = client.post("/api/v1/commands", headers=AUTH, json={"text": "帮我安排明天下午三点和周磊开会"})
    assert scheduled.json()["intent"] == "schedule_add"
    assert scheduled.json()["status"] == "done"
    items = client.get("/api/v1/schedule", headers=AUTH).json()["items"]
    assert len(items) == 1
    assert items[0]["source"] == "command"

    todo = client.post("/api/v1/commands", headers=AUTH, json={"text": "提醒我明天中午前把报告发给张伟"})
    assert todo.json()["intent"] == "todo_add"
    assert todo.json()["status"] == "done"
    todos = client.get("/api/v1/todos?status=all", headers=AUTH).json()["items"]
    assert len(todos) == 1
    assert todos[0]["due_at"] is not None


def test_query_commands(client):
    client.post("/api/v1/commands", headers=AUTH, json={"text": "帮我安排明天下午三点开会"})
    schedule = client.post("/api/v1/commands", headers=AUTH, json={"text": "看看我接下来的日程"})
    assert schedule.json()["intent"] == "schedule_query"
    assert "日程" in schedule.json()["result"]

    data = client.post("/api/v1/commands", headers=AUTH, json={"text": "统计一下我有多少条记录"})
    assert data.json()["intent"] == "data_query"
    assert "卡片" in data.json()["result"] or "记录" in data.json()["result"]


def test_search_endpoint_and_provider_selection(client):
    response = client.post("/api/v1/search", headers=AUTH, json={"query": "上海天气", "limit": 3})
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "static"
    assert body["results"]

    client.patch("/api/v1/me/settings", headers=AUTH, json={"search_provider": "deepseek"})
    fallback = client.post("/api/v1/search", headers=AUTH, json={"query": "上海天气"})
    assert fallback.status_code == 200
    # DeepSeek is not configured in tests → the builtin/static provider is used
    assert fallback.json()["provider"] == "static"


def test_dingtalk_signature_and_payload():
    push = DingTalkPush("https://oapi.dingtalk.com/robot/send?access_token=abc", secret="SECxyz")
    url = push._signed_url()
    assert "timestamp=" in url
    assert "sign=" in url
    assert url.startswith("https://oapi.dingtalk.com/robot/send?access_token=abc")

    unconfigured = DingTalkPush("")
    assert unconfigured.is_configured() is False
    ok, error = unconfigured.send("t", "m")
    assert ok is False
    assert error == "not_configured"


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('好的，结果如下：{"a": {"b": 3}} 谢谢') == {"a": {"b": 3}}
    assert extract_json("no json here") is None


def test_static_search_provider():
    response = StaticSearchProvider(answer="离线答案").search("q", limit=1)
    assert response.answer == "离线答案"
    assert len(response.results) == 1


def test_timeparse_chinese_expressions():
    base = datetime(2026, 3, 5, 10, 0)  # Thursday
    cases = {
        "明天下午三点开会": datetime(2026, 3, 6, 15, 0),
        "下周一上午十点评审": datetime(2026, 3, 9, 10, 0),
        "3月8日晚上7点吃饭": datetime(2026, 3, 8, 19, 0),
        "今天下午两点半": datetime(2026, 3, 5, 14, 30),
        "15:30 开短会": datetime(2026, 3, 5, 15, 30),
    }
    for text, expected in cases.items():
        mentions = parse_time_mentions(text, base)
        assert mentions, text
        assert mentions[0].start == expected, (text, mentions[0].start)

    range_mentions = parse_time_mentions("后天下午两点到四点评审", base)
    assert range_mentions[0].start == datetime(2026, 3, 7, 14, 0)
    assert range_mentions[0].end == datetime(2026, 3, 7, 16, 0)

    deadline = parse_deadline("周五之前把预算表交给财务", base)
    assert deadline == datetime(2026, 3, 6, 18, 0)
    deadline2 = parse_deadline("明天中午前给我", base)
    assert deadline2 == datetime(2026, 3, 6, 12, 0)
    assert parse_deadline("今天天气不错", base) is None


def test_timezone_conversion():
    local = to_local(datetime(2026, 3, 6, 7, 0), "Asia/Shanghai")
    assert local.hour == 15
    back = to_utc(local, "Asia/Shanghai")
    assert back == datetime(2026, 3, 6, 7, 0)


def test_align_labels_turns():
    identities = [
        type("Identity", (), {"start": 0.0, "end": 3.0, "label": "user", "anonymous": "SPEAKER_00"})(),
        type("Identity", (), {"start": 3.0, "end": 6.0, "label": "other", "anonymous": "SPEAKER_01"})(),
    ]
    words = [
        ASRWord(0.2, 0.6, "帮"),
        ASRWord(0.6, 1.0, "我"),
        ASRWord(1.0, 1.6, "记"),
        ASRWord(3.2, 3.8, "好"),
        ASRWord(3.8, 4.4, "的"),
    ]
    turns = align(words, identities)
    assert [t.speaker for t in turns] == ["user", "other"]
    assert turns[0].text == "帮我记"
    assert turns[0].speaker_label == "[使用者]"
    assert turns[1].text == "好的"


def test_align_no_identities_defaults_to_other():
    turns = align([ASRWord(0.0, 1.0, "你好")], [])
    assert len(turns) == 1
    assert turns[0].speaker == "other"
