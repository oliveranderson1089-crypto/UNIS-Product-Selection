"""
Subcommand group: `catalog` — manage product whitelists (政府名录 / 创新名录 ...).

    python -m src.cli catalog import "2025年V1名录承诺函.pdf" --name "2025-V1-名录"
    python -m src.cli catalog import "..." --extractor ocr      # 强制用本地 OCR
    python -m src.cli catalog list
    python -m src.cli catalog show <name>
    python -m src.cli catalog rematch         # 新爬完产品后重新关联
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.table import Table
from sqlalchemy import select

from ._common import console, setup_logging


@click.group(name="catalog", help="名录管理:导入/查看/匹配 政府采购名录、创新名录等。")
def cmd() -> None:
    pass


# ---------------------------------------------------------------------------
@cmd.command("import", help="导入一份名录文件(PDF/扫描件)。")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--name", required=True, help="名录名称(以后在 --catalog 中引用)")
@click.option("--extractor",
              type=click.Choice(["claude", "ocr"], case_sensitive=False),
              default=None,
              help="强制使用某个抽取器;默认有 Claude key 时用 Claude,否则 OCR")
@click.option("--notes", default=None, help="备注(可选)")
def _import(path: Path, name: str, extractor: str | None, notes: str | None) -> None:
    setup_logging()
    from ..catalog_lists import import_catalog

    with console.status(f"[cyan]导入 {name}[/cyan] — 提取 + 匹配中..."):
        try:
            report = import_catalog(
                path, name=name,
                extractor=extractor.lower() if extractor else None,
                notes=notes,
                replace=True,
            )
        except Exception as exc:
            console.print(f"[red]导入失败:[/red] {exc}")
            raise SystemExit(1)

    _render_import_report(report)


@cmd.command("list", help="列出所有已导入的名录。")
def _list() -> None:
    setup_logging()
    from ..storage import get_db
    from ..storage.models import CatalogEntry, CatalogList

    db = get_db()
    with db.session() as s:
        cats = list(s.scalars(select(CatalogList)))
        if not cats:
            console.print("[yellow]还没有任何名录。用 `catalog import` 添加。[/yellow]")
            return
        tbl = Table(title=f"已注册的名录 ({len(cats)})", header_style="bold magenta")
        for h in ("name", "total", "matched", "extractor", "imported_at"):
            tbl.add_column(h)
        for c in cats:
            entries = list(s.scalars(
                select(CatalogEntry).where(CatalogEntry.catalog_id == c.id)
            ))
            total = len(entries)
            matched = sum(1 for e in entries if e.product_id is not None)
            tbl.add_row(
                c.name, str(total), f"{matched}/{total}",
                c.extractor or "?",
                c.imported_at.strftime("%Y-%m-%d %H:%M") if c.imported_at else "-",
            )
    console.print(tbl)


@cmd.command("show", help="查看一份名录里的所有型号 + 匹配状态。")
@click.argument("name")
@click.option("--unmatched-only", is_flag=True, help="只显示未匹配上的型号")
def _show(name: str, unmatched_only: bool) -> None:
    setup_logging()
    from ..storage import get_db
    from ..storage.models import CatalogEntry, CatalogList, Product

    db = get_db()
    with db.session() as s:
        cat = s.scalar(select(CatalogList).where(CatalogList.name == name))
        if cat is None:
            console.print(f"[red]找不到名录 {name!r}。可用 `catalog list` 查看。[/red]")
            raise SystemExit(1)
        entries = list(s.scalars(
            select(CatalogEntry).where(CatalogEntry.catalog_id == cat.id)
        ))
        products = {p.id: p for p in s.scalars(select(Product))}

        tbl = Table(
            title=f"{cat.name}  (extractor={cat.extractor})  "
                  f"共 {len(entries)} 条",
            header_style="bold magenta",
        )
        for h in ("#", "raw code", "matched product", "method", "category"):
            tbl.add_column(h)
        shown = 0
        for i, e in enumerate(entries, 1):
            if unmatched_only and e.product_id is not None:
                continue
            p = products.get(e.product_id) if e.product_id else None
            tbl.add_row(
                str(i), e.raw_model_code,
                p.model if p else "[red]未匹配[/red]",
                e.match_method or "-",
                p.category if p else "-",
            )
            shown += 1
        console.print(tbl)
        console.print(f"[dim]显示 {shown} 行;过滤参数 unmatched_only={unmatched_only}[/dim]")


@cmd.command("rematch", help="对所有名录重新跑型号匹配(在新爬完产品后用)。")
def _rematch() -> None:
    setup_logging()
    from ..catalog_lists import rematch_all

    with console.status("重新匹配中..."):
        reports = rematch_all()
    if not reports:
        console.print("[yellow]没有任何名录。先 `catalog import` 一份。[/yellow]")
        return
    for name, r in reports.items():
        console.print(f"[cyan]{name}[/cyan]: matched {r.matched}/{r.total_codes}, "
                      f"by_method={r.by_method}")


# ---------------------------------------------------------------------------
def _render_import_report(report) -> None:
    from rich.panel import Panel

    method_str = ", ".join(f"{k}={v}" for k, v in sorted(report.by_method.items()))
    body = (
        f"名录: [cyan]{report.catalog_name}[/cyan]\n"
        f"提取器: {report.extractor_used}\n"
        f"总型号数: {report.total_codes}\n"
        f"匹配上: [green]{report.matched}[/green]   未匹配: [yellow]{report.unmatched}[/yellow]\n"
        f"匹配方法分布: {method_str or '-'}"
    )
    console.print(Panel(body, title="导入完成", border_style="green"))

    if report.unmatched_codes:
        unmatched_preview = "\n".join(f"  • {c}" for c in report.unmatched_codes[:15])
        more = f"\n  ... 还有 {len(report.unmatched_codes) - 15} 条" \
            if len(report.unmatched_codes) > 15 else ""
        console.print(Panel(
            f"以下 {len(report.unmatched_codes)} 个型号在你的产品库里找不到,"
            f"可能产品库还没爬到,或者型号代码源不一致。\n\n{unmatched_preview}{more}\n\n"
            f"[dim]提示:抓取产品后跑 `catalog rematch` 自动重连。[/dim]",
            title="未匹配型号", border_style="yellow",
        ))
