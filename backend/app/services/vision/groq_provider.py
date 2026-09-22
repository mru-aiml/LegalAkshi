"""Groq vision provider (OpenAI-compatible, server-side only).

Reuses the OpenRouter provider architecture wholesale (subclass):
same chat/completions transport, same multimodal image parts, same
extraction prompt, same structured-JSON parsing, same schema
normalizer — so the frontend receives exactly the schema it already
expects. Only the endpoint defaults differ:

- base URL: https://api.groq.com/openai/v1
- model: qwen/qwen3.8-27b (Groq-hosted; no :free suffix exists there)

The key comes from GROQ_API_KEY and is never logged, stored, or
exposed. Nothing here judges compliance; extraction only.
"""
from __future__ import annotations

from typing import Any

from app.services.vision.openrouter_provider import OpenRouterVisionProvider

DEFAULT_MODEL = "qwen/qwen3.8-27b"
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"


class GroqVisionProvider(OpenRouterVisionProvider):
    name = "groq"

    def __init__(self, api_key: str = "", model: str = "",
                 base_url: str = "") -> None:
        self._api_key = api_key or ""
        self._model = model or DEFAULT_MODEL
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")

    def available(self) -> bool:
        return bool(self._api_key and self._model)

    def _headers(self) -> dict[str, str]:
        """Request headers: neutral client UA (the stock
        ``Python-urllib`` UA is rejected at the edge), key travels
        here and is never logged."""
        return {"Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
                "User-Agent": "LegalAkshi-inspection/1.0"}

    def health_check(self, timeout_s: float = 10.0) -> dict[str, Any]:
        """ONE minimal text-only liveness probe (no package images).

        Short timeout; any transport, auth, or shape failure returns
        ok=False with a sanitised reason (never key material).
        """
        import time as _time

        if not self.available():
            return {"ok": False,
                    "reason": "groq provider not configured (key/model)"}
        import json as _json
        import urllib.request as _urlreq

        body = {"model": self._model,
                "messages": [{"role": "user",
                              "content": "Reply with exactly: OK"}],
                "max_tokens": 8}
        req = _urlreq.Request(
            self._base_url + "/chat/completions",
            data=_json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self._api_key}"},
            method="POST")
        t0 = _time.perf_counter()
        try:
            with _urlreq.urlopen(req, timeout=timeout_s) as resp:
                payload = _json.loads(resp.read().decode())
        except Exception as exc:
            from app.services.vision.openrouter_provider import (
                _sanitize_error,
            )

            return {"ok": False, "reason": _sanitize_error(exc)}
        _ = round((_time.perf_counter() - t0) * 1000, 1)
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except Exception:
            return {"ok": False,
                    "reason": "malformed health response (no text)"}
        if "OK" in str(text).upper():
            return {"ok": True, "reason": "model replied OK"}
        return {"ok": False,
                "reason": "unexpected health response shape"}
