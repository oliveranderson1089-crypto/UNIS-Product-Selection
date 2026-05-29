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
    show_project_md,
    update_project_customer_ui,
    update_project_notes_ui,
    update_project_status_ui,
)


def build_projects_tab() -> dict:
    """
    Build the 项目管理 tab and return refs the cross-tab refresh wiring
    in app.py needs to update.
    """
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
            # Renamed from "客户关键字" to a single broad search field.
            # The OR query in list_projects matches name / display_name /
            # customer simultaneously, so typing the project code, full
            # name, or customer all work.
            customer_in = gr.Textbox(
                label="🔍 搜索关键字",
                placeholder="项目代码 / 全称 / 客户(任一字段含此关键字即命中)",
                scale=2,
            )

        # ----- Project list (paginated) ------------------------------
        # Initial render: page 1 of all projects.
        _initial_list_md, _initial_page_info, _ = list_projects_md(None, None, None, page=1)
        list_md = gr.Markdown(value=_initial_list_md)
        page_state = gr.State(value=1)
        with gr.Row():
            prev_btn = gr.Button("◀ 上一页", size="sm", scale=1)
            page_info_md = gr.Markdown(value=_initial_page_info)
            next_btn = gr.Button("下一页 ▶", size="sm", scale=1)

        # ----- Detail panel -------------------------------------------
        gr.Markdown("---")
        gr.Markdown("### 🔍 项目详情")

        # Dedicated search box that filters the picker's choices server-side.
        # Gradio's built-in `filterable` typeahead on the dropdown proved
        # undiscoverable/unreliable for the user, so we drive the choices
        # explicitly: typing here narrows the dropdown below to matching
        # projects (OR match on code / full name / customer).
        detail_search = gr.Textbox(
            label="🔍 搜索项目(输入关键字,下方下拉自动过滤)",
            placeholder="项目代码 / 全称 / 客户(任一字段含此关键字即命中)",
        )
        with gr.Row():
            project_picker = gr.Dropdown(
                label="选择项目",
                choices=list_project_choices(),
                value=None,
                scale=4,
                # Keep filterable as a secondary in-dropdown typeahead, but
                # the 🔍 box above is the primary, discoverable search path.
                filterable=True,
                allow_custom_value=False,
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
        # NOTE: scan_btn and refresh_btn are wired in app.py — they need
        # to also update components on the 报价单编辑 tab (the project
        # dropdown there), which only app.py can see.

        # ----- Filter triggers: reset to page 1 and re-render ----------
        def _on_filter_change(a, st, sr):
            list_md_v, page_info_v, _ = list_projects_md(a, st, sr, page=1)
            return list_md_v, page_info_v, 1
        for trigger in (assigner_in, status_in, customer_in):
            trigger.change(
                fn=_on_filter_change,
                inputs=[assigner_in, status_in, customer_in],
                outputs=[list_md, page_info_md, page_state],
            )

        # ----- Pagination buttons --------------------------------------
        def _on_prev(page, a, st, sr):
            new_page = max(1, int(page or 1) - 1)
            list_md_v, page_info_v, total = list_projects_md(a, st, sr, page=new_page)
            return list_md_v, page_info_v, max(1, min(new_page, total))

        def _on_next(page, a, st, sr):
            # We don't know total_pages without querying; let
            # list_projects_md clamp.
            new_page = int(page or 1) + 1
            list_md_v, page_info_v, total = list_projects_md(a, st, sr, page=new_page)
            return list_md_v, page_info_v, max(1, min(new_page, total))

        prev_btn.click(
            fn=_on_prev,
            inputs=[page_state, assigner_in, status_in, customer_in],
            outputs=[list_md, page_info_md, page_state],
        )
        next_btn.click(
            fn=_on_next,
            inputs=[page_state, assigner_in, status_in, customer_in],
            outputs=[list_md, page_info_md, page_state],
        )

        # ----- Detail search: narrow the picker's choices live ---------
        # As the user types, re-query and replace the dropdown's choices.
        # We DON'T force a selection — the user still picks from the
        # narrowed list. If the previously-selected id is no longer in the
        # filtered set, Gradio clears the selection (value falls out of
        # choices), which is the expected behaviour.
        def _on_detail_search(kw):
            return gr.update(choices=list_project_choices(kw))
        detail_search.change(
            fn=_on_detail_search,
            inputs=detail_search,
            outputs=project_picker,
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

    return {
        "scan_btn": scan_btn,
        "refresh_btn": refresh_btn,
        "scan_md": scan_md,
        "list_md": list_md,
        "project_picker": project_picker,
        "detail_md": detail_md,
        "assigner_in": assigner_in,
        "status_in": status_in,
        "customer_in": customer_in,
        # Pagination — needed by app.py for cross-tab scan handlers
        "page_state": page_state,
        "page_info_md": page_info_md,
    }


__all__ = ["build_projects_tab"]
