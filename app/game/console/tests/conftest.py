"""Shared fixtures for the console test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from game.console.app import create_app
from game.console.config import ConsoleSettings
from game.console.tests.fake_world_admin import FakeWorldAdmin, build_transport
from game.console.world_client import WorldAdminClient

TEST_TOKEN = "test-operator-token"


@pytest.fixture
def fake_world() -> FakeWorldAdmin:
    return FakeWorldAdmin(expected_token=TEST_TOKEN)


@pytest.fixture
def world_client(fake_world: FakeWorldAdmin) -> WorldAdminClient:
    return WorldAdminClient(
        base_url="http://world.test",
        operator_token=TEST_TOKEN,
        transport=build_transport(fake_world),
    )


@pytest.fixture
def console_app(fake_world: FakeWorldAdmin, world_client: WorldAdminClient):
    settings = ConsoleSettings(operator_token=TEST_TOKEN, world_base_url="http://world.test")
    return create_app(settings=settings, world_client=world_client)


@pytest.fixture
def client(console_app) -> Iterator[TestClient]:
    with TestClient(console_app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}
