"""
Subcommand: `index` — build / inspect the semantic vector index.

    python -m src.cli index build      # (re)build embeddings over the catalog
    python -m src.cli index build -v   # with DEBUG logging
    python -m src.cli index status     # show backend + indexed vector count

The semantic index is a derivative cache of the SQLite catalog: it powers
meaning-based recall in AI-mode selection. Rebuild it after each `crawl` so
newly-added products become searchable.
"""

from __future__ import annotations

import logging
import time

import click

from ._common import console, setup_logging


@click.group(name="index", help="语义向量索引:构建 / 查看状态。")
def cmd() -> None:
    pass


@cmd.command(name="build", help="(重)构建产品语义向量索引(嵌入全部产品)。")
@click.option("-v", "--verbose", is_flag=True, help="启用 DEBUG 日志")
def build_cmd(verbose: bool) -> None:
    setup_logging()
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    from ..config import get_config
    from ..selector import SemanticIndex

    cfg = get_config()
    if cfg.llm.embedding is None:
        console.print(
            "[red]未配置 llm.embedding —— 无法构建语义索引。[/red]\n"
            "请在 config.yaml 的 llm.embedding 下配置嵌入模型(如 ollama/bge-m3)后重试。"
        )
        raise SystemExit(1)

    backend = cfg.selector.vector_backend
    model = cfg.llm.embedding.model
    try:
        with console.status(f"嵌入中(后端={backend},模型={model})…"):
            t0 = time.time()
            n = SemanticIndex(cfg).build()
            dt = time.time() - t0
    except Exception as exc:                              # noqa: BLE001
        console.print(f"[red]构建失败:[/red] {exc}")
        console.print("[dim]提示:确认 Ollama 正在运行,且已执行 `ollama pull bge-m3`。[/dim]")
        raise SystemExit(1)

    if n == 0:
        console.print("[yellow]目录中没有产品可索引 —— 先运行 `python -m src.cli crawl`。[/yellow]")
    else:
        console.print(
            f"[green]索引构建完成:[/green] {n} 个产品向量,用时 {dt:.1f}s(后端 {backend})。"
        )


@cmd.command(name="status", help="查看索引后端、嵌入模型与已索引向量数。")
def status_cmd() -> None:
    setup_logging()

    from ..config import get_config
    from ..selector import SemanticIndex

    cfg = get_config()
    emb = cfg.llm.embedding
    n = SemanticIndex(cfg).count()

    console.print(f"后端:      [cyan]{cfg.selector.vector_backend}[/cyan]")
    console.print(f"嵌入模型:  [cyan]{emb.provider + '/' + emb.model if emb else '未配置'}[/cyan]")
    console.print(f"已索引向量:[cyan]{n}[/cyan]")
    console.print(f"存储目录:  [dim]{cfg.storage.chroma_dir}[/dim]")
    if n == 0:
        console.print("[yellow]索引为空 —— 运行 `python -m src.cli index build` 构建。[/yellow]")


if __name__ == "__main__":
    cmd()
