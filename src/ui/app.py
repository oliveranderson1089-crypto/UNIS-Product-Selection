"""
Main Gradio app — tabs assembled from `select_page` and `catalog_page`.

Three selection entry points (per spec):
  - 创新型:Autonomous_Controllable products (section=innovation)
  - 通用型:Commercial_Product products    (section=general)
  - 名录型:scoped by a named CatalogList   (catalog=...)

Plus a 名录管理 tab and a projects placeholder.

Launch:
    python -m src.cli ui          # opens http://127.0.0.1:7860
"""

from __future__ import annotations

import gradio as gr

from .catalog_page import build_catalog_tab
from .projects_page import build_projects_tab
from .quote_page import build_quote_tab
from .select_page import build_select_tab


def build_app() -> gr.Blocks:
    """Construct the Gradio Blocks object. Caller calls `.launch()`."""
    # NOTE: Gradio 6 moved `theme` and `css` from Blocks() to launch().
    with gr.Blocks(title="UNIS 产品选型") as app:
        gr.Markdown(
            "# 🎯 UNIS 产品选型工具\n"
            "三类选型入口共享同一引擎,只是过滤范围不同。详见 "
            "[ARCHITECTURE.md](https://github.com/oliveranderson1089-crypto/"
            "UNIS-Product-Selection/blob/main/ARCHITECTURE.md)。"
        )

        # ---- 创新型 -----------------------------------------------------
        innovation_refresh = build_select_tab(
            tab_label="🚀 创新型选型",
            section="innovation",
            show_catalog_picker=False,
            intro_md=(
                "**创新型** = `Autonomous_Controllable` 系列(全部 100% 国产化)。"
                "包含 5 个品类:交换机、路由器、安全、计算存储、大模型一体机。"
            ),
        )

        # ---- 通用型 -----------------------------------------------------
        general_refresh = build_select_tab(
            tab_label="📦 通用型选型",
            section="general",
            show_catalog_picker=False,
            intro_md=(
                "**通用型** = `Commercial_Product` 系列(行业通用,可能含 OEM)。"
                "包含 8 个品类:交换机、路由器、安全、计算存储、智能管理、"
                "云计算、大数据、无线局域网。"
            ),
        )

        # ---- 名录型 -----------------------------------------------------
        catalog_refresh = build_select_tab(
            tab_label="🏷️ 名录型选型",
            section=None,           # no section filter (use catalog instead)
            show_catalog_picker=True,
            intro_md=(
                "**名录型** = 限定到某一份外部名录(政府采购清单等)内的产品。"
                "在右上角下拉里选名录;先去「名录管理」标签导入一份。"
            ),
        )

        # ---- 名录管理 ---------------------------------------------------
        build_catalog_tab()

        # ---- 项目管理 ---------------------------------------------------
        build_projects_tab()

        # ---- 报价单编辑 -------------------------------------------------
        build_quote_tab()

    return app


def launch(
    *,
    host: str = "127.0.0.1",
    port: int = 7860,
    share: bool = False,
    open_browser: bool = True,
    auth: list[tuple[str, str]] | None = None,
) -> None:
    """Build + serve. Blocks until Ctrl-C.

    `auth` is a list of (username, password) tuples — Gradio's native
    format. When set, every visitor sees a browser-native login prompt
    before the UI loads. Strongly recommended when combined with `share=True`
    since the public URL would otherwise be open to anyone who guesses it.
    """
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if auth:
        logging.getLogger(__name__).info(
            "Auth enabled for %d user(s): %s", len(auth),
            ", ".join(u for u, _ in auth),
        )
    app = build_app()
    app.launch(
        server_name=host,
        server_port=port,
        share=share,
        show_error=True,
        # Auto-pop the default browser to the served URL once the server
        # is ready. Gradio handles the timing itself — no need to sleep
        # in a .bat wrapper.
        inbrowser=open_browser,
        auth=auth,
        theme=gr.themes.Soft(primary_hue="purple"),
        css=".container { max-width: 1200px; margin: auto; }",
    )


if __name__ == "__main__":
    launch()
