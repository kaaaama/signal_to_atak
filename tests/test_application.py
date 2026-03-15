import pytest

from app.services.application import Application


@pytest.mark.asyncio
async def test_application_shutdown_closes_resources(
    app_shell: Application,
    fake_task_manager,
    fake_async_closer,
) -> None:
    app_shell.task_manager = fake_task_manager
    app_shell.tak_delivery = fake_async_closer
    app_shell.tak_client = type(fake_async_closer)()
    app_shell.pg = type(fake_async_closer)()

    await app_shell.shutdown()

    assert app_shell.task_manager.shutdown_called is True
    assert app_shell.tak_delivery.closed is True
    assert app_shell.tak_client.closed is True
    assert app_shell.pg.closed is True


def test_schedule_startup_tasks_uses_bot_event_loop(
    app_shell: Application,
    fake_task_manager,
    bot_with_loop,
    fake_loop,
) -> None:
    app_shell.task_manager = fake_task_manager
    app_shell.bot = bot_with_loop

    app_shell.schedule_startup_tasks()

    assert len(fake_loop.coroutines) == 1
    fake_loop.coroutines[0].close()


def test_shutdown_after_run_uses_bot_loop(
    app_shell: Application,
    bot_with_loop,
    fake_loop,
) -> None:
    app_shell.bot = bot_with_loop
    app_shell.shutdown = lambda: "shutdown-coro"

    app_shell._shutdown_after_run()

    assert fake_loop.shutdown_calls == ["shutdown-coro"]
