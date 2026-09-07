"""
LLM client abstraction — multi-tier provider client with automatic cascading fallback.

Supports:
  1. Extractor LLM (Local model -> Groq -> OpenRouter)
  2. Reasoner LLM (Groq -> OpenRouter -> Local model)

Any role can seamlessly cascade to the next tier on HTTP 429 (rate-limit),
connection failure, or API errors.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

_TIMEOUT = 180.0  # seconds

# Models that support Qwen3-style /no_think token to suppress reasoning
_QWEN3_MODELS = {"qwen/qwen3.8-27b", "qwen/qwen3.6-27b"}


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    model: str
    max_tokens: int = 900


class LLMClient:
    """Multi-provider client with cascading fallback across tiers."""

    def __init__(
        self,
        providers: list[ProviderConfig] | None = None,
        prefix: str = "LLM",
    ) -> None:
        if providers:
            self.providers = [p for p in providers if p.base_url]
        else:
            self.providers = self._build_providers_from_env(prefix)

        self.last_model_used: str = (
            self.providers[0].model if self.providers else "unknown"
        )

    @property
    def model(self) -> str:
        return self.last_model_used

    @property
    def base_url(self) -> str:
        return self.providers[0].base_url if self.providers else ""

    def _build_providers_from_env(self, prefix: str) -> list[ProviderConfig]:
        """Build ordered list of provider configs from environment variables."""
        configs: list[ProviderConfig] = []

        # 1. Primary provider from prefix
        primary_url = os.environ.get(f"{prefix}_BASE_URL") or os.environ.get("LLM_BASE_URL")
        primary_key = (
            os.environ.get(f"{prefix}_API_KEY")
            or os.environ.get("LLM_API_KEY")
            or os.environ.get("GROQ_API_KEY", "")
        )
        primary_model = os.environ.get(f"{prefix}_MODEL") or os.environ.get("LLM_MODEL", "qwen/qwen3.8-27b")
        if primary_url:
            configs.append(
                ProviderConfig(
                    name=f"{prefix}-primary",
                    base_url=primary_url.rstrip("/"),
                    api_key=primary_key,
                    model=primary_model,
                )
            )

        # 2. Secondary fallback (e.g. OpenRouter or Groq)
        fallback_url = os.environ.get(f"FALLBACK_{prefix}_BASE_URL") or os.environ.get("FALLBACK_BASE_URL")
        fallback_key = (
            os.environ.get(f"FALLBACK_{prefix}_API_KEY")
            or os.environ.get("FALLBACK_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY", "")
        )
        fallback_model = (
            os.environ.get(f"FALLBACK_{prefix}_MODEL")
            or os.environ.get("FALLBACK_MODEL", "minimax/minimax-m3:free")
        )
        if fallback_url and (not configs or fallback_url.rstrip("/") != configs[0].base_url):
            configs.append(
                ProviderConfig(
                    name=f"{prefix}-fallback",
                    base_url=fallback_url.rstrip("/"),
                    api_key=fallback_key,
                    model=fallback_model,
                    max_tokens=1500,
                )
            )

        # 3. Local fallback (if local server is configured and not already added)
        local_url = os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:8080/v1").rstrip("/")
        local_model = os.environ.get("LOCAL_LLM_MODEL", "local-model")
        already_has_local = any(c.base_url == local_url for c in configs)
        if not already_has_local and local_url:
            configs.append(
                ProviderConfig(
                    name=f"{prefix}-local-backup",
                    base_url=local_url,
                    api_key="none",
                    model=local_model,
                )
            )

        return configs

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int | None = None,
    ) -> str:
        """
        Send chat completion request trying each provider in cascading order.
        """
        if not self.providers:
            raise RuntimeError("No LLM providers configured.")

        last_error = None
        for provider in self.providers:
            effective_tokens = max_tokens or provider.max_tokens
            # Cap tokens safely for 4K context windows & Groq OTPM limits
            if "api.groq.com" in provider.base_url:
                effective_tokens = min(effective_tokens, 900)
            elif "127.0.0.1" in provider.base_url or "localhost" in provider.base_url:
                effective_tokens = min(effective_tokens, 3500)
            try:
                content = self._call_provider(
                    base_url=provider.base_url,
                    api_key=provider.api_key,
                    model=provider.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=effective_tokens,
                )
                self.last_model_used = provider.model
                return content
            except Exception as exc:
                log.warning(
                    "Provider '%s' (%s at %s) failed: %s. Trying next provider in cascade...",
                    provider.name,
                    provider.model,
                    provider.base_url,
                    exc,
                )
                last_error = exc

        raise RuntimeError(f"All configured LLM providers failed. Last error: {last_error}")

    def _call_provider(
        self,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        patched = self._maybe_inject_no_think(messages, model)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": patched,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        with httpx.Client(timeout=_TIMEOUT) as client:
            response = client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
            )

        if response.status_code != 200:
            raise ValueError(
                f"API status {response.status_code}: {response.text[:300]}"
            )

        text = response.text.strip()
        data = json.loads(text)

        if "error" in data:
            raise ValueError(f"API returned error: {data['error']}")

        choices = data.get("choices")
        if not choices:
            raise ValueError(f"No choices returned: {data}")

        msg = choices[0].get("message", {})
        content = msg.get("content") or ""
        if not content.strip():
            content = msg.get("reasoning_content") or msg.get("reasoning") or ""

        # Strip think tags if model emitted them inside content
        if "<think>" in content and "</think>" in content:
            content = content.split("</think>", 1)[-1].strip()

        return content

    def _maybe_inject_no_think(
        self, messages: list[dict[str, str]], model: str
    ) -> list[dict[str, str]]:
        """Append /no_think to the last user turn for Qwen3 models on Groq."""
        if model not in _QWEN3_MODELS:
            return messages
        patched = list(messages)
        for i in range(len(patched) - 1, -1, -1):
            if patched[i]["role"] == "user":
                if "/no_think" not in patched[i]["content"]:
                    patched[i] = {
                        **patched[i],
                        "content": patched[i]["content"] + "\n/no_think",
                    }
                break
        return patched


def get_extractor_client() -> LLMClient:
    """Instantiate LLM client for extraction (Local -> Groq -> OpenRouter)."""
    return LLMClient(prefix="EXTRACTOR_LLM")


def get_reasoner_client() -> LLMClient:
    """Instantiate LLM client for comparison & reasoning (Groq -> OpenRouter -> Local)."""
    return LLMClient(prefix="REASONER_LLM")
