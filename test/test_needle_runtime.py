import hashlib
import importlib.util
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from integration.needle_runtime import (
    ENGINE_FILE,
    ENGINE_VERSION,
    ENGINE_WHEEL_SHA256,
    GENERATION,
    LIBRARY_FILE,
    MODEL_FILE,
    MODEL_REPO,
    MODEL_REVISION,
    MODEL_SHA256,
    PACKAGE_VERSION,
    PLATFORM_TAG,
    NeedleArtifactContract,
    preflight,
    runtime_status,
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class NeedleRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('needle'), 'cactus-needle integration dependency not installed')
    def test_installed_runtime_constants_match_contract(self):
        import importlib.metadata

        import needle.agent.fetch as fetch

        self.assertEqual(importlib.metadata.version('cactus-needle'), PACKAGE_VERSION)
        self.assertEqual(fetch.ENGINE_VERSIONS[GENERATION], ENGINE_VERSION)
        self.assertEqual(fetch.ENGINE_REPOS[GENERATION], MODEL_REPO)
        self.assertEqual(fetch.BASE_WEIGHTS[GENERATION], MODEL_FILE)
        self.assertEqual(fetch._platform_tag(), PLATFORM_TAG)
        self.assertEqual(fetch._lib_name(), LIBRARY_FILE)
        self.assertEqual(
            ENGINE_FILE,
            'python/cactus_needle-3.0.1-py3-none-manylinux2014_x86_64.whl',
        )

    def test_resolved_artifact_contract_defaults_to_immutable_pins(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            contract = NeedleArtifactContract.from_env()
        self.assertEqual(contract.revision, MODEL_REVISION)
        self.assertEqual(contract.model_sha256, MODEL_SHA256)
        self.assertEqual(contract.engine_wheel_sha256, ENGINE_WHEEL_SHA256)

    def test_artifact_identity_cannot_be_overridden(self):
        with mock.patch.dict(os.environ, {'NEEDLE_MODEL_REPO': 'other/model'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'must match the pinned value'):
                NeedleArtifactContract.from_env()

    def test_artifact_checksums_and_idempotent_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = b'model'
            engine = b'engine'
            (root / MODEL_FILE).write_bytes(model)
            (root / LIBRARY_FILE).write_bytes(engine)
            contract = NeedleArtifactContract('revision', digest(model), 'a' * 64, digest(engine), root)
            contract.engine_provenance_path.write_text('a' * 64 + '\n')
            first = contract.verify()
            second = contract.verify()
            self.assertEqual(first, second)
            self.assertEqual(first['model_bytes'], len(model))
            (root / MODEL_FILE).write_bytes(b'tampered')
            with self.assertRaisesRegex(RuntimeError, 'SHA256 mismatch'):
                contract.verify()

    def test_preflight_contract_enters_offline_base_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = b'model'
            engine = b'engine'
            (root / MODEL_FILE).write_bytes(model)
            (root / LIBRARY_FILE).write_bytes(engine)
            (root / '.engine-wheel.sha256').write_text(ENGINE_WHEEL_SHA256 + '\n')
            environment = {
                'NEEDLE_MODEL_REVISION': MODEL_REVISION,
                'NEEDLE_MODEL_SHA256': digest(model),
                'NEEDLE_ENGINE_WHEEL_SHA256': ENGINE_WHEEL_SHA256,
                'NEEDLE_ENGINE_LIBRARY_SHA256': digest(engine),
                'NEEDLE_TELEMETRY': '0',
            }
            fake_agent = mock.MagicMock()
            fake_needle = mock.MagicMock()
            fake_needle.Needle.return_value = fake_agent
            with (
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch('integration.needle_runtime.default_cache_dir', return_value=root),
                mock.patch('integration.needle_runtime.MODEL_SHA256', digest(model)),
                mock.patch(
                    'integration.needle_runtime.importlib.metadata.version',
                    return_value=PACKAGE_VERSION,
                ),
                mock.patch.dict('sys.modules', {'needle': fake_needle}),
            ):
                status = preflight()
                self.assertEqual(os.environ['HF_HUB_OFFLINE'], '1')
            self.assertTrue(status['runtime_verified'])
            self.assertNotIn('weights', fake_needle.Needle.call_args.kwargs)
            fake_agent.close.assert_called_once()

    def test_fetch_script_uses_resolved_pins_before_network(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            contract = NeedleArtifactContract.from_env()
        self.assertEqual(contract.revision, MODEL_REVISION)
        self.assertNotEqual(contract.revision, 'main')
        self.assertEqual(contract.engine_wheel_sha256, ENGINE_WHEEL_SHA256)

    def test_fetch_prepares_exact_cache_and_reuses_verified_artifacts(self):
        spec = importlib.util.spec_from_file_location(
            'fetch_needle_artifacts', 'scripts/integration/fetch-needle-artifacts.py'
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloads = root / 'downloads'
            downloads.mkdir()
            model = downloads / MODEL_FILE
            model.write_bytes(b'real model bytes')
            wheel = downloads / 'engine.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('needle/libneedle3.so', b'real engine bytes')
            contract = NeedleArtifactContract(
                'immutable-revision',
                digest(b'real model bytes'),
                digest(wheel.read_bytes()),
                digest(b'real engine bytes'),
                root / 'cache',
            )
            calls = []

            def download(**kwargs):
                calls.append(kwargs)
                return model if kwargs['filename'] == MODEL_FILE else wheel

            first = module.prepare(contract, download)
            second = module.prepare(contract, download)
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 2)
            self.assertEqual({call['revision'] for call in calls}, {'immutable-revision'})

    def test_fetch_rejects_wheel_before_extraction(self):
        spec = importlib.util.spec_from_file_location(
            'fetch_needle_artifacts', 'scripts/integration/fetch-needle-artifacts.py'
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / MODEL_FILE
            model.write_bytes(b'model')
            wheel = root / 'engine.whl'
            wheel.write_bytes(b'not the pinned wheel')
            contract = NeedleArtifactContract(
                MODEL_REVISION,
                digest(b'model'),
                '0' * 64,
                None,
                root / 'cache',
            )

            def download(**kwargs):
                return model if kwargs['filename'] == MODEL_FILE else wheel

            with self.assertRaisesRegex(RuntimeError, 'downloaded'):
                module.prepare(contract, download)
            self.assertFalse(contract.library_path.exists())

    def test_status_does_not_expose_paths_or_trigger_initialization(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            status = runtime_status(last_route_ms=12.5)
        self.assertEqual(status['last_route_ms'], 12.5)
        self.assertFalse(status['artifacts_present'])
        self.assertNotIn('path', ' '.join(status))


if __name__ == '__main__':
    unittest.main()
