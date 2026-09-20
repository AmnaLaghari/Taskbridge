import pytest
from django.test import Client


@pytest.mark.django_db
def test_health_reports_ok_when_dependencies_are_up(client: Client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "redis": "ok"}
