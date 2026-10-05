import pytest
from cryptography.fernet import Fernet, InvalidToken

from local_meeting_ai.infrastructure.hosted_credentials import EncryptedFileKeyring


def test_encrypted_credentials_survive_restart_and_reject_wrong_key(tmp_path):
    path = tmp_path / "secrets" / "credentials.enc"
    key = Fernet.generate_key().decode()
    store = EncryptedFileKeyring(path, key)
    store.set_password("Meet2Notes", "summary", "private-api-key")
    store.set_password("Meet2Notes", "webhook", "signing-secret")
    assert b"private-api-key" not in path.read_bytes()
    restarted = EncryptedFileKeyring(path, key)
    assert restarted.get_password("Meet2Notes", "summary") == "private-api-key"
    restarted.delete_password("Meet2Notes", "summary")
    assert restarted.get_password("Meet2Notes", "summary") is None
    assert restarted.get_password("Meet2Notes", "webhook") == "signing-secret"
    with pytest.raises(InvalidToken):
        EncryptedFileKeyring(path, Fernet.generate_key().decode()).set_password("x", "y", "z")
    assert restarted.get_password("Meet2Notes", "webhook") == "signing-secret"
