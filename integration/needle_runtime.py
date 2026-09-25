"""Offline Needle3 artifact contract and startup preflight."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GENERATION = 3
PACKAGE_VERSION = '3.0.1'
ENGINE_VERSION = '3.0.1'
MODEL_REPO = 'Cactus-Compute/needle3'
MODEL_FILE = 'needle3.cact'
PLATFORM_TAG = 'manylinux2014_x86_64'
ENGINE_FILE = f'python/cactus_needle-{ENGINE_VERSION}-py3-none-{PLATFORM_TAG}.whl'
LIBRARY_FILE = 'libneedle.so'
MODEL_REVISION = '0f51a1ac2917a03644c4cc7836f19476c6d177dd'
MODEL_SHA256 = 'c9d915eca282ed42d1a09b143b592adb4cc6744ffe2d294adf5cfc5548170c38'
ENGINE_WHEEL_SHA256 = '05770ef9a85686583968ea15f62f9ad44217e078efdaa99559d3208bb8a369b0'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def default_cache_dir() -> Path:
    return Path.home() / '.cache' / 'cactus-needle' / 'v3' / ENGINE_VERSION


@dataclass(frozen=True)
class NeedleArtifactContract:
    revision: str
    model_sha256: str
    engine_wheel_sha256: str
    engine_library_sha256: str | None
    cache_dir: Path

    @classmethod
    def from_env(cls, cache_dir: str | Path | None = None) -> NeedleArtifactContract:
        configured = {
            'NEEDLE_MODEL_REPO': (os.getenv('NEEDLE_MODEL_REPO', MODEL_REPO), MODEL_REPO),
            'NEEDLE_MODEL_FILE': (os.getenv('NEEDLE_MODEL_FILE', MODEL_FILE), MODEL_FILE),
            'NEEDLE_ENGINE_FILE': (os.getenv('NEEDLE_ENGINE_FILE', ENGINE_FILE), ENGINE_FILE),
        }
        for name, (actual, expected) in configured.items():
            if actual != expected:
                raise RuntimeError(f'{name} must match the pinned value {expected!r}')
        revision = os.getenv('NEEDLE_MODEL_REVISION', MODEL_REVISION).strip()
        model_hash = os.getenv('NEEDLE_MODEL_SHA256', MODEL_SHA256).strip().lower()
        wheel_hash = os.getenv('NEEDLE_ENGINE_WHEEL_SHA256', ENGINE_WHEEL_SHA256).strip().lower()
        library_hash = os.getenv('NEEDLE_ENGINE_LIBRARY_SHA256', '').strip().lower() or None
        if revision != MODEL_REVISION:
            raise RuntimeError(f'NEEDLE_MODEL_REVISION must match the pinned value {MODEL_REVISION!r}')
        for name, value in (('NEEDLE_MODEL_SHA256', model_hash), ('NEEDLE_ENGINE_WHEEL_SHA256', wheel_hash)):
            if len(value) != 64 or any(char not in '0123456789abcdef' for char in value):
                raise RuntimeError(f'{name} must be a 64-character hexadecimal SHA256')
        if model_hash != MODEL_SHA256 or wheel_hash != ENGINE_WHEEL_SHA256:
            raise RuntimeError('Needle artifact checksums must match the pinned deployment contract')
        if library_hash and (len(library_hash) != 64 or any(char not in '0123456789abcdef' for char in library_hash)):
            raise RuntimeError('NEEDLE_ENGINE_LIBRARY_SHA256 must be a 64-character hexadecimal SHA256')
        return cls(
            revision, model_hash, wheel_hash, library_hash, Path(cache_dir) if cache_dir else default_cache_dir()
        )

    @property
    def model_path(self) -> Path:
        return self.cache_dir / MODEL_FILE

    @property
    def library_path(self) -> Path:
        return self.cache_dir / LIBRARY_FILE

    @property
    def engine_provenance_path(self) -> Path:
        return self.cache_dir / '.engine-wheel.sha256'

    def verify(self) -> dict[str, Any]:
        for label, path, expected in (
            ('base model', self.model_path, self.model_sha256),
            ('native engine', self.library_path, self.engine_library_sha256),
        ):
            if not path.is_file():
                raise RuntimeError(f'Needle {label} artifact is missing')
            actual = sha256(path)
            if expected and actual != expected:
                raise RuntimeError(f'Needle {label} SHA256 mismatch')
        if (
            not self.engine_provenance_path.is_file()
            or self.engine_provenance_path.read_text().strip() != self.engine_wheel_sha256
        ):
            raise RuntimeError('Needle native engine wheel provenance is missing or invalid')
        return {
            'artifacts_present': True,
            'model_bytes': self.model_path.stat().st_size,
            'engine_bytes': self.library_path.stat().st_size,
            'engine_library_sha256': sha256(self.library_path),
        }


def preflight(*, initialize: bool = True) -> dict[str, Any]:
    installed = importlib.metadata.version('cactus-needle')
    if installed != PACKAGE_VERSION:
        raise RuntimeError(f'expected cactus-needle {PACKAGE_VERSION}, found {installed}')
    contract = NeedleArtifactContract.from_env()
    artifact_status = contract.verify()
    status: dict[str, Any] = {
        'installed': True,
        **artifact_status,
        'loaded': False,
        'model_generation': GENERATION,
        'runtime_verified': False,
        'telemetry_disabled': os.getenv('NEEDLE_TELEMETRY', '0') == '0',
    }
    if initialize:
        # Offline mode makes any unexpected Hugging Face access fail immediately.
        os.environ['HF_HUB_OFFLINE'] = '1'
        import needle

        agent = needle.Needle(tools=[], generation=GENERATION, auto_date=False)
        agent.close()
        status['loaded'] = True
        status['runtime_verified'] = True
    return status


def runtime_status(*, loaded: bool = False, last_route_ms: float | None = None) -> dict[str, Any]:
    """Non-sensitive internal status; never triggers downloads or initialization."""
    try:
        installed_version = importlib.metadata.version('cactus-needle')
    except importlib.metadata.PackageNotFoundError:
        installed_version = None
    artifacts_present = False
    checksums_valid = False
    try:
        contract = NeedleArtifactContract.from_env()
        contract.verify()
        artifacts_present = checksums_valid = True
    except RuntimeError:
        pass
    return {
        'installed': installed_version == PACKAGE_VERSION,
        'artifacts_present': artifacts_present,
        'checksums_valid': checksums_valid,
        'loaded': loaded,
        'model_generation': GENERATION,
        'runtime_verified': loaded and artifacts_present,
        'last_route_ms': last_route_ms,
        'telemetry_disabled': os.getenv('NEEDLE_TELEMETRY', '0') == '0',
    }
