from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .contracts import SYSTEM, Call, Model, positive

MAX_RESPONSE_BYTES = 2_000_000


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class Response:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    truncated: bool = False
    reported_cost_usd: float | None = None
    billing: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.reported_cost_usd is not None:
            positive(self.reported_cost_usd, "reported_cost_usd", zero=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _token_count(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


class HTTPProvider:
    """Non-streaming adapters; no automatic retries or credential-bearing redirects."""

    def complete(self, model: Model, call: Call, timeout: float) -> Response:
        if model.provider == "copilot":
            from .copilot_provider import CopilotProvider

            return CopilotProvider().complete(model, call, timeout)
        key = os.environ.get(model.api_key_env, "") if model.api_key_env else ""
        if model.api_key_env and not key:
            raise ProviderError(
                f"Missing credential environment variable {model.api_key_env}"
            )
        headers = {"Content-Type": "application/json"}
        if model.provider == "anthropic":
            endpoint = model.base_url.rstrip("/") + "/messages"
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
            payload = {
                "model": model.model_id,
                "max_tokens": call.max_output_tokens,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": call.prompt}],
            }
        else:
            endpoint = model.base_url.rstrip("/") + "/chat/completions"
            if key:
                headers["Authorization"] = f"Bearer {key}"
            payload = {
                "model": model.model_id,
                model.output_limit_field: call.max_output_tokens,
                "messages": [
                    {"role": model.system_role, "content": SYSTEM},
                    {"role": "user", "content": call.prompt},
                ],
            }
        request = urllib.request.Request(
            endpoint, json.dumps(payload).encode("utf-8"), headers
        )
        try:
            opener = urllib.request.build_opener(NoRedirect())
            with opener.open(request, timeout=max(0.001, timeout)) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise ProviderError("Provider response exceeded the size limit")
            return self.decode(model.provider, json.loads(data))
        except urllib.error.HTTPError as error:
            error.close()
            raise ProviderError(
                f"Provider HTTP {error.code}; not retried. "
                "Billing may still have occurred."
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ProviderError(
                "Provider connection failed or timed out; not retried. "
                "Billing may still have occurred."
            ) from None
        except (ValueError, KeyError, TypeError, IndexError):
            raise ProviderError("Provider returned an invalid response") from None

    @staticmethod
    def decode(provider: str, data: dict) -> Response:
        if not isinstance(data, dict):
            raise ValueError("Expected an object response")
        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            raise ValueError("Expected a usage object")
        if provider == "anthropic":
            text = "\n".join(
                block["text"]
                for block in data["content"]
                if block.get("type") == "text"
            )
            parts = [
                _token_count(usage.get("input_tokens")),
                _token_count(usage.get("cache_creation_input_tokens", 0)),
                _token_count(usage.get("cache_read_input_tokens", 0)),
            ]
            return Response(
                text,
                sum(parts) if all(p is not None for p in parts) else None,
                _token_count(usage.get("output_tokens")),
                data.get("stop_reason") == "max_tokens",
            )
        choice = data["choices"][0]
        text = choice["message"]["content"]
        if not isinstance(text, str):
            raise ValueError("Expected text content")
        return Response(
            text,
            _token_count(usage.get("prompt_tokens")),
            _token_count(usage.get("completion_tokens")),
            choice.get("finish_reason") == "length",
        )
