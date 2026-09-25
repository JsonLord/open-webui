from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

REPO_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')


def normalize_repository(value: str) -> str:
    value = value.strip()
    if '://' in value:
        parsed = urlparse(value)
        if parsed.scheme != 'https' or parsed.hostname != 'github.com' or parsed.username or parsed.password:
            raise ValueError('only credential-free https://github.com repositories are accepted')
        value = parsed.path.strip('/')
    if value.endswith('.git'):
        value = value[:-4]
    if not REPO_RE.fullmatch(value) or '..' in value:
        raise ValueError('invalid GitHub repository identity')
    return value.lower()


@dataclass(frozen=True)
class RepositoryAllowlist:
    repositories: frozenset[str]
    organizations: frozenset[str]

    @classmethod
    def from_env(cls) -> RepositoryAllowlist:
        repos = frozenset(
            normalize_repository(x) for x in os.getenv('ALLOWED_GITHUB_REPOS', '').split(',') if x.strip()
        )
        raw_orgs = [x.strip().lower() for x in os.getenv('ALLOWED_GITHUB_ORGS', '').split(',') if x.strip()]
        if any(not re.fullmatch(r'[a-z0-9_.-]+', org) or '..' in org for org in raw_orgs):
            raise ValueError('invalid GitHub organization in allowlist')
        orgs = frozenset(raw_orgs)
        return cls(repos, orgs)

    def authorize(self, value: str) -> str:
        repository = normalize_repository(value)
        if repository not in self.repositories and repository.split('/', 1)[0] not in self.organizations:
            raise PermissionError('repository_not_allowed')
        return repository


def task_branch(slug: str, task_id: str) -> str:
    safe = re.sub(r'[^a-z0-9]+', '-', slug.lower()).strip('-')[:40] or 'task'
    short_id = re.sub(r'[^a-f0-9]', '', task_id.lower())[:8]
    if len(short_id) < 6:
        raise ValueError('task ID cannot produce a safe branch suffix')
    return f'agent/{safe}-{short_id}'


def contained_path(root: Path, *parts: str) -> Path:
    root = root.resolve()
    candidate = root.joinpath(*parts).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError('worktree path escapes configured root')
    return candidate
