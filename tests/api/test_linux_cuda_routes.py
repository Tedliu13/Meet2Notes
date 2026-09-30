from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from local_meeting_ai.domain.errors import CapabilityUnavailableError
from local_meeting_ai.infrastructure import linux_cuda


def test_linux_cuda_repair_api_reports_success_and_failure(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    async def repair() -> dict[str, Any]:
        return {"state": "restart_required", "restart_required": True}

    monkeypatch.setattr(linux_cuda, "repair_linux_cuda", repair)
    response = client.post("/api/runtimes/linux-cuda/install")
    assert response.status_code == 200
    assert response.json()["restart_required"] is True

    async def failure() -> dict[str, Any]:
        raise CapabilityUnavailableError("NVIDIA driver unavailable; use CPU/int8.")

    monkeypatch.setattr(linux_cuda, "repair_linux_cuda", failure)
    response = client.post("/api/runtimes/linux-cuda/install")
    assert response.status_code == 503
    assert "CPU/int8" in response.json()["detail"]
