"""
Seed a handful of demo products so the CLI runs end-to-end before the crawler
has been pointed at a live network.

    python scripts/seed_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage import get_db      # noqa: E402


DEMO_PRODUCTS = [
    {
        "model": "UNIS S12600-CR-G",
        "series": "UNIS S12600",
        "category": "交换机",
        "sub_category": "数据中心核心",
        "name": "UNIS S12600-CR-G 系列数据中心交换机",
        "description": "面向超大规模数据中心的核心交换平台,支持高密 100G/400G,L3 全功能。",
        "page_url": "https://www.unisyue.com/Autonomous_Controllable/11/UNISS12600-CR-G/2497.html",
        "port_count": 48,
        "port_speed": "100G",
        "uplink_speed": "400G",
        "switching_capacity_gbps": 25600.0,
        "forwarding_rate_mpps": 9600.0,
        "layer": "L3",
        "poe": False,
        "redundant_power": True,
        "rack_units": 16,
        "is_domestic": True,
    },
    {
        "model": "UNIS S6520X-EI",
        "series": "UNIS S6520X",
        "category": "交换机",
        "sub_category": "汇聚",
        "name": "UNIS S6520X-EI 万兆汇聚交换机",
        "description": "园区汇聚层万兆交换机,支持丰富三层路由协议。",
        "page_url": "https://www.unisyue.com/example/UNISS6520X-EI",
        "port_count": 48,
        "port_speed": "10G",
        "uplink_speed": "40G",
        "switching_capacity_gbps": 2560.0,
        "forwarding_rate_mpps": 1080.0,
        "layer": "L3",
        "poe": False,
        "redundant_power": True,
        "rack_units": 1,
        "is_domestic": True,
    },
    {
        "model": "UNIS S5130S-EI",
        "series": "UNIS S5130S",
        "category": "交换机",
        "sub_category": "接入",
        "name": "UNIS S5130S-EI 千兆接入交换机",
        "description": "中小型园区接入层千兆交换机,支持 PoE+ 供电。",
        "page_url": "https://www.unisyue.com/example/UNISS5130S-EI",
        "port_count": 48,
        "port_speed": "1G",
        "uplink_speed": "10G",
        "switching_capacity_gbps": 336.0,
        "forwarding_rate_mpps": 130.0,
        "layer": "L2",
        "poe": True,
        "redundant_power": False,
        "rack_units": 1,
        "is_domestic": True,
    },
    {
        "model": "UNIS S5120V3-EI",
        "series": "UNIS S5120V3",
        "category": "交换机",
        "sub_category": "接入",
        "name": "UNIS S5120V3-EI 千兆接入交换机",
        "description": "经济型千兆接入交换机,适合小型办公。",
        "page_url": "https://www.unisyue.com/example/UNISS5120V3-EI",
        "port_count": 24,
        "port_speed": "1G",
        "uplink_speed": "10G",
        "switching_capacity_gbps": 224.0,
        "forwarding_rate_mpps": 96.0,
        "layer": "L2",
        "poe": False,
        "redundant_power": False,
        "rack_units": 1,
        "is_domestic": True,
    },
    {
        "model": "UNIS R6800-G2",
        "series": "UNIS R6800",
        "category": "服务器",
        "sub_category": "通用机架",
        "name": "UNIS R6800-G2 2U 机架服务器",
        "description": "通用业务工作负载,支持双路至强,2U 机架。",
        "page_url": "https://www.unisyue.com/example/R6800",
        "cpu_cores": 64,
        "memory_gb": 512,
        "storage_tb": 12.0,
        "rack_units": 2,
        "redundant_power": True,
        "is_domestic": True,
    },
]


def main() -> None:
    db = get_db()
    n = db.bulk_insert_seed(DEMO_PRODUCTS)
    print(f"Seeded {n} demo products.")


if __name__ == "__main__":
    main()
