"""Dependency-backed OAuth storage checks; not part of stdlib test discovery."""

import asyncio
from pathlib import Path
import secrets
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oauth_storage import OAuthStorageFull, build_oauth_storage


async def rejected(operation, reason):
    try:
        await operation
    except OAuthStorageFull as error:
        assert reason in str(error), str(error)
    else:
        raise AssertionError("the OAuth quota admitted an over-budget write")


async def check_encryption_and_restart(directory, signing_key):
    store = build_oauth_storage(directory, signing_key)
    secret = "oauth-smoke-private-token-" + secrets.token_urlsafe(24)
    original = {"access_token": secret, "client_name": "storage smoke"}
    await store.put("original", original, collection="clients")
    await store.put_many(["batch-a", "batch-b"],
                         [{"token": "one"}, {"token": "two"}], collection="clients")
    assert await store.get("original", collection="clients") == original
    files = [path for path in directory.rglob("*") if path.is_file()]
    assert files, "the OAuth store did not persist any files"
    for path in files:
        raw = path.read_bytes()
        assert secret.encode() not in raw, "OAuth token appeared in plaintext"
        assert b'"access_token"' not in raw, "OAuth record appeared in plaintext"
    reopened = build_oauth_storage(directory, signing_key)
    assert await reopened.get("original", collection="clients") == original
    assert await reopened.get_many(["batch-a", "batch-b"], collection="clients") == [
        {"token": "one"}, {"token": "two"}]


async def check_byte_budget(directory, signing_key):
    store = build_oauth_storage(directory, signing_key, max_bytes=131072)
    original = {"token": "preserved byte-budget state"}
    await store.put("original", original, collection="clients")
    before = {path.relative_to(directory): path.read_bytes()
              for path in directory.rglob("*") if path.is_file()}
    await rejected(store.put("large", {"token": "x" * 131072}, collection="clients"),
                   "byte budget")
    await rejected(store.put_many(["large-a", "large-b"],
                                   [{"token": "x" * 65536}, {"token": "y" * 65536}],
                                   collection="clients"), "byte budget")
    assert await store.get("original", collection="clients") == original
    assert await store.get("large", collection="clients") is None
    assert await store.get("large-a", collection="clients") is None
    after = {path.relative_to(directory): path.read_bytes()
             for path in directory.rglob("*") if path.is_file()}
    assert after == before, "rejected writes changed existing OAuth files"


async def check_file_budget(directory, signing_key):
    store = build_oauth_storage(directory, signing_key)
    original = {"token": "preserved file-budget state"}
    await store.put("original", original, collection="clients")
    actual_files = sum(path.is_file() for path in directory.rglob("*"))
    limited = build_oauth_storage(directory, signing_key, max_files=actual_files + 1)
    await rejected(limited.put("another", {"token": "new"}, collection="clients"),
                   "file budget")
    await rejected(limited.put_many(["another-a", "another-b"], [{}, {}],
                                     collection="clients"), "file budget")
    assert await limited.get("original", collection="clients") == original
    assert await limited.get("another", collection="clients") is None
    assert sum(path.is_file() for path in directory.rglob("*")) == actual_files


async def check_directory_bytes(directory, signing_key):
    directory.mkdir()
    (directory / "existing-directory").mkdir()
    size = sum(max(path.stat().st_size, path.stat().st_blocks * 512)
               for path in (directory, directory / "existing-directory"))
    limited = build_oauth_storage(directory, signing_key, max_bytes=size)
    await rejected(limited.put("no-space", {}, collection="clients"), "byte budget")
    assert not any(path.is_file() for path in directory.rglob("*"))


async def main():
    signing_key = secrets.token_urlsafe(48)
    with tempfile.TemporaryDirectory(prefix="task-mailbox-oauth-smoke-") as temporary:
        directory = Path(temporary)
        await check_encryption_and_restart(directory / "encrypted", signing_key)
        await check_byte_budget(directory / "bytes", signing_key)
        await check_file_budget(directory / "files", signing_key)
        await check_directory_bytes(directory / "directories", signing_key)
    print("PASS: encrypted OAuth persistence, stable-key restart, byte/file refusal, old-state preservation, directory accounting")
    print("SCOPE: one process and one writer; conservative admission budget, not a filesystem or VPS disk quota")


if __name__ == "__main__":
    asyncio.run(main())
