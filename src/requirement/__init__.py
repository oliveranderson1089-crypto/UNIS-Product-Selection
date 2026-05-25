"""Requirement parsing: free input (text/doc/image) → structured Requirement."""

from .ai_parser import AIRequirementParser
from .rule_parser import RuleRequirementParser
from .schema import Requirement, RequirementField, parse_requirement

__all__ = [
    "Requirement",
    "RequirementField",
    "RuleRequirementParser",
    "AIRequirementParser",
    "parse_requirement",
]
