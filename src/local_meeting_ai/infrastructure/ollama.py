"""Read-only Ollama discovery. Never starts a service or downloads/loads models."""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from local_meeting_ai.domain.errors import ValidationError


def executable_path() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    candidates: list[Path] = []
    if platform.system() == "Windows":
        root = os.environ.get("LOCALAPPDATA")
        if root:
            candidates.append(Path(root) / "Programs/Ollama/ollama.exe")
    elif platform.system() == "Darwin":
        candidates.extend(
            [
                Path("/Applications/Ollama.app/Contents/Resources/ollama"),
                Path.home() / "Applications/Ollama.app/Contents/Resources/ollama",
                Path("/opt/homebrew/bin/ollama"),
            ]
        )
    candidates.extend([Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")])
    return next((str(path) for path in candidates if path.is_file()), None)


def normalize_url(value: str | None) -> str:
    candidate = value or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
    if not value and "://" not in candidate:
        candidate = "http://" + candidate
    try:
        url = urlsplit(candidate.strip())
        if url.scheme not in {"http", "https"} or not url.hostname:
            raise ValueError
        if url.username or url.password or url.query or url.fragment:
            raise ValueError
        host = url.hostname
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1" if host == "0.0.0.0" else "::1"
        authority = f"[{host}]" if ":" in host else host
        if url.port:
            authority += f":{url.port}"
        return urlunsplit((url.scheme, authority, url.path.rstrip("/"), "", ""))
    except ValueError as exc:
        raise ValidationError(
            "Enter an HTTP(S) Ollama URL without credentials, query or fragment."
        ) from exc


async def discover_ollama(base_url: str | None = None) -> dict[str, Any]:
    base = normalize_url(base_url)
    executable = executable_path()
    local = urlsplit(base).hostname in {"localhost", "127.0.0.1", "::1"}
    result: dict[str, Any] = {
        "base_url": base,
        "executable_detected": bool(executable),
        "local_endpoint": local,
        "models": [],
        "excluded_models": 0,
        "unverified_models": 0,
    }
    # Requests originate in the backend, avoiding browser CORS configuration.
    # No redirects, credentials, environment proxies or LAN/port scanning.
    async with httpx.AsyncClient(timeout=3, trust_env=False, follow_redirects=False) as client:
        try:
            version = await client.get(base + "/api/version")
            version.raise_for_status()
            payload = version.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("version"), str):
                raise ValueError("Unexpected Ollama version response")
            result["version"] = payload["version"]
            response = await client.get(base + "/api/tags")
            response.raise_for_status()
            models = response.json()["models"]
            if not isinstance(models, list):
                raise ValueError("Unexpected Ollama model list")
        except httpx.ConnectError:
            result["state"] = "stopped" if executable and local else "unreachable"
            return result
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            result["state"] = "connection_error"
            return result
        semaphore = asyncio.Semaphore(6)

        async def inspect(item: Any) -> None:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                result["unverified_models"] += 1
                return
            name = item["name"]
            async with semaphore:
                try:
                    response = await client.post(base + "/api/show", json={"model": name})
                    response.raise_for_status()
                    detail = response.json()
                    capabilities = detail.get("capabilities")
                    if not isinstance(capabilities, list):
                        result["unverified_models"] += 1
                        return
                    if "completion" not in capabilities:
                        result["excluded_models"] += 1
                        return
                    info = detail.get("model_info") or {}
                    contexts = [
                        v
                        for k, v in info.items()
                        if k.endswith(".context_length") and isinstance(v, int) and v > 0
                    ]
                    cloud = bool(
                        detail.get("remote_host")
                        or detail.get("remote_model")
                        or name.endswith((":cloud", "-cloud"))
                    )
                    result["models"].append(
                        {
                            "name": name,
                            "size": item.get("size", 0),
                            "parameter_size": (item.get("details") or {}).get("parameter_size", ""),
                            "context_length": min(contexts) if contexts else None,
                            "cloud": cloud,
                        }
                    )
                except (httpx.HTTPError, ValueError, AttributeError, TypeError):
                    result["unverified_models"] += 1

        # Bound discovery even on a server with many models or a slow connection.
        tasks = [asyncio.create_task(inspect(item)) for item in models[:100]]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=8)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            result["unverified_models"] += len(pending)
        result["unverified_models"] += max(0, len(models) - 100)
        result["models"].sort(key=lambda item: item["name"].casefold())
        result["state"] = "ready" if result["models"] else "empty"
        return result
