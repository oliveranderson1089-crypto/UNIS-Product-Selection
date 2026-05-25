"""Product selection engines."""

from .base import MatchResult, Matcher
from .ai_matcher import AIMatcher
from .rule_matcher import RuleMatcher

__all__ = ["Matcher", "MatchResult", "RuleMatcher", "AIMatcher"]
