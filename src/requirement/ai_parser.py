"""
LLM-backed requirement parser.

Asks the configured chat model (DeepSeek by default) to return a JSON object
matching `Requirement`. We always fall back to the rule parser if the model
output is malformed — so AI mode is strictly an ENHANCEMENT, never a
single-point-of-failure.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from ..llm import Message, get_router
from .rule_parser import RuleRequirementParser
from .schema import Requirement, RequirementField

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
你是一名网络/服务器产品选型助手。用户会给你一段需求描述(可能来自一段文本、
一份文档或一张图片)。你的任务是把需求抽取为下面这个 JSON schema:

{
  "category":     "交换机|路由器|服务器|存储|防火墙|无线|null",
  "use_case":     "字符串或 null",
  "port_count":          {"exact": null, "min": null, "max": null},
  "port_speed":          {"exact": "100M|1G|2.5G|10G|25G|40G|100G", "min": null, "max": null},
  "uplink_speed":        {"exact": null, "min": null, "max": null},
  "switching_capacity_gbps": {"exact": null, "min": null, "max": null},
  "forwarding_rate_mpps":    {"exact": null, "min": null, "max": null},
  "layer":               {"exact": "L2|L3|null"},
  "poe":                 {"exact": true|false|null},
  "redundant_power":     {"exact": true|null},
  "rack_units":          {"exact": 1|2|3|4|null},
  "cpu_cores":           {"min": null, "max": null},
  "memory_gb":           {"min": null, "max": null},
  "storage_tb":          {"min": null, "max": null},
  "budget_cny":          {"max": null},
  "must_be_domestic":    true|false,
  "keywords":            ["可选关键词..."],
  "notes":               "一句话概括用户意图"
}

规则:
- 用户没明确提到的字段必须返回 null,不要凭空猜。
- 数字字段不要带单位,把 "万兆" 翻译为 "10G",把 "100Mpps" 翻译为 100。
- 输出严格 JSON,不要包含 markdown 围栏或解释文字。
"""


@dataclass
class AIRequirementParser:
    """Wraps the LLM call and falls back to RuleRequirementParser on errors."""

    def parse(self, text: str, image_path: str | None = None) -> Requirement:
        if not text and not image_path:
            return Requirement()

        try:
            raw = self._invoke_llm(text, image_path)
            parsed = self._coerce_json(raw)
            req = self._to_requirement(parsed)
            if req.is_empty():
                logger.info("AI parse returned no useful fields; using rule parser.")
                return RuleRequirementParser().parse(text)
            return req
        except Exception as exc:                              # noqa: BLE001
            logger.warning("AI parser failed (%s) — falling back to rule parser.", exc)
            return RuleRequirementParser().parse(text)

    # ---- internals ----------------------------------------------------------
    def _invoke_llm(self, text: str, image_path: str | None) -> str:
        router = get_router()
        if image_path:
            messages = [
                Message(role="system", content=SYSTEM_PROMPT),
                Message(role="user", content=text or "图中是产品需求,请抽取结构化字段。",
                        image=image_path),
            ]
            resp = router.call("vision", messages)
        else:
            messages = [
                Message(role="system", content=SYSTEM_PROMPT),
                Message(role="user", content=text),
            ]
            resp = router.call("chat", messages)
        logger.debug("AI parser used %s tokens (%.4f CNY)",
                     resp.prompt_tokens + resp.completion_tokens, resp.cost_cny)
        return resp.text

    @staticmethod
    def _coerce_json(raw: str) -> dict[str, Any]:
        """Models occasionally wrap JSON in ```json fences. Strip and parse."""
        cleaned = raw.strip()
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
        if fence:
            cleaned = fence.group(1)
        return json.loads(cleaned)

    def _to_requirement(self, data: dict[str, Any]) -> Requirement:
        req = Requirement()

        # Coarse fields
        req.category = data.get("category") or None
        req.use_case = data.get("use_case") or None
        req.must_be_domestic = bool(data.get("must_be_domestic", False))
        req.keywords = [str(k) for k in (data.get("keywords") or []) if k]
        req.notes = data.get("notes") or None

        # RequirementField fields — schema name list must match dataclass.
        for name in (
            "port_count", "port_speed", "uplink_speed",
            "switching_capacity_gbps", "forwarding_rate_mpps",
            "layer", "poe", "redundant_power", "rack_units",
            "cpu_cores", "memory_gb", "storage_tb", "budget_cny",
        ):
            payload = data.get(name)
            if not isinstance(payload, dict):
                continue
            setattr(req, name, RequirementField(
                exact=payload.get("exact"),
                min=payload.get("min"),
                max=payload.get("max"),
            ))
        return req


__all__ = ["AIRequirementParser"]
