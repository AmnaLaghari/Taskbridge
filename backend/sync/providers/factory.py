"""Builds a live provider from a stored connection."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from ..models import Connection, ProviderName
from .base import ChangeSource
from .github import GitHubProvider


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
