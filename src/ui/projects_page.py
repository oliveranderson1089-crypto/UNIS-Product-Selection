"""
"项目管理" tab — scan / list / select / edit.

Layout:
  ┌─ Top row: scan button + filters ───────────────────────────┐
  │  [🔄 扫描工作目录]   [人员] [状态] [客户关键字]  [🔁 刷新] │
  ├─ Project list table ────────────────────────────────────────┤
  │  | id | 状态 | 人 | 代码 | 全称 | 客户 | 文件数 | 终版 |    │
  ├─ Detail panel ──────────────────────────────────────────────┤
  │  Select project ▼  [📂 打开文件夹]                          │
  │  Detail markdown + status/customer/notes editors            │
  └─────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import gradio as gr

from ..storage import PROJECT_STATUSES
from .helpers import (
    list_project_choices,
    list_projects_md,
    open_project_ui,
    scan_projects_ui,
    show_project_md,
    update_project_customer_ui,
    update_project_notes_ui,
    update_project_status_ui,
)


def build_projects_tab():
    with gr.Tab("📁 项目管理"):
        gr.Markdown(
            "## 项目管理\n\n"
            "扫描 `D:\\Work\\紫光恒越\\日常工作\\<人名>\\<项目>` 自动建档,"
            "跟踪状态(进行中 / 中标 / 未中标 / 结案),管理每个项目下的文件。"
        )

        # ----- Top row: scan + filters --------------------------------
        with gr.Row():
            scan_btn = gr.Button("🔄 扫描工作目录", variant="primary", size="sm")
            refresh_btn = gr.Button("🔁 刷新列表", size="sm")
        scan_md = gr.Markdown(value="")

        with gr.Row():
            assigner_in = gr.Textbox(label="按下发人过滤", placeholder="如 漆森骅", scale=1)
            status_in = gr.Dropdown(
                label="按状态过滤", choices=[""] + list(PROJECT_STATUSES),
                value="", scale=1,
            )
            customer_in = gr.Textbox(label="客户关键字", placeholder="如 中核", scale=1)

        # ----- Project list -------------------------------------------
        list_md = gr.Markdown(value=list_projects_md(None, None, None))

        # ----- Detail panel -------------------------------------------
        gr.Markdown("---")
        gr.Markdown("### 🔍 项目详情")

        with gr.Row():
            project_picker = gr.Dropdown(
                label="选择项目",
                choices=list_project_choices(),
                value=None,
                scale=4,
            )
            open_folder_btn = gr.Button("📂 打开文件夹", size="sm", scale=1)
        open_result = gr.Markdown(value="")

        detail_md = gr.Markdown(value="_左侧下拉选一个项目。_")

        # ----- Editors (status / customer / notes) --------------------
        with gr.Accordion("✏️ 编辑项目信息", open=False):
            with gr.Row():
                status_select = gr.Dropdown(
                    label="状态",
                    choices=list(PROJECT_STATUSES),
                    value="进行中",
                )
                status_btn = gr.Button("更新状态", size="sm")
            status_result = gr.Markdown(value="")

            with gr.Row():
                customer_edit = gr.Textbox(label="客户名称", placeholder="如 中核环保")
                customer_btn = gr.Button("更新客户", size="sm")
            customer_result = gr.Markdown(value="")

            notes_edit = gr.Textbox(label="备注", lines=3,
                                    placeholder="如 客户特殊要求 / 注意事项 / 联系人")
            notes_btn = gr.Button("保存备注", size="sm")
            notes_result = gr.Markdown(value="")

        # ===== Wiring ====================================================
        def _refresh_all(assigner, status, customer):
            return (
                list_projects_md(assigner, status, customer),
                gr.update(choices=list_project_choices()),
            )

        scan_btn.click(
            fn=lambda a, s, c: (scan_projects_ui(),) + _refresh_all(a, s, c),
            inputs=[assigner_in, status_in, customer_in],
            outputs=[scan_md, list_md, project_picker],
        )
        refresh_btn.click(
            fn=_refresh_all,
            inputs=[assigner_in, status_in, customer_in],
            outputs=[list_md, project_picker],
        )
        for trigger in (assigner_in, status_in, customer_in):
            trigger.change(
                fn=list_projects_md,
                inputs=[assigner_in, status_in, customer_in],
                outputs=list_md,
            )

        project_picker.change(fn=show_project_md, inputs=project_picker, outputs=detail_md)
        open_folder_btn.click(fn=open_project_ui, inputs=project_picker, outputs=open_result)

        # Editor wiring — also refresh detail panel so users see new state.
        def _on_status(pid, new_status):
            return update_project_status_ui(pid, new_status), show_project_md(pid)
        status_btn.click(
            fn=_on_status,
            inputs=[project_picker, status_select],
            outputs=[status_result, detail_md],
        )

        def _on_customer(pid, cust):
            return update_project_customer_ui(pid, cust), show_project_md(pid)
        customer_btn.click(
            fn=_on_customer,
            inputs=[project_picker, customer_edit],
            outputs=[customer_result, detail_md],
        )

        def _on_notes(pid, notes):
            return update_project_notes_ui(pid, notes), show_project_md(pid)
        notes_btn.click(
            fn=_on_notes,
            inputs=[project_picker, notes_edit],
            outputs=[notes_result, detail_md],
        )


__all__ = ["build_projects_tab"]
