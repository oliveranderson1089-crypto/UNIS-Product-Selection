"""
"名录管理" tab — import / inspect / rematch.

Two import paths:
  1. Pick an existing PDF from data/References/  ← preferred
  2. Upload a new PDF (will be auto-copied to data/References/ on success)

Either way, the PDF ends up in References/ so the 参考文件管理 tab can
show it as "✅ 已导入".
"""

from __future__ import annotations

import shutil
from pathlib import Path

import gradio as gr

from .helpers import (
    _references_dir,
    import_catalog_via_ui,
    list_catalog_names,
    list_catalogs_summary,
    list_reference_pdfs,
    reference_pdf_path,
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
                "**两种来源任选其一**:从 `data/References/` 选已有 PDF "
                "(推荐,留底审计),或直接上传新 PDF(上传后会自动落地到 References/)。\n\n"
                "默认走 **Claude vision**(已配 ANTHROPIC_API_KEY 时);"
                "选 `ocr` 走本地 RapidOCR(需先装 MS VC++ Redistributable)。"
            )
            with gr.Row():
                refs_pdf_pick = gr.Dropdown(
                    label="🅰️ 从 References/ 选(优先)",
                    choices=list_reference_pdfs(),
                    value=None,
                    interactive=True,
                )
                refresh_refs_btn = gr.Button("🔄", size="sm")
            pdf_in = gr.File(
                label="🅱️ 或上传新 PDF(会自动复制到 data/References/)",
                file_types=[".pdf"], type="filepath",
            )
            with gr.Row():
                name_in = gr.Textbox(
                    label="名录名称", placeholder="如 2025-V1-名录",
                )
                extractor_in = gr.Dropdown(
                    label="抽取器",
                    choices=["auto", "claude", "ocr"],
                    value="auto",
                    info="auto = 有 Claude key 用 Claude,否则 OCR",
                )
            notes_in = gr.Textbox(label="备注 (可选)", placeholder="例如:首次导入 / 2025 Q2 更新")
            import_btn = gr.Button("📥 导入", variant="primary")
            import_result = gr.Markdown()

            def _on_import(refs_pick, uploaded, name, ext, notes):
                """
                Resolve PDF source. Priority: References-pick > uploaded.
                If the user uploaded a file, copy it into References/ on
                success so it shows up next time.
                """
                # ---- pick source PDF -------------------------------------
                pdf_path: str | None = None
                copied_into_refs: str | None = None
                if refs_pick:
                    p = reference_pdf_path(refs_pick)
                    if p is None:
                        return (
                            f"❌ References 里找不到 `{refs_pick}` —"
                            f" 刷新一下下拉?",
                            list_catalogs_summary(),
                            gr.update(choices=list_reference_pdfs()),
                        )
                    pdf_path = str(p)
                elif uploaded:
                    pdf_path = uploaded
                else:
                    return (
                        "❌ 请在🅰️ 或 🅱️ 二者中提供 PDF。",
                        list_catalogs_summary(),
                        gr.update(choices=list_reference_pdfs()),
                    )

                # ---- run import ------------------------------------------
                md = import_catalog_via_ui(pdf_path, name, ext, notes)

                # ---- if uploaded path used + import succeeded, copy into
                #      References/ so the file is now tracked there.
                if uploaded and not refs_pick and md.startswith("✅"):
                    refs_dir = _references_dir()
                    refs_dir.mkdir(parents=True, exist_ok=True)
                    src = Path(uploaded)
                    dest = refs_dir / src.name
                    try:
                        if dest.resolve() != src.resolve():
                            shutil.copy(src, dest)
                        copied_into_refs = dest.name
                    except Exception as exc:                          # noqa: BLE001
                        md += f"\n\n⚠️ 入库成功,但复制到 References/ 失败: `{exc}`"

                if copied_into_refs:
                    md += f"\n\n📂 同时已存档到 `data/References/{copied_into_refs}`"

                return (
                    md,
                    list_catalogs_summary(),
                    gr.update(choices=list_reference_pdfs()),
                )

            import_btn.click(
                fn=_on_import,
                inputs=[refs_pdf_pick, pdf_in, name_in, extractor_in, notes_in],
                outputs=[import_result, summary_md, refs_pdf_pick],
            )
            refresh_refs_btn.click(
                fn=lambda: gr.update(choices=list_reference_pdfs()),
                outputs=refs_pdf_pick,
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
