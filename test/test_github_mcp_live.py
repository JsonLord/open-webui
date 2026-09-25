"""Opt-in protocol test for the pinned, read-only GitHub MCP binary."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from open_webui.control_plane.adapters import GitHubMCP  # noqa: E402

BINARY = os.getenv('GITHUB_MCP_BINARY')


@unittest.skipUnless(BINARY, 'GITHUB_MCP_BINARY is not configured')
class GitHubMCPProtocolTests(unittest.TestCase):
    def test_initialize_and_read_only_tools_list(self):
        recognizable_fake = 'PHASE3D_FAKE_TOKEN_DO_NOT_LEAK_9f3a'
        with mock.patch.dict(os.environ, {'GITHUB_PAT': recognizable_fake}, clear=False):
            client = GitHubMCP(command=(BINARY, 'stdio', '--read-only'), timeout=20)
            try:
                client.start()
                result = client._request('tools/list', {})
            finally:
                client.close()
        names = {tool['name'] for tool in result['tools']}
        self.assertIn('issue_read', names)
        self.assertIn('get_file_contents', names)
        self.assertIn('list_pull_requests', names)
        self.assertIn('actions_list', names)
        write_markers = ('create', 'delete', 'merge', 'update', 'push')
        self.assertFalse([name for name in names if any(marker in name for marker in write_markers)])


if __name__ == '__main__':
    unittest.main()
