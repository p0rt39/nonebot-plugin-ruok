import os
import json
import atexit
import shutil
import tempfile
from pathlib import Path

import pytest
import nonebot
from pytest_asyncio import is_async_test
from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

if Path(".env.dev").exists():
    os.environ["ENVIRONMENT"] = "dev"
else:
    os.environ["ENVIRONMENT"] = "test"

# Point the plugin data directory at a throwaway location before NoneBot loads
# the plugin.  ``.env.test`` sets ``LOCALSTORE_USE_CWD=true``, which otherwise
# resolves to the repository's ``data/`` directory and lets local runtime state
# (leftover automatic Sessions, module status) decide test outcomes.
_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="ruok-test-data-"))
atexit.register(shutil.rmtree, _TEST_DATA_DIR, True)
os.environ["LOCALSTORE_PLUGIN_DATA_DIR"] = json.dumps(
    {"nonebot_plugin_ruok": str(_TEST_DATA_DIR)}
)


def pytest_collection_modifyitems(items: list[pytest.Item]):
    pytest_asyncio_tests = (item for item in items if is_async_test(item))
    session_scope_marker = pytest.mark.asyncio(loop_scope="session")
    for async_test in pytest_asyncio_tests:
        async_test.add_marker(session_scope_marker, append=False)


@pytest.fixture(scope="session", autouse=True)
async def after_nonebot_init(after_nonebot_init: None):
    # 加载适配器
    driver = nonebot.get_driver()
    driver.register_adapter(OnebotV11Adapter)

    # 加载插件
    nonebot.load_from_toml("pyproject.toml")
