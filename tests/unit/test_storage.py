from __future__ import annotations

from pathlib import Path

import pytest

from local_meeting_ai.config import AppSettings
from local_meeting_ai.domain.errors import UploadTooLargeError, ValidationError
from local_meeting_ai.infrastructure.storage import MeetingStorage
from local_meeting_ai.paths import AppPaths


def test_storage_refuses_directory_traversal(tmp_path: Path) -> None:
    paths = AppPaths.from_settings(
        AppSettings(
            data_dir=tmp_path / "data",
            models_dir=tmp_path / "models",
        )
    )
    paths.ensure()
    storage = MeetingStorage(paths, max_upload_bytes=1024)

    with pytest.raises(ValidationError, match="outside meeting storage"):
        storage.delete_meeting("../outside")


@pytest.mark.asyncio
@pytest.mark.parametrize("voice_sample", [False, True])
@pytest.mark.parametrize("limit", [0, 8])
async def test_zero_upload_limit_and_finite_limit(tmp_path: Path, voice_sample: bool,
                                                 limit: int) -> None:
    class Upload:
        filename = "test.wav"
        content_type = "audio/wav"
        sent = False
        closed = False

        async def read(self, size: int) -> bytes:
            if self.sent:
                return b""
            self.sent = True
            return b"x" * 32

        async def close(self) -> None:
            self.closed = True

    paths = AppPaths.from_settings(AppSettings(data_dir=tmp_path / "data",
                                              models_dir=tmp_path / "models"))
    paths.ensure()
    storage = MeetingStorage(paths, max_upload_bytes=limit)
    upload = Upload()
    operation = (storage.save_speaker_profile_sample(1, upload) if voice_sample
                 else storage.save_import("test", upload))
    if limit:
        with pytest.raises(UploadTooLargeError):
            await operation
        assert not list(paths.root.rglob("*.wav"))
    else:
        stored = await operation
        assert stored.size_bytes == 32
    assert upload.closed
