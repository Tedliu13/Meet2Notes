"""Provider-specific cache hints on stable prefixes, never a cache of answers."""
from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any


def cache_arguments(litellm: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    request = deepcopy(arguments)
    messages = request.get("messages") or []
    if not messages or messages[0].get("role") != "system":
        return request
    text = messages[0].get("content")
    if not isinstance(text, str) or not text:
        return request
    model = str(request.get("model", ""))
    # Custom gateways may implement only the basic chat schema.
    if request.get("api_base"):
        return request
    if model.startswith("anthropic/"):
        messages[0]["content"] = [{
            "type": "text", "text": text, "cache_control": {"type": "ephemeral"},
        }]
    elif model.startswith("openai/"):
        request["prompt_cache_key"] = "meet2notes-" + hashlib.sha256(text.encode()).hexdigest()[:32]
        try:
            info = litellm.get_model_info(model=model)
        except Exception:
            info = {}
        if info.get("supports_prompt_cache_breakpoint"):
            messages[0]["content"] = [{
                "type": "text", "text": text,
                "prompt_cache_breakpoint": {"mode": "explicit"},
            }]
            request["prompt_cache_options"] = {"mode": "implicit", "ttl": "30m"}
    # Gemini uses implicit caching: avoid creating billed persistent cache objects.
    return request
