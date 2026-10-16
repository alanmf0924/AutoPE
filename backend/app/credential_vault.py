from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class CredentialVaultError(RuntimeError):
    pass


def _vault_key_path() -> Path:
    configured_path = os.getenv("AUTOPE_LOCAL_VAULT_KEY_FILE", "").strip()
    return Path(configured_path) if configured_path else Path(".autope-vault.key")


def _load_or_create_vault_key() -> bytes:
    configured_key = os.getenv("AUTOPE_LOCAL_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if configured_key:
        return configured_key.encode()

    key_path = _vault_key_path()
    if key_path.exists():
        return key_path.read_bytes().strip()

    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    try:
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return key_path.read_bytes().strip()
    with os.fdopen(descriptor, "wb") as file:
        file.write(key)
    return key


def _fernet() -> Fernet:
    if get_settings().environment in {"shared_test", "production"}:
        raise CredentialVaultError("The local credential vault is disabled in deployed environments.")
    try:
        return Fernet(_load_or_create_vault_key())
    except ValueError as error:
        raise CredentialVaultError("The local credential vault key is invalid.") from error


def encrypt_local_api_key(api_key: str) -> str:
    if not api_key.strip():
        raise CredentialVaultError("A Dify API key is required.")
    return _fernet().encrypt(api_key.strip().encode()).decode()


def decrypt_local_api_key(encrypted_api_key: str) -> str:
    try:
        return _fernet().decrypt(encrypted_api_key.encode()).decode()
    except (InvalidToken, UnicodeDecodeError) as error:
        raise CredentialVaultError("The stored Dify credential cannot be decrypted.") from error
