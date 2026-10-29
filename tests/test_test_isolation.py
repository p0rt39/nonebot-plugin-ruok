"""Regression test: the suite must not read or write the repository's runtime data."""

from __future__ import annotations

from pathlib import Path

import pytest
from nonebug import App


def _plugin_data_dir_is_outside_repository_data() -> tuple[bool, Path]:
    import nonebot_plugin_ruok

    repository_data_dir = (Path(__file__).resolve().parents[1] / "data").resolve()
    plugin_data_dir = Path(nonebot_plugin_ruok.data_dir).resolve()
    return not plugin_data_dir.is_relative_to(repository_data_dir), plugin_data_dir


async def test_plugin_data_dir_is_isolated_from_repository_data(app: App) -> None:
    """The plugin data directory must live outside the repository ``data/`` tree."""
    outside_repository, plugin_data_dir = _plugin_data_dir_is_outside_repository_data()

    assert outside_repository
    assert plugin_data_dir.name.startswith("ruok-test-data-")


# ``_connection_history`` is process-global, so an entry left by one test stays
# visible to the next.  ``plugin_test.py`` creates real bots, and each leftover
# connection was reported as an extra disconnected connection on the dashboard,
# which made the hero assertion depend on test order.  The conftest fixture
# clears the dict around every test; the module-scoped fixture below plants an
# entry the way ``plugin_test.py`` does, and the test then checks it is gone.
# A module-scoped fixture is set up before the function-scoped autouse one, so
# this holds on whichever xdist worker runs the module.
_STALE_BOT_ID = "isolation-probe-bot"


@pytest.fixture(scope="module", autouse=True)
def _plant_stale_connection_history() -> None:
    """Leave a bot connection behind the way ``plugin_test.py`` does."""
    from datetime import datetime, timezone

    from nonebot_plugin_ruok.protocol import BotConnectionStatus
    from nonebot_plugin_ruok.collectors import metrics

    metrics._connection_history[_STALE_BOT_ID] = BotConnectionStatus(
        self_id=_STALE_BOT_ID,
        adapter="probe",
        connected=True,
        connected_at=datetime.now(timezone.utc),
    )


def test_connection_history_is_cleared_before_each_test() -> None:
    """The autouse conftest fixture must drop connections left by other tests."""
    from nonebot_plugin_ruok.collectors import metrics

    assert _STALE_BOT_ID not in metrics._connection_history
