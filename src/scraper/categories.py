"""
Section + category vocabulary for unisyue.com.

Two top-level sections on the site:
  - Autonomous_Controllable (创新产品 / Innovation)
  - Commercial_Product       (通用产品 / General-purpose)

Each section has numeric category IDs in its URL path, e.g.
`/Autonomous_Controllable/11/UNISS12600-CR-G/2497.html`. The ID alone is
opaque (11, 14, 21…), so we maintain an explicit map to:
  - the Chinese category name as displayed in the breadcrumb
  - a stable English slug used for on-disk folder names

Mappings were verified against the live site on 2026-05-25:

    Autonomous_Controllable        Commercial_Product
    -----------------------        ---------------------
    11  交换机                     21  交换机
    12  路由器                     22  路由器
    13  安全                       23  安全
    14  计算存储                   24  计算存储
    15  大模型一体机               25  智能管理
                                   26  云计算
                                   27  大数据
                                   28  无线局域网

Keep this list authoritative — the crawler & downloader both rely on it.
"""

from __future__ import annotations

from dataclasses import dataclass


# Top-level sections
SECTION_AUTONOMOUS = "Autonomous_Controllable"   # 创新产品
SECTION_COMMERCIAL = "Commercial_Product"        # 通用产品

# English label per section — used in DB so it's queryable.
SECTION_LABEL_EN = {
    SECTION_AUTONOMOUS: "innovation",
    SECTION_COMMERCIAL: "general",
}
SECTION_LABEL_ZH = {
    SECTION_AUTONOMOUS: "创新产品",
    SECTION_COMMERCIAL: "通用产品",
}


@dataclass(frozen=True)
class CategorySpec:
    section: str          # "Autonomous_Controllable" | "Commercial_Product"
    cat_id: str           # "11", "14", "21", …
    name_zh: str          # "交换机", "路由器", …
    slug_en: str          # "switches", "routers", …  → folder name

    @property
    def start_path(self) -> str:
        return f"/{self.section}/{self.cat_id}/default.html"


# Chinese → English slug. Used both for known IDs and as a fallback when we
# encounter a name we haven't pre-mapped (rare).
ZH_TO_EN: dict[str, str] = {
    "交换机":           "switches",
    "路由器":           "routers",
    "安全":             "security",
    "计算存储":         "compute_storage",
    "大模型一体机":     "ai_platforms",
    "智能管理":         "smart_management",
    "云计算":           "cloud_computing",
    "大数据":           "big_data",
    "无线局域网":       "wireless_lan",
    "无线":             "wireless",
    "服务器":           "servers",
    "存储":             "storage",
    "防火墙":           "firewalls",
}

# Authoritative category list. The crawler iterates these as its start paths.
CATEGORIES: tuple[CategorySpec, ...] = (
    # ---- 创新产品 ----
    CategorySpec(SECTION_AUTONOMOUS, "11", "交换机",       "switches"),
    CategorySpec(SECTION_AUTONOMOUS, "12", "路由器",       "routers"),
    CategorySpec(SECTION_AUTONOMOUS, "13", "安全",         "security"),
    CategorySpec(SECTION_AUTONOMOUS, "14", "计算存储",     "compute_storage"),
    CategorySpec(SECTION_AUTONOMOUS, "15", "大模型一体机", "ai_platforms"),
    # ---- 通用产品 ----
    CategorySpec(SECTION_COMMERCIAL, "21", "交换机",       "switches"),
    CategorySpec(SECTION_COMMERCIAL, "22", "路由器",       "routers"),
    CategorySpec(SECTION_COMMERCIAL, "23", "安全",         "security"),
    CategorySpec(SECTION_COMMERCIAL, "24", "计算存储",     "compute_storage"),
    CategorySpec(SECTION_COMMERCIAL, "25", "智能管理",     "smart_management"),
    CategorySpec(SECTION_COMMERCIAL, "26", "云计算",       "cloud_computing"),
    CategorySpec(SECTION_COMMERCIAL, "27", "大数据",       "big_data"),
    CategorySpec(SECTION_COMMERCIAL, "28", "无线局域网",   "wireless_lan"),
)


# Quick lookup helpers
_BY_PATH: dict[tuple[str, str], CategorySpec] = {
    (c.section, c.cat_id): c for c in CATEGORIES
}


def lookup_by_url_parts(section: str, cat_id: str) -> CategorySpec | None:
    """Resolve (section, cat_id) parsed from a product URL → CategorySpec."""
    return _BY_PATH.get((section, cat_id))


def english_slug_for(name_zh: str) -> str:
    """
    Translate a Chinese category name to its English folder slug.
    Returns the input lowered with non-alnum stripped if unknown — never raises.
    """
    if name_zh in ZH_TO_EN:
        return ZH_TO_EN[name_zh]
    import re
    fallback = re.sub(r"[^A-Za-z0-9]+", "_", name_zh.lower()).strip("_")
    return fallback or "uncategorized"


__all__ = [
    "CategorySpec",
    "CATEGORIES",
    "SECTION_AUTONOMOUS",
    "SECTION_COMMERCIAL",
    "SECTION_LABEL_EN",
    "SECTION_LABEL_ZH",
    "ZH_TO_EN",
    "lookup_by_url_parts",
    "english_slug_for",
]
