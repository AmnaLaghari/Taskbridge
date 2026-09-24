"""Run one poll by hand.

Celery Beat does this on a schedule from Day 5; until then this is how a human
triggers a sync and sees what it did.
"""

from __future__ import annotations

from argparse import ArgumentParser
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from sync.engine.polling import poll_connection
from sync.models import Connection, ProviderName
from sync.providers.factory import build_change_source


class Command(BaseCommand):
    help = "Poll one provider once and report what changed."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("provider", choices=[choice.value for choice in ProviderName])
        parser.add_argument(
            "--account",
            help="Account to poll (a repository for GitHub). Defaults to the configured one.",
        )
        parser.add_argument(
            "--reset-cursor",
            action="store_true",
            help="Forget where the last poll got to and read everything again.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        provider: str = options["provider"]
        account: str | None = options["account"]

        connection = self._connection(provider, account)
        if options["reset_cursor"]:
            connection.cursor = None
            connection.save(update_fields=["cursor", "updated_at"])
            self.stdout.write("cursor reset — reading everything")

        try:
            source = build_change_source(connection)
        except ImproperlyConfigured as error:
            raise CommandError(f"{error}. Check your .env file.") from error

        self.stdout.write(f"polling {connection.provider}:{connection.account_label}…")
        result = poll_connection(connection, source)

        self.stdout.write(
            self.style.SUCCESS(
                f"fetched {result.fetched} · created {result.created} · "
                f"updated {result.updated} · unchanged {result.unchanged}"
            )
        )
        if result.fetched == 0:
            self.stdout.write("nothing has changed since the last poll")

    def _connection(self, provider: str, account: str | None) -> Connection:
        from django.conf import settings

        label = account or (settings.GITHUB_REPO if provider == ProviderName.GITHUB else "")
        if not label:
            raise CommandError(
                f"no account configured for {provider}. Pass --account, or set it in .env."
            )

        connection, created = Connection.objects.get_or_create(
            provider=provider,
            account_label=label,
            defaults={"config": {"repo": label}},
        )
        if created:
            self.stdout.write(f"created connection for {provider}:{label}")
        return connection
