"""Encrypted OAuth persistence with a conservative, single-writer write budget.

The dedicated directory must have no other writers. This is a refusal budget
for this backend, not a filesystem quota for the directory or the VPS. Existing
records are never evicted to make room for a new OAuth registration.
"""

import asyncio
from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
import stat
from typing import Any, SupportsFloat

from cryptography.fernet import Fernet
from fastmcp.server.auth.jwt_issuer import derive_jwt_key
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper


class OAuthStorageFull(RuntimeError):
    """The dedicated OAuth store cannot safely admit another write."""


class BoundedOAuthFileTreeStore(FileTreeStore):
    """Serialize writes within one process and inspect actual on-disk usage."""

    def __init__(self, directory: Path, max_bytes: int, max_files: int):
        for name, value in (("max_bytes", max_bytes), ("max_files", max_files)):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.directory = directory.expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.max_bytes, self.max_files = max_bytes, max_files
        self._write_lock = asyncio.Lock()
        super().__init__(
            data_directory=self.directory,
            key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(self.directory),
            collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(
                self.directory),
        )

    def _usage(self) -> tuple[int, int]:
        """Count regular files and allocated/logical bytes, including directories."""
        files = allocated = 0
        pending = [self.directory]
        while pending:
            path = pending.pop()
            info = path.lstat()
            allocated += max(info.st_size, getattr(info, "st_blocks", 0) * 512)
            if stat.S_ISDIR(info.st_mode):
                with os.scandir(path) as entries:
                    pending.extend(Path(entry.path) for entry in entries)
            elif stat.S_ISREG(info.st_mode):
                files += 1
            else:
                raise OAuthStorageFull(
                    "OAUTH_STORAGE_UNSAFE: only dedicated regular files and directories are allowed")
        return files, allocated

    async def _admit(self, values: Sequence[Mapping[str, Any]]) -> None:
        # This backend receives the already encrypted wrapper dictionaries.
        # Reserve two files and at least 16 KiB per entry for metadata, block
        # rounding, a new collection directory, and the parent's write overhead.
        reserve = sum(
            2 * len(json.dumps(value, ensure_ascii=True, allow_nan=False,
                               separators=(",", ":")).encode("utf-8")) + 16384
            for value in values
        )
        files, allocated = await asyncio.to_thread(self._usage)
        if files + 2 * len(values) > self.max_files:
            raise OAuthStorageFull("OAUTH_STORAGE_FULL: OAuth file budget exceeded")
        if allocated + reserve > self.max_bytes:
            raise OAuthStorageFull("OAUTH_STORAGE_FULL: OAuth byte budget exceeded")

    async def put(self, key: str, value: Mapping[str, Any], *,
                  collection: str | None = None, ttl: SupportsFloat | None = None) -> None:
        snapshot = dict(value)
        async with self._write_lock:
            await self._admit([snapshot])
            await super().put(key, snapshot, collection=collection, ttl=ttl)

    async def put_many(self, keys: Sequence[str], values: Sequence[Mapping[str, Any]], *,
                       collection: str | None = None,
                       ttl: SupportsFloat | None = None) -> None:
        keys, snapshots = list(keys), [dict(value) for value in values]
        if len(keys) != len(snapshots):
            raise ValueError("put_many requires the same number of keys and values")
        if not keys:
            return
        # FileTreeStore.put_many does not call its public put method, so this
        # independent path cannot recursively acquire the same lock.
        async with self._write_lock:
            await self._admit(snapshots)
            await super().put_many(keys, snapshots, collection=collection, ttl=ttl)


def build_oauth_storage(directory: Path, signing_key: str,
                        max_bytes: int = 8388608, max_files: int = 512) -> AsyncKeyValue:
    """Build an encrypted, bounded store from stable deployment key material."""
    if (not isinstance(signing_key, str) or len(signing_key) < 43 or
            not signing_key.isascii() or signing_key.startswith("REPLACE_")):
        raise ValueError("provide stable, high-entropy signing material of at least 32 random bytes")
    backend = BoundedOAuthFileTreeStore(Path(directory), max_bytes, max_files)
    encryption_key = derive_jwt_key(
        high_entropy_material=signing_key,
        salt="task-mailbox-oauth-storage-v1",
    )
    return FernetEncryptionWrapper(
        key_value=backend, fernet=Fernet(key=encryption_key), raise_on_decryption_error=True)
