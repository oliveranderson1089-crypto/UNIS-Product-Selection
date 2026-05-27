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
def cmd(host: str, port: int, share: bool) -> None:
    setup_logging()
    from ..ui import launch
    launch(host=host, port=port, share=share)


if __name__ == "__main__":
    cmd()
