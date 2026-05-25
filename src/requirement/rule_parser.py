"""
Rule-based requirement parser.

No LLM, no network, fully deterministic. Handles the common patterns we see
in Chinese-language switch / server specs:

    "48口万兆三层核心交换机"  →  port_count>=48, port_speed=10G, layer=L3
    "支持POE,2U,冗余电源"     →  poe=True, rack_units=2, redundant_power=True
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .schema import Category, Requirement, RequirementField

# ---------------------------------------------------------------------------
# Patterns. Order matters when multiple match the same span — longer/more
# specific patterns should come first.
# ---------------------------------------------------------------------------

CATEGORY_KEYWORDS: dict[Category, tuple[str, ...]] = {
    "交换机": ("交换机", "switch", "switching", "汇聚", "接入", "核心"),
    "路由器": ("路由器", "router", "routing"),
    "服务器": ("服务器", "server", "刀片", "机架"),
    "存储":   ("存储", "存储阵列", "san", "nas", "storage"),
    "防火墙": ("防火墙", "firewall", "ips", "ngfw"),
    "无线":   ("无线", "wifi", "wi-fi", "ap", "ac控制器"),
}

SPEED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(100\s*g|百\s*g|万兆\s*\*\s*100)\b", re.I), "100G"),
    (re.compile(r"\b(40\s*g)\b", re.I), "40G"),
    (re.compile(r"\b(25\s*g)\b", re.I), "25G"),
    (re.compile(r"\b(10\s*g|万兆)\b", re.I), "10G"),
    (re.compile(r"\b(2\.5\s*g)\b", re.I), "2.5G"),
    (re.compile(r"\b(1\s*g|千兆|gigabit|ge\b)\b", re.I), "1G"),
    (re.compile(r"\b(100\s*m|百兆)\b", re.I), "100M"),
]

LAYER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(三层|l3|layer\s*3)", re.I), "L3"),
    (re.compile(r"(二层|l2|layer\s*2)", re.I), "L2"),
]

# "48口" / "48 ports" / "48个端口"
PORT_COUNT_RE = re.compile(r"(\d{1,3})\s*(?:口|个端口|ports?|个口|路)", re.I)

# "2U", "1U", "4U"
RU_RE = re.compile(r"\b(\d{1,2})\s*U\b", re.I)

# "100Gbps 交换容量" / "switching capacity 1.2T"
CAPACITY_RE = re.compile(
    r"(?:交换容量|switching\s*capacity)[^\d]*([\d\.]+)\s*([tg])bps?", re.I,
)
# "100Mpps 包转发率"
PPS_RE = re.compile(
    r"(?:包转发率|forwarding\s*rate|包转发)[^\d]*([\d\.]+)\s*mpps", re.I,
)

POE_RE = re.compile(r"(poe\+?|供电|802\.3a[tf])", re.I)
NEG_POE_RE = re.compile(r"(不需要|无需|不要|without)\s*poe", re.I)

REDUNDANT_PSU_RE = re.compile(r"(冗余电源|双电源|redundant\s*(power|psu))", re.I)
DOMESTIC_RE = re.compile(r"(国产化|自主可控|信创|国密)", re.I)

BUDGET_RE = re.compile(
    r"(?:预算|价格|不超过|<=|≤)[^\d]*([\d\.]+)\s*(万|w|k|千|元)?", re.I,
)


# ---------------------------------------------------------------------------
@dataclass
class RuleRequirementParser:
    """Stateless parser. Cheap to instantiate; safe to share across threads."""

    def parse(self, text: str, image_path: str | None = None) -> Requirement:
        req = Requirement()
        if not text:
            return req

        self._extract_category(text, req)
        self._extract_port_count(text, req)
        self._extract_port_speed(text, req)
        self._extract_layer(text, req)
        self._extract_capacity(text, req)
        self._extract_forwarding_rate(text, req)
        self._extract_rack_units(text, req)
        self._extract_poe(text, req)
        self._extract_redundant_psu(text, req)
        self._extract_domestic(text, req)
        self._extract_budget(text, req)
        self._extract_keywords(text, req)

        return req

    # ---- per-field extractors ----------------------------------------------
    def _extract_category(self, text: str, req: Requirement) -> None:
        lowered = text.lower()
        for cat, kws in CATEGORY_KEYWORDS.items():
            if any(kw in lowered for kw in kws):
                req.category = cat
                return

    def _extract_port_count(self, text: str, req: Requirement) -> None:
        nums = [int(m.group(1)) for m in PORT_COUNT_RE.finditer(text)]
        if not nums:
            return
        # Use the largest found number as the minimum requirement.
        # ("48+4" common pattern → user wants at least 48 access ports.)
        req.port_count = RequirementField(min=max(nums))

    def _extract_port_speed(self, text: str, req: Requirement) -> None:
        for pattern, label in SPEED_PATTERNS:
            if pattern.search(text):
                req.port_speed = RequirementField(exact=label)
                return

    def _extract_layer(self, text: str, req: Requirement) -> None:
        for pattern, label in LAYER_PATTERNS:
            if pattern.search(text):
                req.layer = RequirementField(exact=label)
                return

    def _extract_capacity(self, text: str, req: Requirement) -> None:
        m = CAPACITY_RE.search(text)
        if not m:
            return
        val = float(m.group(1))
        unit = m.group(2).lower()
        gbps = val * 1000 if unit == "t" else val
        req.switching_capacity_gbps = RequirementField(min=gbps)

    def _extract_forwarding_rate(self, text: str, req: Requirement) -> None:
        m = PPS_RE.search(text)
        if m:
            req.forwarding_rate_mpps = RequirementField(min=float(m.group(1)))

    def _extract_rack_units(self, text: str, req: Requirement) -> None:
        m = RU_RE.search(text)
        if m:
            req.rack_units = RequirementField(exact=int(m.group(1)))

    def _extract_poe(self, text: str, req: Requirement) -> None:
        if NEG_POE_RE.search(text):
            req.poe = RequirementField(exact=False)
        elif POE_RE.search(text):
            req.poe = RequirementField(exact=True)

    def _extract_redundant_psu(self, text: str, req: Requirement) -> None:
        if REDUNDANT_PSU_RE.search(text):
            req.redundant_power = RequirementField(exact=True)

    def _extract_domestic(self, text: str, req: Requirement) -> None:
        if DOMESTIC_RE.search(text):
            req.must_be_domestic = True

    def _extract_budget(self, text: str, req: Requirement) -> None:
        m = BUDGET_RE.search(text)
        if not m:
            return
        val = float(m.group(1))
        unit = (m.group(2) or "").lower()
        multiplier = {"万": 10000, "w": 10000, "k": 1000, "千": 1000}.get(unit, 1)
        req.budget_cny = RequirementField(max=val * multiplier)

    def _extract_keywords(self, text: str, req: Requirement) -> None:
        # Heuristic: collect distinct meaningful Chinese 2–4 char tokens / English words
        # that weren't already captured by the structured extractors.
        tokens = re.findall(r"[A-Za-z]{3,}|[一-鿿]{2,4}", text)
        skip = {"端口", "电源", "需求", "需要", "支持", "采购", "项目", "设备", "如下", "希望"}
        seen: set[str] = set()
        keywords: list[str] = []
        for t in tokens:
            tl = t.lower()
            if tl in skip or tl in seen:
                continue
            seen.add(tl)
            keywords.append(t)
        req.keywords = keywords[:20]   # cap to keep prompts tight


def _iter_field_lines(req: Requirement) -> Iterable[str]:
    """For pretty-printing; used by CLI."""
    if req.category:
        yield f"category: {req.category}"
    for name in (
        "port_count", "port_speed", "layer", "switching_capacity_gbps",
        "forwarding_rate_mpps", "rack_units", "poe", "redundant_power", "budget_cny",
    ):
        f: RequirementField = getattr(req, name)
        if f.is_set():
            parts = []
            if f.exact is not None: parts.append(f"=={f.exact}")
            if f.min   is not None: parts.append(f">={f.min}")
            if f.max   is not None: parts.append(f"<={f.max}")
            yield f"{name}: {' '.join(parts)}"
    if req.must_be_domestic:
        yield "must_be_domestic: True"
    if req.keywords:
        yield f"keywords: {', '.join(req.keywords[:10])}"


__all__ = ["RuleRequirementParser", "_iter_field_lines"]
