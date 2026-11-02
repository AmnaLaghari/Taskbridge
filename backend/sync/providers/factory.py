"""Builds live providers from configuration."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from ..models import Connection, ProviderName
from .base import ChangeSource, TaskWriter
from .github import GitHubProvider
from .todoist import TodoistProvider


def build_change_source(connection: Connection) -> ChangeSource:
    if connection.provider == ProviderName.GITHUB:
        repo = connection.config.get("repo") or settings.GITHUB_REPO
        if not repo:
            raise ImproperlyConfigured("no repository configured for this GitHub connection")
        if not settings.GITHUB_TOKEN:
            raise ImproperlyConfigured("GITHUB_TOKEN is not set")
        return GitHubProvider(
            repo=repo, token=settings.GITHUB_TOKEN, base_url=settings.GITHUB_API_URL
        )
    raise LookupError(f"no change source for provider: {connection.provider}")


def build_writer(provider: str) -> TaskWriter:
    if provider == ProviderName.TODOIST:
        if not settings.TODOIST_API_TOKEN:
            raise ImproperlyConfigured("TODOIST_API_TOKEN is not set")
        return TodoistProvider(
            token=settings.TODOIST_API_TOKEN,
            project_id=settings.TODOIST_PROJECT_ID,
            base_url=settings.TODOIST_API_URL,
        )
    raise LookupError(f"no writer for provider: {provider}")


def build_writers() -> dict[str, TaskWriter]:
    """Writers for every configured push target.

    A target whose credentials are missing is left out rather than raising: the rest
    of the sync should keep working, and its operations wait in the outbox.
    """
    writers: dict[str, TaskWriter] = {}
    for provider in settings.PUSH_TARGETS:
        try:
            writers[provider] = build_writer(provider)
        except (ImproperlyConfigured, LookupError):
            continue
    return writers
