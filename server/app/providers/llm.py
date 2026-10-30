"""LLM providers: OpenAI-compatible chat completions (DeepSeek etc.)."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx


class OpenAICompatibleLLM:
    """Any OpenAI-compatible ``/chat/completions`` endpoint (DeepSeek by default)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 60.0,
        max_tokens: int = 1600,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens
        self.name = f"llm:{model}"

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.base_url)

    def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> str:
        if not self.available:
            raise RuntimeError("llm_not_configured")
        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens or self.max_tokens,
            "stream": False,
        }
        with httpx.Client(timeout=self.timeout_s) as client:
            response = client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:  # pragma: no cover - defensive
            raise RuntimeError(f"unexpected_llm_response: {exc}") from exc

    def complete_json(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> dict[str, Any] | None:
        raw = self.complete(system, user, temperature=temperature, max_tokens=max_tokens)
        return extract_json(raw)


class RuleBasedLLM:
    """Placeholder LLM used when no API key is configured.

    ``available`` is False, so higher layers take their deterministic rule
    based path instead of calling ``complete``.  The class exists so the
    provider registry always has an object with a uniform interface.
    """

    name = "rule"
    available = False

    def complete(self, system: str, user: str, temperature: float = 0.3, max_tokens: int | None = None) -> str:
        return ""


def extract_json(text: str) -> dict[str, Any] | None:
    """Best-effort JSON object extraction from an LLM reply."""
    if not text:
        return None
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    try:
        value = json.loads(candidate)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    # fall back to the first balanced {...} block
    start = candidate.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(candidate)):
        ch = candidate[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(candidate[start : idx + 1])
                    return value if isinstance(value, dict) else None
                except json.JSONDecodeError:
                    return None
    return None
