from __future__ import annotations

from pathlib import Path

from webui_test_utils import (
    _client,
    _login_admin,
    _module_names,
    _webui_config,
    _upsert_module,
)


def test_modules_page_links_to_details_without_inline_actions(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/modules")

    assert response.status_code == 200
    assert 'id="module-form-panel"' in response.text
    assert 'id="module-list"' in response.text
    assert 'hx-target="closest tr"' not in response.text
    assert "module-edit-form?" not in response.text
    assert 'hx-post="/ruok/_actions/module-delete"' not in response.text


def test_module_detail_contains_edit_and_delete_actions(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    _upsert_module(
        tmp_path,
        ModuleDefinition(
            name="demo",
            display_name="Demo",
            plugins=["nonebot_plugin_ruok"],
        ),
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.get("/ruok/modules/demo")

    assert response.status_code == 200
    assert 'name="original_name"' in response.text
    assert 'name="return_to_detail"' in response.text
    assert 'hx-post="/ruok/_actions/module-delete"' in response.text
    assert 'class="ruok-picker"' in response.text
    assert 'data-field-name="selected_plugins"' in response.text
    assert 'name="selected_plugins" value="nonebot_plugin_ruok"' in response.text
    assert 'name="enabled"' not in response.text
    assert "← 返回模块列表" in response.text
    assert response.text.count("返回模块列表") == 1
    assert "取消" in response.text
    assert "module-edit-form?" in response.text
    assert 'hx-target="closest details"' in response.text
    assert 'class="ruok-form-actions"' in response.text
    assert "Back to Modules" not in response.text
    assert 'class="secondary outline ruok-button-danger"' in response.text


def test_module_edit_form_loads_special_character_name(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    module_name = "mod with/slash"
    _upsert_module(
        tmp_path,
        ModuleDefinition(
            name=module_name,
            display_name="特殊模块",
            plugins=["plugin_a"],
            description="desc",
        ),
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.get(
        "/ruok/_actions/module-edit-form",
        params={"name": module_name},
    )

    assert response.status_code == 200
    assert 'name="original_name"' in response.text
    assert f'value="{module_name}"' in response.text
    assert "plugin_a" in response.text


def test_module_edit_renames_without_duplicate(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    _upsert_module(tmp_path, ModuleDefinition(name="old/name", display_name="Old"))
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/module-upsert",
        data={
            "original_name": "old/name",
            "name": "new name",
            "display_name": "New",
            "description": "updated",
            "plugins": "plugin_a,plugin_b",
        },
    )

    names = _module_names(config, tmp_path)
    assert response.status_code == 200
    assert "old/name" not in names
    assert "new name" in names
    assert response.headers["HX-Trigger"]


def test_module_detail_edit_redirects_and_accepts_multiple_plugins(
    tmp_path: Path,
) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    _upsert_module(tmp_path, ModuleDefinition(name="old/name", display_name="Old"))
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/module-upsert",
        data={
            "original_name": "old/name",
            "name": "new name",
            "display_name": "New",
            "description": "updated",
            "selected_plugins": ["plugin_a", "plugin_b"],
            "plugins": "plugin_c\nplugin_a",
            "return_to_detail": "true",
        },
    )

    from nonebot_plugin_ruok.collectors.modules import get_module

    module = get_module(tmp_path, config, "new name")
    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/ruok/modules/new%20name"
    assert module is not None
    assert module.plugins == ["plugin_a", "plugin_b", "plugin_c"]


def test_module_rename_conflict_returns_400(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    _upsert_module(tmp_path, ModuleDefinition(name="first", display_name="First"))
    _upsert_module(tmp_path, ModuleDefinition(name="second", display_name="Second"))
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/module-upsert",
        data={
            "original_name": "first",
            "name": "second",
            "display_name": "First",
        },
    )

    names = _module_names(config, tmp_path)
    assert response.status_code == 400
    assert "first" in names
    assert "second" in names


def test_module_delete_accepts_special_character_name(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    module_name = "mod with/slash"
    _upsert_module(tmp_path, ModuleDefinition(name=module_name))
    _upsert_module(tmp_path, ModuleDefinition(name="keep"))
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.post(
        "/ruok/_actions/module-delete",
        data={"name": module_name},
    )

    names = _module_names(config, tmp_path)
    assert response.status_code == 200
    assert module_name not in names
    assert "keep" in names


def test_module_delete_rejects_external_redirect_target(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    _upsert_module(tmp_path, ModuleDefinition(name="victim"))
    client = _client(config, tmp_path)
    _login_admin(client)

    for hostile in (
        "https://evil.example/x",
        "//evil.example",
        "/ruok/../evil",
        "javascript:alert(1)",
    ):
        response = client.post(
            "/ruok/_actions/module-delete",
            data={"name": "victim", "redirect_to": hostile},
        )
        assert response.status_code == 200
        assert response.headers["HX-Redirect"] == "/ruok/modules"
        _upsert_module(tmp_path, ModuleDefinition(name="victim"))


def test_module_delete_without_redirect_target_uses_default(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    _upsert_module(tmp_path, ModuleDefinition(name="victim"))
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.post("/ruok/_actions/module-delete", data={"name": "victim"})

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/ruok/modules"


def test_module_table_provides_mobile_card_labels(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    _upsert_module(
        tmp_path,
        ModuleDefinition(
            name="cardmod",
            display_name="卡片模块",
            plugins=["plugin_a"],
            description="很长的一段描述文本，用于检查移动端卡片布局不会裁剪内容",
        ),
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.get("/ruok/modules")

    assert response.status_code == 200
    assert 'class="table-scroll status-table-scroll"' in response.text
    assert 'class="striped status-table"' in response.text
    # Every body cell carries the label the stacked card layout renders.
    assert response.text.count('data-label="状态"') == response.text.count("<tr>") - 1
    for label in ("状态", "模块", "内部名", "插件数", "描述"):
        assert f'data-label="{label}"' in response.text
    # The table keeps real table semantics for wide viewports.
    assert "<thead>" in response.text
    assert "<tbody>" in response.text
    assert "cardmod" in response.text


def test_module_table_wraps_rows_in_scroll_container(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ModuleDefinition

    config = _webui_config()
    _upsert_module(tmp_path, ModuleDefinition(name="scrollmod"))
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.get("/ruok/_partials/modules")

    assert response.status_code == 200
    assert 'id="module-list"' in response.text
    assert "status-table-scroll" in response.text
    assert "module-table" not in response.text


def test_module_form_has_inline_status_region(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/modules")

    assert response.status_code == 200
    assert "data-form-status" in response.text
    assert 'id="module-edit-result" class="ruok-form-status"' in response.text
    assert 'aria-live="polite"' in response.text


def test_shared_page_script_describes_validation_errors(tmp_path: Path) -> None:
    client = _client(_webui_config(), tmp_path)
    _login_admin(client)

    response = client.get("/ruok/modules")

    assert response.status_code == 200
    # FastAPI's 422 payload must be turned into a readable message, and an
    # inline region must suppress the duplicate error toast.
    assert "describeValidationError" in response.text
    assert "请填写：" in response.text
    assert "window.ruokErrorMessage = messageFromXhr" in response.text
    assert "form.querySelector('[data-form-status]')" in response.text
    # Busy state stays announced on the submitting control.
    assert "aria-busy" in response.text
    assert "正在提交…" in response.text


def test_session_rows_keep_the_compact_grid(tmp_path: Path) -> None:
    from nonebot_plugin_ruok.protocol import ReporterInfo
    from nonebot_plugin_ruok.collector import create_session

    config = _webui_config()
    create_session(
        tmp_path,
        module_name="ruok",
        description="row layout check",
        reporter=ReporterInfo(type="user", user_id="1", platform="webui"),
    )
    client = _client(config, tmp_path)
    _login_admin(client)

    response = client.get("/ruok/sessions")

    assert response.status_code == 200
    # Row-level action forms must not reserve space for a status region; the
    # shared script skips them so the compact grid is preserved.
    assert "form.closest('.session-row')" in client.get("/ruok/modules").text
    assert ".session-row [data-form-status]" not in response.text
