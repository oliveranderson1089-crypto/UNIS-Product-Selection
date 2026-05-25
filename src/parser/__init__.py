"""Convert downloaded brochure PDFs into structured product specs."""

from .brochure_parser import BrochureParser, parse_all_pending

__all__ = ["BrochureParser", "parse_all_pending"]
