"""
"名录管理" tab — import / inspect / rematch.
"""

from __future__ import annotations

import gradio as gr

from .helpers import (
    import_catalog_via_ui,
    list_catalog_names,
    list_catalogs_summary,
    rematch_all_ui,
    show_catalog_md,
)


def build_catalog_tab():
    with gr.Tab("🏷️ 名录管理"):
        gr.Markdown(
            "## 名录(政府采购清单 / 创新名录)管理\n\n"
            "名录是一份外部权威清单(通常是 PDF 承诺函),里面的产品是你库内"
            "产品的子集。导入后,可在「名录型选型」标签里用它做范围限制。"
        )

        # Summary at the top -----------------------------------------------
        with gr.Row():
            summary_md = gr.Markdown(value=list_catalogs_summary(), label="已注册的名录")
            refresh_summary_btn = gr.Button("🔄 刷新列表", size="sm")
        refresh_summary_btn.click(fn=list_catalogs_summary, outputs=summary_md)

        # Import box -------------------------------------------------------
        with gr.Accordion("➕ 导入新名录", open=True):
            gr.Markdown(
                "支持扫描型 PDF。默认走 **Claude vision**(已配 ANTHROPIC_API_KEY 时);"
                "选 `ocr` 走本地 RapidOCR(需先装 MS VC++ Redistributable)。"
            )
            with gr.Row():
                pdf_in = gr.File(
                    label="名录文件(PDF)",
                    file_types=[".pdf"], type="filepath",
                )
                name_in = gr.Textbox(
                    label="名录名称", placeholder="如 2025-V1-名录",
                )
            with gr.Row():
                extractor_in = gr.Dropdown(
                    label="抽取器",
                    choices=["auto", "claude", "ocr"],
                    value="auto",
                    info="auto = 有 Claude key 用 Claude,否则 OCR",
                )
                notes_in = gr.Textbox(label="备注 (可选)", placeholder="例如:首次导入 / 2025 Q2 更新")
            import_btn = gr.Button("📥 导入", variant="primary")
            import_result = gr.Markdown()

            def _on_import(pdf, name, ext, notes):
                md = import_catalog_via_ui(pdf, name, ext, notes)
                # Refresh the summary table too
                return md, list_catalogs_summary()

            import_btn.click(
                fn=_on_import,
                inputs=[pdf_in, name_in, extractor_in, notes_in],
                outputs=[import_result, summary_md],
            )

        # View one catalog --------------------------------------------------
        with gr.Accordion("🔍 查看名录详情", open=False):
            with gr.Row():
                view_dropdown = gr.Dropdown(
                    label="选择名录",
                    choices=list_catalog_names(),
                    value=None,
                )
                refresh_view_btn = gr.Button("🔄", size="sm")
            view_md = gr.Markdown(value="_选择一份名录查看详情。_")

            view_dropdown.change(fn=show_catalog_md, inputs=view_dropdown, outputs=view_md)
            refresh_view_btn.click(
                fn=lambda: gr.update(choices=list_catalog_names()),
                outputs=view_dropdown,
            )

        # Rematch all -------------------------------------------------------
        with gr.Accordion("🔁 重新匹配", open=False):
            gr.Markdown(
                "**用途**:抓取新产品后(`crawl` 子命令),重新跑型号匹配,"
                "把之前未匹配的名录条目自动连到新出现的产品上。"
            )
            rematch_btn = gr.Button("🔁 一键重新匹配所有名录")
            rematch_md = gr.Markdown()
            rematch_btn.click(fn=rematch_all_ui, outputs=rematch_md)


__all__ = ["build_catalog_tab"]
