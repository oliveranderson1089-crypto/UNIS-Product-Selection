"""
Subcommand: `ui` — launch the Gradio Web UI.

    python -m src.cli ui                         # http://127.0.0.1:7860
    python -m src.cli ui --port 8080
    python -m src.cli ui --host 0.0.0.0          # let LAN reach it
    python -m src.cli ui --share                 # public temporary URL
"""

from __future__ import annotations

import click

from ._common import setup_logging


@click.command(name="ui", help="启动 Web UI(Gradio,三个选型入口 + 名录管理)。")
@click.option("--host", default="127.0.0.1", help="监听地址")
@click.option("--port", type=int, default=7860, help="监听端口")
@click.option("--share", is_flag=True, help="生成临时公开 URL(Gradio 隧道)")
@click.option("--open-browser/--no-open-browser", default=True,
              help="启动后自动打开默认浏览器(默认开启)")
@click.option("--auth", "auth_entries", multiple=True, metavar="USER:PASSWORD",
              help="加密码访问。格式: 用户名:密码。可多次,如 "
                   "--auth alice:pw1 --auth bob:pw2")
def cmd(host: str, port: int, share: bool, open_browser: bool,
        auth_entries: tuple[str, ...]) -> None:
    setup_logging()
    from ..ui import launch

    # Parse "user:password" pairs into the list-of-tuples form Gradio wants.
    # Silently keep only well-formed entries — a typo (missing colon) would
    # otherwise be passed to Gradio as a malformed creds object.
    auth: list[tuple[str, str]] = []
    for entry in auth_entries:
        if ":" not in entry:
            click.echo(f"⚠ --auth 条目 {entry!r} 缺少 ':' 分隔符,已忽略")
            continue
        user, _, pwd = entry.partition(":")
        user, pwd = user.strip(), pwd.strip()
        if not user or not pwd:
            click.echo(f"⚠ --auth 条目 {entry!r} 用户名或密码为空,已忽略")
            continue
        auth.append((user, pwd))

    launch(
        host=host, port=port, share=share,
        open_browser=open_browser,
        auth=auth or None,
    )


if __name__ == "__main__":
    cmd()
