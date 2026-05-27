"""Catalog lists — 政府名录 / 创新名录 / etc. management.

A "catalog list" is an authoritative external whitelist of products. Use
`import_catalog()` to load a PDF and `match_codes_to_products()` to fuzz-
match its entries against our Product table.
"""

from .importer import ImportReport, import_catalog, rematch_all
from .matcher import MatchResult, match_codes_to_products
from .model_codes import (
    extract_from_extractor_output,
    extract_from_text,
    normalize,
)

__all__ = [
    "ImportReport",
    "import_catalog",
    "rematch_all",
    "MatchResult",
    "match_codes_to_products",
    "extract_from_text",
    "extract_from_extractor_output",
    "normalize",
]
