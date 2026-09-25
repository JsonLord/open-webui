#!/usr/bin/env python3
"""Build-time-only acquisition of pinned stock Needle3 artifacts."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from integration.needle_runtime import (
    ENGINE_FILE,
    MODEL_FILE,
    MODEL_REPO,
    NeedleArtifactContract,
    sha256,
)


def copy_verified(source: Path, target: Path, expected: str) -> None:
    if target.is_file() and sha256(target) == expected:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + '.tmp')
    shutil.copyfile(source, temporary)
    if sha256(temporary) != expected:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f'SHA256 mismatch for {target.name}')
    temporary.replace(target)


def prepare(contract: NeedleArtifactContract, downloader, *, token: str | None = None):
    try:
        return contract.verify()
    except RuntimeError:
        model_download = Path(
            downloader(
                repo_id=MODEL_REPO,
                filename=MODEL_FILE,
                revision=contract.revision,
                token=token,
            )
        )
        wheel_download = Path(
            downloader(
                repo_id=MODEL_REPO,
                filename=ENGINE_FILE,
                revision=contract.revision,
                token=token,
            )
        )
        if sha256(wheel_download) != contract.engine_wheel_sha256:
            raise RuntimeError(f'SHA256 mismatch for downloaded {ENGINE_FILE}')
        copy_verified(model_download, contract.model_path, contract.model_sha256)
        with zipfile.ZipFile(wheel_download) as archive:
            member = 'needle/libneedle3.so'
            if member not in archive.namelist():
                raise RuntimeError(f'native engine member missing from {ENGINE_FILE}: {member}')
            extracted = contract.cache_dir / '.libneedle3.so.download'
            extracted.write_bytes(archive.read(member))
        try:
            if contract.engine_library_sha256:
                copy_verified(extracted, contract.library_path, contract.engine_library_sha256)
            else:
                contract.library_path.parent.mkdir(parents=True, exist_ok=True)
                extracted.replace(contract.library_path)
            contract.engine_provenance_path.write_text(contract.engine_wheel_sha256 + '\n')
        finally:
            extracted.unlink(missing_ok=True)
        return contract.verify()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir')
    args = parser.parse_args()
    try:
        contract = NeedleArtifactContract.from_env(args.cache_dir)
        from huggingface_hub import hf_hub_download

        status = prepare(contract, hf_hub_download, token=os.getenv('HF_TOKEN') or None)
        print(f"Needle model: {contract.model_path} ({status['model_bytes']} bytes)")
        print(f"Needle engine: {contract.library_path} ({status['engine_bytes']} bytes)")
        return 0
    except Exception as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
