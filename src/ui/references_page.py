"""
"参考文件管理" tab — CRUD on data/References/.

This is where the user uploads / inspects / replaces / deletes the
support files the quote rules read at runtime:

  - IT产品BOM编码*.xlsx        — for `swap_oem_service_line` lookup
  - R3800FT20 G3配置模板*.xlsx  — for `fill_r3800ft20_template`
  - 名录承诺函 *.pdf            — for the 名录管理 tab to import

The status panel at the top shows the EXACT file each rule will pick
right now, after applying the glob + "已选型" + mtime preference. This
way users know whether a fresh upload actually became the active one.
"""

from __future__ import annotations

import gradio as gr

from .helpers import (
    delete_reference_ui,
    list_reference_filenames,
    list_references_md,
    references_status_md,
    upload_reference_ui,
)


def build_references_tab():
    with gr.Tab("📂 参考文件管理"):
        gr.Markdown(
            "## `data/References/` 参考文件管理\n\n"
            "管理报价规则引擎在运行时读取的参考文件:\n\n"
            "- **`IT产品BOM编码*.xlsx`** — `swap_oem_service_line` 规则查 7×24 NBD 维保行\n"
            "- **`R3800FT20 G3配置模板*.xlsx`** — `fill_r3800ft20_template` 规则按模板展开 BOM("
            "**带「已选型」字样的文件优先**)\n"
            "- **`*.pdf`** — 给「名录管理」标签导入用\n\n"
            "> 同类文件存在多份时,**按修改时间取最新**;FT20 模板额外优先「已选型」版本。"
            "上面提交报价单后规则会自动用本面板「✅ 生效」的那份文件。"
        )

        # ---- Active-file status (always visible at top) -------------------
        status_md = gr.Markdown(value=references_status_md())

        # ---- Full file listing --------------------------------------------
        with gr.Row():
            list_md = gr.Markdown(value=list_references_md())
        with gr.Row():
            refresh_btn = gr.Button("🔄 刷新列表", size="sm")

        refresh_btn.click(
            fn=lambda: (references_status_md(), list_references_md()),
            outputs=[status_md, list_md],
        )

        # ---- Upload + Delete (components declared first, handlers wired at
        # the end so the upload handler can also refresh the delete dropdown)
        with gr.Accordion("➕ 上传 / 替换文件", open=True):
            gr.Markdown(
                "支持 `.xlsx` / `.xls` / `.pdf`。**同名文件会被覆盖**,所以建议带日期戳:"
                "`IT产品BOM编码20260601.xlsx`、"
                "`R3800FT20 G3配置模板20260601（已选型）.xlsx`。"
            )
            upload_in = gr.File(
                label="📁 选文件(可拖拽)",
                file_types=[".xlsx", ".xls", ".xlsm", ".pdf"],
                type="filepath",
            )
            upload_btn = gr.Button(
                "📥 上传到 data/References/",
                variant="primary",
            )
            upload_result = gr.Markdown()

        with gr.Accordion("🗑️ 删除文件(不可恢复)", open=False):
            gr.Markdown(
                "**⚠️ 不可逆操作**,文件不会进回收站。"
                "如果删掉的是当前生效的 BOM 或模板,对应的格式化规则会跳过。"
            )
            with gr.Row():
                delete_pick = gr.Dropdown(
                    label="选要删的文件",
                    choices=list_reference_filenames(),
                    value=None,
                    interactive=True,
                )
                refresh_pick_btn = gr.Button("🔄", size="sm")
            confirm_delete = gr.Checkbox(
                label="我确认要永久删除这份文件",
                value=False,
            )
            delete_btn = gr.Button("🗑️ 删除", variant="stop")
            delete_result = gr.Markdown()

        # ---- Wire up handlers (after every component exists) ---------------
        def _on_upload(path):
            msg = upload_reference_ui(path)
            return (
                msg,
                references_status_md(),
                list_references_md(),
                gr.update(choices=list_reference_filenames(), value=None),
            )

        upload_btn.click(
            fn=_on_upload,
            inputs=upload_in,
            outputs=[upload_result, status_md, list_md, delete_pick],
        )

        def _on_delete(name, confirmed):
            msg = delete_reference_ui(name, confirmed)
            return (
                msg,
                references_status_md(),
                list_references_md(),
                gr.update(choices=list_reference_filenames(), value=None),
                False,
            )

        delete_btn.click(
            fn=_on_delete,
            inputs=[delete_pick, confirm_delete],
            outputs=[
                delete_result, status_md, list_md,
                delete_pick, confirm_delete,
            ],
        )
        refresh_pick_btn.click(
            fn=lambda: gr.update(choices=list_reference_filenames()),
            outputs=delete_pick,
        )


__all__ = ["build_references_tab"]
