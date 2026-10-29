"""Regression test: the suite must not read or write the repository's runtime data."""

from __future__ import annotations

from pathlib import Path

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
