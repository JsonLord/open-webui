from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .repositories import RepositoryAllowlist, contained_path, task_branch


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedRepository:
    repository: str
    branch: str
    worktree: Path
    base_commit_sha: str


class GitWorktrees:
    def __init__(self, cache_root: Path, worktree_root: Path, allowlist: RepositoryAllowlist):
        self.cache_root, self.worktree_root, self.allowlist = cache_root.resolve(), worktree_root.resolve(), allowlist

    def _run(self, args, *, cwd=None):
        child_env = {
            key: value
            for key, value in os.environ.items()
            if key not in {'GITHUB_PAT', 'GITHUB_PERSONAL_ACCESS_TOKEN', 'GH_TOKEN'}
        }
        result = subprocess.run(
            ['git', *args], cwd=cwd, text=True, capture_output=True, timeout=120, env=child_env
        )
        if result.returncode:
            raise GitError(result.stderr.strip() or 'git operation failed')
        return result.stdout.strip()

    def prepare(
        self, repository: str, base_branch: str, task_id: str, slug: str, clone_url: str | None = None
    ) -> PreparedRepository:
        repository = self.allowlist.authorize(repository)
        if (
            not base_branch
            or base_branch.startswith('-')
            or '..' in base_branch
            or not all(c.isalnum() or c in '._/-' for c in base_branch)
        ):
            raise ValueError('invalid base branch')
        owner, name = repository.split('/')
        cache = contained_path(self.cache_root, owner, f'{name}.git')
        worktree = contained_path(self.worktree_root, task_id)
        branch = task_branch(slug, task_id)
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            url = clone_url or f'https://github.com/{repository}.git'
            if url != f'https://github.com/{repository}.git':
                raise ValueError('clone URL does not match authorized repository')
            self._run(['clone', '--bare', url, str(cache)])
        self._run(['fetch', '--prune', 'origin', '+refs/heads/*:refs/remotes/origin/*'], cwd=cache)
        base_ref = f'refs/remotes/origin/{base_branch}'
        base_sha = self._run(['rev-parse', '--verify', base_ref], cwd=cache)
        if worktree.exists():
            registered = self._run(['worktree', 'list', '--porcelain'], cwd=cache)
            if f'worktree {worktree}' not in registered:
                raise GitError('existing path is not the task worktree')
        else:
            worktree.parent.mkdir(parents=True, exist_ok=True)
            self._run(['worktree', 'add', '-b', branch, str(worktree), base_ref], cwd=cache)
        return PreparedRepository(repository, branch, worktree, base_sha)

    def diff(self, worktree: str) -> str:
        path = Path(worktree).resolve()
        if self.worktree_root not in path.parents:
            raise ValueError('worktree outside configured root')
        return self._run(['diff', '--no-ext-diff'], cwd=path)
