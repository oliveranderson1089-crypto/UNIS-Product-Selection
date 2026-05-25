"""
Scheduled catalog refresh.

`run_full_refresh()` is callable directly (for ad-hoc runs and tests) and is
also what the APScheduler cron job invokes.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from ..config import get_config
from ..parser import parse_all_pending
from ..scraper import UnisCrawler

logger = logging.getLogger(__name__)


@dataclass
class RefreshReport:
    products_seen: int = 0
    pdfs_downloaded: int = 0
    pdfs_parsed: int = 0
    parse_failures: int = 0
    fields_set: int = 0


def run_full_refresh(max_products: int | None = None) -> RefreshReport:
    """
    Crawl → download → parse, end to end.

    Args:
        max_products: cap for smoke-tests. None = no cap (production).
    """
    logger.info("Starting full catalog refresh (max_products=%s)", max_products)
    crawler = UnisCrawler()
    try:
        crawl_stats = crawler.run(max_products=max_products)
    finally:
        crawler.close()
    parse_stats = parse_all_pending()

    report = RefreshReport(
        products_seen=crawl_stats["products"],
        pdfs_downloaded=crawl_stats["pdfs"],
        pdfs_parsed=parse_stats.parsed,
        parse_failures=parse_stats.failed,
        fields_set=parse_stats.fields_set,
    )
    logger.info("Refresh complete: %s", asdict(report))
    return report


def start_scheduler() -> None:
    """
    Run as a long-lived process. Reads cron schedule from config.yaml.

        python -m src.scheduler.jobs
    """
    cfg = get_config()
    if not cfg.scheduler.enabled:
        logger.warning("Scheduler disabled in config.yaml (scheduler.enabled = false).")
        return

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler(timezone=cfg.scheduler.timezone)
    trigger = CronTrigger.from_crontab(cfg.scheduler.crawl_cron, timezone=cfg.scheduler.timezone)
    scheduler.add_job(run_full_refresh, trigger, id="refresh", max_instances=1, coalesce=True)
    logger.info("Scheduler started. Next run by cron: %s (%s)",
                cfg.scheduler.crawl_cron, cfg.scheduler.timezone)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    start_scheduler()


__all__ = ["run_full_refresh", "start_scheduler", "RefreshReport"]
