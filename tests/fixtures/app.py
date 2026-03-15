from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.application import Application


@dataclass
class FakeLoop:
    coroutines: list[Any] = field(default_factory=list)
    shutdown_calls: list[Any] = field(default_factory=list)
    closed: bool = False

    def create_task(self, coro: Any) -> object:
        self.coroutines.append(coro)
        return object()

    def is_closed(self) -> bool:
        return self.closed

    def run_until_complete(self, coro: Any) -> None:
        self.shutdown_calls.append(coro)


@dataclass
class FakeTaskManager:
    shutdown_called: bool = False

    async def ensure_tasks_running(self) -> None:
        return None

    async def shutdown(self) -> None:
        self.shutdown_called = True


@dataclass
class FakeAsyncCloser:
    closed: bool = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def app_shell() -> Application:
    return Application.__new__(Application)


@pytest.fixture
def fake_loop() -> FakeLoop:
    return FakeLoop()


@pytest.fixture
def fake_task_manager() -> FakeTaskManager:
    return FakeTaskManager()


@pytest.fixture
def fake_async_closer() -> FakeAsyncCloser:
    return FakeAsyncCloser()


@pytest.fixture
def bot_with_loop(fake_loop: FakeLoop) -> SimpleNamespace:
    return SimpleNamespace(_event_loop=fake_loop)
