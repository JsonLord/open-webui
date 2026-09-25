import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.control_plane import plandex_tokenizer as tokenizer  # noqa: E402
from open_webui.control_plane.adapters import PlandexExecutor  # noqa: E402


class PlandexTokenizerTests(unittest.TestCase):
    def test_deployment_loader_does_not_import_web_application(self):
        script = ROOT / 'scripts' / 'integration' / 'plandex_tokenizer_runtime.py'
        statement = f"import runpy,sys; runpy.run_path({str(script)!r}); print('open_webui' in sys.modules)"
        result = subprocess.run(
            [sys.executable, '-I', '-c', statement],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), 'False')

    def test_canonical_cache_key_derivation(self):
        self.assertEqual(hashlib.sha1(tokenizer.CANONICAL_URL.encode()).hexdigest(), tokenizer.EXPECTED_CACHE_KEY)
        self.assertEqual(tokenizer.cache_key(), tokenizer.EXPECTED_CACHE_KEY)
        self.assertEqual(tokenizer.EXPECTED_SHA256, '446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d')

    def test_mirror_is_immutable_transport_only(self):
        self.assertIn(tokenizer.MIRROR_COMMIT, tokenizer.MIRROR_URL)
        self.assertTrue(tokenizer.MIRROR_URL.endswith('/' + tokenizer.EXPECTED_CACHE_KEY))
        self.assertNotIn('/main/', tokenizer.MIRROR_URL)

    def test_atomic_fixture_install_and_preflight(self):
        data = b'deterministic test fixture, not the production tokenizer'
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = tokenizer.install_bytes(data, directory, expected_sha256=digest)
            self.assertEqual(path.name, tokenizer.EXPECTED_CACHE_KEY)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(path.stat().st_mode & 0o777, 0o444)
            self.assertEqual(tokenizer.preflight(directory, expected_sha256=digest), path)

    def test_corrupt_and_missing_artifacts_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(tokenizer.TokenizerArtifactError, 'missing'):
                tokenizer.preflight(directory)
            tokenizer.artifact_path(directory).write_bytes(b'corrupt')
            with self.assertRaisesRegex(tokenizer.TokenizerArtifactError, 'mismatch'):
                tokenizer.preflight(directory)

    def test_wrong_cache_key_does_not_satisfy_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'wrong-key').write_bytes(b'any')
            with self.assertRaisesRegex(tokenizer.TokenizerArtifactError, 'missing'):
                tokenizer.preflight(directory)

    def test_missing_cache_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'absent'
            with self.assertRaisesRegex(tokenizer.TokenizerArtifactError, 'directory missing'):
                tokenizer.preflight(missing)

    def test_plandex_child_preserves_cache_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'print-cache'
            executable.write_text('#!/bin/sh\nprintf "%s|%s|%s" "$TIKTOKEN_CACHE_DIR" "$HOME" "$PLANDEX_API_HOST"\n')
            executable.chmod(0o755)
            adapter = PlandexExecutor(executable=str(executable), require_tokenizer=True)
            with mock.patch.dict(
                os.environ,
                {'TIKTOKEN_CACHE_DIR': '/verified/cache', 'PLANDEX_CLI_HOME': '/persistent/plandex'},
            ):
                with mock.patch('open_webui.control_plane.adapters.tokenizer_preflight') as check:
                    self.assertEqual(
                        adapter.status('task', directory),
                        '/verified/cache|/persistent/plandex|http://127.0.0.1:8099',
                    )
            check.assert_called_once_with('/verified/cache')

    def test_plandex_is_not_started_after_preflight_failure(self):
        adapter = PlandexExecutor(executable='/should/not/run', require_tokenizer=True)
        with mock.patch.dict(os.environ, {'TIKTOKEN_CACHE_DIR': '/missing'}, clear=False):
            with self.assertRaisesRegex(RuntimeError, '^plandex_tokenizer_unavailable$'):
                adapter.status('task', '/')


if __name__ == '__main__':
    unittest.main()
