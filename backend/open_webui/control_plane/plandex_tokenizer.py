"""Pinned, offline Plandex tokenizer artifact contract.

The SHA-1 filename is tiktoken-go's URL cache key.  The SHA-256 is the
independent integrity identity and must be checked before Plandex starts.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

CANONICAL_URL = 'https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken'
EXPECTED_SHA256 = '446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d'
EXPECTED_CACHE_KEY = 'fb374d419588a4632f3f557e76b4b70aebbca790'
DEFAULT_CACHE_DIR = Path('/opt/integration/tiktoken-cache')
MIRROR_REPOSITORY = 'rmusser01/tldw_chatbook'
MIRROR_COMMIT = 'b0dadf19414f5f8faf69b854d8e59007d275083e'
MIRROR_PATH = f'tldw_chatbook/assets/tiktoken_cache/{EXPECTED_CACHE_KEY}'
MIRROR_URL = f'https://raw.githubusercontent.com/{MIRROR_REPOSITORY}/{MIRROR_COMMIT}/{MIRROR_PATH}'


class TokenizerArtifactError(RuntimeError):
    pass


def cache_key(url: str = CANONICAL_URL) -> str:
    return hashlib.sha1(url.encode('utf-8')).hexdigest()  # noqa: S324 -- upstream cache identity, not security


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifact_path(cache_dir: str | os.PathLike[str] | None = None) -> Path:
    root = Path(cache_dir or os.getenv('TIKTOKEN_CACHE_DIR', DEFAULT_CACHE_DIR))
    return root / EXPECTED_CACHE_KEY


def verify_bytes(data: bytes, expected_sha256: str = EXPECTED_SHA256) -> None:
    actual = sha256_bytes(data)
    if actual != expected_sha256:
        raise TokenizerArtifactError(f'tokenizer SHA256 mismatch: expected {expected_sha256}, got {actual}')


def preflight(
    cache_dir: str | os.PathLike[str] | None = None,
    *,
    expected_sha256: str = EXPECTED_SHA256,
) -> Path:
    if cache_key() != EXPECTED_CACHE_KEY:
        raise TokenizerArtifactError('canonical tokenizer URL/cache key drift')
    path = artifact_path(cache_dir)
    if not path.parent.is_dir():
        raise TokenizerArtifactError('tokenizer cache directory missing')
    if not path.is_file():
        raise TokenizerArtifactError('tokenizer cache artifact missing')
    verify_bytes(path.read_bytes(), expected_sha256)
    return path


def install_bytes(
    data: bytes,
    cache_dir: str | os.PathLike[str],
    *,
    expected_sha256: str = EXPECTED_SHA256,
) -> Path:
    """Atomically install already-downloaded, verified bytes.

    ``expected_sha256`` exists solely to permit tiny deterministic test
    fixtures. Production callers use the immutable default.
    """
    verify_bytes(data, expected_sha256)
    destination = artifact_path(cache_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        try:
            verify_bytes(destination.read_bytes(), expected_sha256)
            return destination
        except TokenizerArtifactError:
            pass
    fd, temporary = tempfile.mkstemp(prefix=f'.{EXPECTED_CACHE_KEY}.', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o444)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def fetch_and_install(cache_dir: str | os.PathLike[str], fetch_url: str | None = None) -> Path:
    source = fetch_url or CANONICAL_URL
    with urllib.request.urlopen(source, timeout=120) as response:
        data = response.read()
    # Always validate canonical content and install under the canonical URL's
    # cache key, even when a build-only mirror supplied the bytes.
    return install_bytes(data, cache_dir)
