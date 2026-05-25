"""Document and image extractors.

`extract(path)` returns a normalized `ExtractedContent` regardless of source
format, so downstream code never branches on file type.
"""

from .base import ExtractedContent, ExtractedTable
from .dispatcher import extract, extract_bytes, supported_extensions

__all__ = [
    "ExtractedContent",
    "ExtractedTable",
    "extract",
    "extract_bytes",
    "supported_extensions",
]
