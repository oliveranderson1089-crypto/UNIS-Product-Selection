"""
"报价单编辑" tab — upload, apply rules, download.
"""

from __future__ import annotations

import gradio as gr

from .helpers import format_quote_ui


def build_quote_tab():
    with gr.Tab("📊 报价单编辑"):
        gr.Markdown(
            "## 报价单格式化\n\n"
            "上传 H3C 配置器导出的报价单(`.xls` / `.xlsx` 都可以),自动应用以下规则:\n\n"
            "- 删除 价格汇总表 的固定 4 列(产品名称 / 详细描述 / 要求提前报备周期 / 订单准备周期)\n"
            "- 价格汇总表 中 **产品型号** 为空时,从 描述 列自动补出 UNIS 型号\n"
            "- 删除左上角的 H3C logo 图片\n"
            "- 服务器报价:删除内部组件行(假内存/电缆/滑轨/导风罩等)\n\n"
            "> 💡 `.xls` 会自动用本机 Excel / WPS 转成 `.xlsx`(公式、图片、格式全保留)。"
            "如果系统没装 Office,会回退到纯 Python 读法(只保留数值,丢公式)。\n\n"
            "> 🚧 即将上线:R4930/R3935 G7 服务行替换、R3800FT20 G3 模板填充。"
        )

        with gr.Row():
            file_in = gr.File(
                label="📁 上传报价单(.xls / .xlsx)",
                file_types=[".xls", ".xlsx", ".xlsm"],
                type="filepath",
            )
        with gr.Accordion("跳过规则(高级)", open=False):
            with gr.Row():
                skip_logo = gr.Checkbox(label="不删 logo")
                skip_columns = gr.Checkbox(label="不删 4 列")
            with gr.Row():
                skip_model = gr.Checkbox(label="不补型号")
                skip_server = gr.Checkbox(label="不清理服务器内部组件")

        run_btn = gr.Button("🚀 开始格式化", variant="primary", size="lg")
        report_md = gr.Markdown(value="")
        download = gr.File(label="📥 下载格式化后的文件", interactive=False)

        run_btn.click(
            fn=format_quote_ui,
            inputs=[file_in, skip_logo, skip_columns, skip_model, skip_server],
            outputs=[report_md, download],
        )


__all__ = ["build_quote_tab"]
