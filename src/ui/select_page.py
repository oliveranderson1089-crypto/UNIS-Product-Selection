"""
Reusable factory that builds one "selection" tab in the Gradio app.

The three tabs (创新型 / 通用型 / 名录型) all share the same input UI and
result rendering — they only differ in scope:

  创新型 → section="innovation"
  通用型 → section="general"
  名录型 → catalog=<dropdown picked by user>

So we expose one builder and call it three times from app.py.
"""

from __future__ import annotations

from typing import Callable

import gradio as gr

from .helpers import list_catalog_names, run_selection


def build_select_tab(
    *,
    tab_label: str,
    section: str | None,
    show_catalog_picker: bool,
    intro_md: str,
) -> Callable[[], None]:
    """
    Build a single `gr.Tab` and return a refresher callable for catalogs.

    The refresher is only meaningful when `show_catalog_picker=True`;
    app.py wires it to a "刷新名录" button so freshly imported catalogs
    appear in the dropdown without having to restart the UI.
    """

    with gr.Tab(tab_label):
        gr.Markdown(intro_md)

        # Input row -------------------------------------------------------
        text_in = gr.Textbox(
            label="📝 需求文本(自然语言)",
            placeholder="例如:48口万兆三层核心交换机,自主可控,冗余电源",
            lines=3,
        )
        with gr.Row():
            doc_in = gr.File(
                label="📄 文档(PDF / Word / Excel / TXT / CSV / MD)",
                file_types=[".pdf", ".docx", ".xlsx", ".xls", ".txt", ".md", ".csv"],
                type="filepath",
            )
            image_in = gr.File(
                label="🖼️ 图片(需开启 AI 模式)",
                file_types=["image"],
                type="filepath",
            )

        # Controls --------------------------------------------------------
        catalog_dropdown = None
        if show_catalog_picker:
            with gr.Row():
                catalog_dropdown = gr.Dropdown(
                    label="🏷️ 名录(必选)",
                    choices=list_catalog_names(),
                    value=None,
                    info="只在该名录范围内挑选产品",
                )
                refresh_btn = gr.Button("🔄 刷新名录", size="sm")

                def _refresh():
                    return gr.update(choices=list_catalog_names())

                refresh_btn.click(fn=_refresh, outputs=catalog_dropdown)

        with gr.Row():
            ai_toggle = gr.Checkbox(label="🤖 AI 模式 (DeepSeek 解析 + 重排,图片走 Claude)",
                                    value=False)
            top_k = gr.Slider(label="返回 Top N", minimum=1, maximum=15, step=1, value=5)
        run_btn = gr.Button("🚀 开始选型", variant="primary", size="lg")

        # Output ----------------------------------------------------------
        with gr.Row():
            req_out = gr.Markdown(label="解析后的需求")
            res_out = gr.Markdown(label="推荐结果")

        # Wiring ---------------------------------------------------------
        inputs = [text_in, doc_in, image_in, ai_toggle, top_k]
        if catalog_dropdown is not None:
            inputs.append(catalog_dropdown)

        def _on_click(text, doc, image, use_ai, k, catalog=None):
            return run_selection(
                text=text,
                document_path=doc,
                image_path=image,
                use_ai=bool(use_ai),
                section=section,
                catalog_name=(catalog or None),
                top_k=int(k),
            )

        run_btn.click(fn=_on_click, inputs=inputs, outputs=[req_out, res_out])

    # Caller may need to refresh catalogs after a new import on another tab.
    def refresher():
        if catalog_dropdown is not None:
            return gr.update(choices=list_catalog_names())
        return gr.update()
    return refresher


__all__ = ["build_select_tab"]
