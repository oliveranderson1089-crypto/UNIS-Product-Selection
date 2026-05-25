"""Run one catalog refresh now (crawl + download + parse).

    python scripts/run_crawler.py                  # full refresh
    python scripts/run_crawler.py --max 3          # smoke-test: 3 products
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scheduler import run_full_refresh    # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", "--max-products", dest="max_products", type=int,
                        default=None, help="Stop after N products (smoke-test)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    report = run_full_refresh(max_products=args.max_products)
    print(report)
