"""Encryption at rest.

Some of the sources OmaDex reads protect their contacts on disk. Aggregating
them into a plaintext database would quietly remove a protection the user was
already given, so the merged store is encrypted too: one random key held in
the Secret Service, and AES-GCM per stored value.

Values carry a versioned prefix so a stored value can always say what it is,
which matters when a database written by one version is read by another.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass

PREFIX = "omadex:aesgcm:v1:"
KEY_BYTES = 32
NONCE_BYTES = 12

SECRET_LABEL = "OmaDex local storage key"
SECRET_ATTRIBUTES = {"purpose": "omadex-storage", "version": "1"}


class StorageUnavailable(RuntimeError):
    """The wallet could not supply a key, so nothing may be written."""


@dataclass(frozen=True, slots=True)
class KeyRing:
    """The wallet-backed key, resolved once per process."""

    key: bytes

    @staticmethod
    def _secret_module():
        try:
            import gi

            gi.require_version("Secret", "1")
            from gi.repository import Secret
        except (ImportError, ValueError) as error:
            raise StorageUnavailable(
                "no Secret Service client is installed (needs libsecret and "
                "python-gobject)"
            ) from error
        return Secret

    @classmethod
    def open(cls, *, create: bool = True) -> KeyRing:
        Secret = cls._secret_module()
        schema = Secret.Schema.new(
            "com.omadex.Storage",
            Secret.SchemaFlags.NONE,
            {
                "purpose": Secret.SchemaAttributeType.STRING,
                "version": Secret.SchemaAttributeType.STRING,
            },
        )
        try:
            stored = Secret.password_lookup_sync(schema, SECRET_ATTRIBUTES, None)
        except Exception as error:  # noqa: BLE001 - a locked wallet raises
            raise StorageUnavailable(
                f"the desktop wallet is unavailable: {error}"
            ) from error

        if stored:
            key = base64.b64decode(stored)
            if len(key) != KEY_BYTES:
                raise StorageUnavailable("the stored key is the wrong length")
            return cls(key)

        if not create:
            raise StorageUnavailable("no storage key exists yet")

        key = os.urandom(KEY_BYTES)
        try:
            Secret.password_store_sync(
                schema, SECRET_ATTRIBUTES, Secret.COLLECTION_DEFAULT,
                SECRET_LABEL, base64.b64encode(key).decode("ascii"), None,
            )
        except Exception as error:  # noqa: BLE001
            raise StorageUnavailable(f"could not store a key: {error}") from error
        return cls(key)

    def encrypt(self, plaintext: str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(NONCE_BYTES)
        sealed = AESGCM(self.key).encrypt(nonce, plaintext.encode("utf-8"), None)
        return PREFIX + base64.b64encode(nonce + sealed).decode("ascii")

    def blind(self, value: str) -> str:
        """A deterministic, keyed digest used where a column must be matched.

        Lookup columns cannot hold ciphertext, because two encryptions of the
        same address differ. They must not hold the address either. An HMAC
        under the same wallet key keeps rows joinable and equality-searchable
        while leaving nothing legible to anyone reading the file.
        """
        import hmac
        from hashlib import sha256

        return hmac.new(self.key, value.encode("utf-8"), sha256).hexdigest()

    def decrypt(self, value: str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if not is_encrypted(value):
            # A database written before encryption, or with it turned off.
            return value
        raw = base64.b64decode(value[len(PREFIX):])
        nonce, sealed = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
        return AESGCM(self.key).decrypt(nonce, sealed, None).decode("utf-8")


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)
