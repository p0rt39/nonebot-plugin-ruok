"""Tests for chat command image rendering presentation helpers."""

from nonebug import App


def test_chat_status_template_contains_fields_and_escapes_html(app: App) -> None:
    """Status image template should include labels while escaping values."""
    from nonebot_plugin_ruok.chat_render import ChatStatusItem
    from nonebot_plugin_ruok.webui.jinja import render_to_string

    html = render_to_string(
        "chat_status.html.jinja2",
        items=[
            ChatStatusItem(
                name="<script>alert(1)</script>",
                status="degraded",
                label="降级",
                icon="🟡",
                reasons=["reason <b>bold</b>"],
            )
        ],
    )

    assert "RUOK 模块状态" in html
    assert "降级" in html
    assert "status-degraded" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "reason &lt;b&gt;bold&lt;/b&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_chat_session_list_template_contains_fields_and_escapes_html(app: App) -> None:
    """Session list image template should include labels while escaping values."""
    from nonebot_plugin_ruok.chat_render import ChatSessionItem
    from nonebot_plugin_ruok.webui.jinja import render_to_string

    html = render_to_string(
        "chat_session_list.html.jinja2",
        title="<script>title</script>",
        total_count=3,
        hidden_count=2,
        items=[
            ChatSessionItem(
                session_id="ruok-12345678",
                module_name="<img src=x onerror=alert(1)>",
                status="unsolved",
                label="未解决",
                icon="🔴",
                time_text="12:00",
                description="desc <script>alert(1)</script>",
            )
        ],
    )

    assert "&lt;script&gt;title&lt;/script&gt;" in html
    assert "ruok-12345678" in html
    assert "未解决" in html
    assert "status-unsolved" in html
    assert "另有 2 个未显示" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "desc &lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>title</script>" not in html
    assert "<img src=x onerror=alert(1)>" not in html
