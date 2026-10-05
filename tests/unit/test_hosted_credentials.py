import pytest
from cryptography.fernet import Fernet, InvalidToken

from local_meeting_ai.infrastructure.hosted_credentials import EncryptedFileKeyring


@pytest.mark.parametrize("key", ["private-password", "a" * 32, "中文金鑰", ""])
def test_invalid_key_has_actionable_error_without_modifying_store(tmp_path, key):
    path = tmp_path / "credentials.enc"
    original = b"existing encrypted credentials"
    path.write_bytes(original)
    with pytest.raises(ValueError, match="Invalid M2N_SECRETS_KEY") as error:
        EncryptedFileKeyring(path, key)
    assert "restore the original key" in str(error.value)
    if key:
        assert key not in str(error.value)
    assert path.read_bytes() == original


def test_generated_key_requires_its_padding(tmp_path):
    key = Fernet.generate_key().decode()
    with pytest.raises(ValueError, match="Invalid M2N_SECRETS_KEY"):
        EncryptedFileKeyring(tmp_path / "credentials.enc", key.rstrip("="))
    assert not (tmp_path / "credentials.enc").exists()


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
