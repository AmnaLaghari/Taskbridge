"""Provider registry.

Adding a provider: write the module, add one entry here. Nothing else changes.
"""

from __future__ import annotations

from .base import Provider, ProviderMapper
from .github import GitHubMapper
from .todoist import TodoistMapper

MAPPERS: dict[str, ProviderMapper] = {
    GitHubMapper.name: GitHubMapper(),
    TodoistMapper.name: TodoistMapper(),
}


def mapper_for(provider: str) -> ProviderMapper:
    try:
        return MAPPERS[provider]
    except KeyError:
        raise LookupError(f"unknown provider: {provider}") from None


__all__ = ["MAPPERS", "GitHubMapper", "Provider", "ProviderMapper", "TodoistMapper", "mapper_for"]
