"""
Structured representation of a parsed user requirement.

The shape is deliberately broad: a product-selection query touches many
dimensions (port count, throughput, layer, power…), but for any given query
most fields are unset. The matcher treats `None` as "user doesn't care".
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal

# Categories we know how to filter. Keep aligned with crawler.category_whitelist.
Category = Literal[
    "交换机", "路由器", "服务器", "存储",
    "防火墙", "无线", "云平台", "其他",
]


@dataclass
class RequirementField:
    """
    A single constraint with optional min/max and an exact-match hint.

    Examples:
        port_count = RequirementField(min=48)          # at least 48 ports
        port_speed = RequirementField(exact="10G")     # exactly 10G
        layer      = RequirementField(exact="L3")
    """

    exact: Any = None
    min: float | None = None
    max: float | None = None
    # Free-form preference text the matcher may surface to the user
    note: str | None = None

    def is_set(self) -> bool:
        return self.exact is not None or self.min is not None or self.max is not None

    def matches(self, value: Any) -> bool:
        """True iff `value` satisfies this constraint."""
        if not self.is_set():
            return True
        if self.exact is not None:
            if isinstance(value, str) and isinstance(self.exact, str):
                return self.exact.lower() in value.lower()
            return value == self.exact
        if value is None:
            return False
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return False
        if self.min is not None and numeric < self.min:
            return False
        if self.max is not None and numeric > self.max:
            return False
        return True


@dataclass
class Requirement:
    """
    Structured user requirement. Every field is optional — matchers should
    skip unset constraints, not penalize them.
    """

    # ---- coarse buckets -----------------------------------------------------
    category: Category | None = None              # e.g. "交换机"
    use_case: str | None = None                   # "数据中心" / "园区接入" / etc.
    deployment_scale: str | None = None           # "小型办公" / "大型园区"

    # ---- networking-specific constraints ------------------------------------
    port_count: RequirementField = field(default_factory=RequirementField)
    port_speed: RequirementField = field(default_factory=RequirementField)   # "1G"/"10G"/"25G"/"40G"/"100G"
    uplink_speed: RequirementField = field(default_factory=RequirementField)
    switching_capacity_gbps: RequirementField = field(default_factory=RequirementField)
    forwarding_rate_mpps: RequirementField = field(default_factory=RequirementField)
    layer: RequirementField = field(default_factory=RequirementField)        # "L2" / "L3"
    poe: RequirementField = field(default_factory=RequirementField)          # True/False
    redundant_power: RequirementField = field(default_factory=RequirementField)
    rack_units: RequirementField = field(default_factory=RequirementField)

    # ---- compute / storage extensibility (for future categories) -----------
    cpu_cores: RequirementField = field(default_factory=RequirementField)
    memory_gb: RequirementField = field(default_factory=RequirementField)
    storage_tb: RequirementField = field(default_factory=RequirementField)

    # ---- commercial constraints ---------------------------------------------
    budget_cny: RequirementField = field(default_factory=RequirementField)
    must_be_domestic: bool = False                # "国产化" / "自主可控"

    # ---- free-text & raw context --------------------------------------------
    keywords: list[str] = field(default_factory=list)    # other meaningful tokens
    raw_input: str = ""                                  # original text for traceability
    source_kind: str = "text"                            # text|docx|pdf|xlsx|image
    notes: str | None = None                             # human-readable summary

    # ---- helpers ------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Drop empty/unset fields for compact display
        return {k: v for k, v in d.items() if v not in (None, "", [], {}, RequirementField())}

    def is_empty(self) -> bool:
        """True if the parser couldn't extract any usable signal."""
        any_field_set = any(
            isinstance(getattr(self, name), RequirementField)
            and getattr(self, name).is_set()
            for name in self.__dataclass_fields__
        )
        return not (
            any_field_set
            or self.category
            or self.use_case
            or self.keywords
            or self.must_be_domestic
        )


def parse_requirement(
    *,
    text: str | None = None,
    document_path: str | None = None,
    image_path: str | None = None,
    use_ai: bool = False,
) -> Requirement:
    """
    Convenience facade so the CLI can stay one-line.

    Heavy lifting is in `RuleRequirementParser` / `AIRequirementParser`.
    """
    from ..extractors import extract                    # local import: avoid cycles
    from .ai_parser import AIRequirementParser
    from .rule_parser import RuleRequirementParser

    pieces: list[str] = []
    source_kind = "text"
    image_for_ai: str | None = image_path

    if text:
        pieces.append(text)
    if document_path:
        content = extract(document_path)
        source_kind = content.kind
        pieces.append(content.to_prompt())
    if image_path:
        source_kind = "image"
        if not use_ai:
            # Without AI we can't read images — degrade gracefully.
            pieces.append(f"[Image requirement ignored in rule mode: {image_path}]")

    combined = "\n\n".join(p for p in pieces if p)

    parser = (
        AIRequirementParser() if use_ai else RuleRequirementParser()
    )
    req = parser.parse(combined, image_path=image_for_ai if use_ai else None)
    req.raw_input = combined
    req.source_kind = source_kind
    return req


__all__ = ["Requirement", "RequirementField", "Category", "parse_requirement"]
