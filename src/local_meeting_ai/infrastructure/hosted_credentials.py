"""Encrypted persistent keyring for the single-process hosted deployment."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import cast

from cryptography.fernet import Fernet
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError


class EncryptedFileKeyring(KeyringBackend):
    priority = 1

    def __init__(self, path: Path, key: str) -> None:
        self.path = path
        try:
            self.cipher = Fernet(key.encode("ascii"))
        except ValueError:
            raise ValueError(
                "Invalid M2N_SECRETS_KEY: expected a URL-safe base64-encoded 32-byte "
                "Fernet key, not a password. Paste the complete generated key including "
                "its trailing '=' into the Coolify runtime variable. "
                "If credentials were previously saved, restore the original key. "
                "See COOLIFY_DEPLOYMENT.md; the credential store was not modified."
            ) from None
        self.lock = threading.RLock()
        if self.path.exists():
            self._read()

    def _read(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        # Invalid keys or corrupt stores must fail, never silently reset secrets.
        return cast(
            dict[str, dict[str, str]], json.loads(self.cipher.decrypt(self.path.read_bytes()))
        )

    def _write(self, values: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(self.cipher.encrypt(json.dumps(values).encode("utf-8")))
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(self.path)

    def get_password(self, service: str, username: str) -> str | None:
        with self.lock:
            return self._read().get(service, {}).get(username)

    def set_password(self, service: str, username: str, password: str) -> None:
        with self.lock:
            values = self._read()
            values.setdefault(service, {})[username] = password
            self._write(values)

    def delete_password(self, service: str, username: str) -> None:
        with self.lock:
            values = self._read()
            if username not in values.get(service, {}):
                raise PasswordDeleteError("Password not found")
            del values[service][username]
            self._write(values)
