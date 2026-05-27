"""
Catalog extractor using Claude vision.

Per-page workflow:
    1. Render PDF page to PNG (200 dpi by default — Claude tokenizer handles it).
    2. Send PNG to Claude via LLMRouter task='vision'.
    3. Ask Claude to extract the verbatim text content of the page
       AND return JSON `[{"model": "UNIS S5800-X-EI-G", ...}, ...]`.

We prefer the JSON output (machine-readable) but also keep the raw text
in case the JSON fails to parse — then we fall back to regex extraction.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from ...config import get_config
from ...llm import Message, get_router
from ..rasterize import pdf_to_png_bytes
from .base import CatalogExtractor

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
你是一个文档结构化助手。我会上传一张图片(政府采购名录 / 产品名录的扫描页)。
请你:
1) 把页面里所有产品型号代码完整列出
2) 不要漏掉任何 UNIS / H3C / 紫光 系列的型号代码
3) 严格按下面 JSON 数组格式输出,不要添加解释,不要 markdown 围栏:

[
  {"model": "UNIS S5800-X-EI-G", "category": "交换机", "notes": ""},
  {"model": "UNIS Server R4930 G7", "category": "服务器", "notes": ""}
]

如果某行不是产品(标题/说明/印章/页码)就跳过。category 可以留空。
"""


class ClaudeVisionExtractor(CatalogExtractor):
    name = "claude"

    def __init__(self, dpi: int = 200):
        self.dpi = dpi

    def available(self) -> bool:
        cfg = get_config()
        return bool(cfg.secrets.anthropic_api_key)

    def extract_text(self, pdf_path: Path) -> str:
        """
        Returns a JSON array string of {"model","category","notes"} per page,
        concatenated. The downstream parser tolerates both JSON and free text.
        """
        if not self.available():
            raise RuntimeError(
                "Claude extractor unavailable. Fix:\n"
                "  1) Copy .env.example to .env if you haven't already\n"
                "  2) Set ANTHROPIC_API_KEY=sk-ant-... in .env\n"
                "     (get a key at https://console.anthropic.com/settings/keys)\n"
                "Or fall back to local OCR with --extractor ocr"
            )

        pages = pdf_to_png_bytes(pdf_path, dpi=self.dpi)
        if not pages:
            logger.warning("No pages rasterized from %s", pdf_path)
            return ""

        router = get_router()
        all_entries: list[dict] = []
        for i, png in enumerate(pages, 1):
            logger.info("Claude vision: page %d/%d", i, len(pages))
            resp = router.call(
                task="vision",
                messages=[
                    Message(role="system", content=_SYSTEM_PROMPT),
                    Message(
                        role="user",
                        content="请提取这一页中的所有产品型号。",
                        image=png,
                    ),
                ],
            )
            page_entries = self._parse_json_safe(resp.text)
            if page_entries:
                all_entries.extend(page_entries)
            else:
                # Even when JSON parsing fails, keep raw text for the
                # downstream regex parser to grovel through.
                all_entries.append({"_raw": resp.text})

        return json.dumps(all_entries, ensure_ascii=False, indent=2)

    @staticmethod
    def _parse_json_safe(text: str) -> list[dict]:
        cleaned = text.strip()
        # Tolerate ```json fences if the model adds them despite the prompt.
        m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
        if m:
            cleaned = m.group(1).strip()
        try:
            data = json.loads(cleaned)
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        except Exception:                                             # noqa: BLE001
            pass
        return []


__all__ = ["ClaudeVisionExtractor"]
