r"""
Lock down the rule-parser fixes from stage L.

The recurring failure mode: Chinese-character word boundaries. Patterns like
`\b千兆\b` look correct in English-context regex but DON'T match inside
"48口千兆接入交换机" because Chinese is `\w` under Unicode, so there's no
boundary between "口" and "千" or between "兆" and "接".

If one of these tests starts failing, check whether someone re-introduced
`\b` around a Chinese token in src/requirement/rule_parser.py.
"""

from __future__ import annotations

import pytest

from src.requirement.rule_parser import RuleRequirementParser


parser = RuleRequirementParser()


# ---------------------------------------------------------------------------
# port_speed: Chinese tokens (千兆 / 万兆 / 百兆) must match in any context.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        # Bare tokens
        ("千兆",                 "1G"),
        ("万兆",                 "10G"),
        ("百兆",                 "100M"),
        # Surrounded by Chinese on both sides — the bug case
        ("48口千兆接入交换机",   "1G"),
        ("万兆三层核心交换机",   "10G"),
        ("百兆接入",             "100M"),
        # English/numeric forms still work
        ("48口 10G 三层",        "10G"),
        ("100G 数据中心",        "100G"),
        ("400G",                 "400G"),
        ("10GE 上行",            "10G"),
        ("25GbE",                "25G"),
        # Higher tier wins when multiple appear
        ("万兆上行,100G 核心",   "100G"),
    ],
)
def test_port_speed_extraction(text: str, expected: str) -> None:
    req = parser.parse(text)
    assert req.port_speed.is_set(), f"no port_speed extracted from {text!r}"
    assert req.port_speed.exact == expected


def test_port_speed_absent_when_no_signal() -> None:
    assert not parser.parse("我要一台便宜点的设备").port_speed.is_set()


# ---------------------------------------------------------------------------
# layer: 三层 / 二层 must match without surrounding whitespace.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("三层核心交换机",       "L3"),
        ("二层接入",             "L2"),
        ("万兆三层",             "L3"),
        ("L3 路由",              "L3"),
        ("layer 2 access",       "L2"),
    ],
)
def test_layer_extraction(text: str, expected: str) -> None:
    req = parser.parse(text)
    assert req.layer.is_set(), f"no layer extracted from {text!r}"
    assert req.layer.exact == expected


# ---------------------------------------------------------------------------
# rack_units: \b-around-digit-and-U was broken next to Chinese chars.
# Replaced with explicit lookarounds. Test both correct matches and rejections.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("2U 机架服务器",        2),
        ("1U千兆PoE",            1),                # the bug case (no space)
        ("数据中心 4U 机箱",     4),
        ("16U 全高",             16),
    ],
)
def test_rack_units_extraction(text: str, expected: int) -> None:
    req = parser.parse(text)
    assert req.rack_units.is_set(), f"no rack_units extracted from {text!r}"
    assert req.rack_units.exact == expected


@pytest.mark.parametrize(
    "text",
    [
        "USB 接口",          # "U" inside a word — must NOT match
        "100U的电压",         # "U" inside a longer number — must NOT match
        "需要 UART",          # standalone U-word
    ],
)
def test_rack_units_rejects_false_positives(text: str) -> None:
    assert not parser.parse(text).rack_units.is_set(), f"false rack_units in {text!r}"


# ---------------------------------------------------------------------------
# port_count: not boundary-sensitive, but exercise it to be safe.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("48口",                 48),
        ("24个端口",             24),
        ("48 ports",             48),
        ("48口+4个上行",         48),     # max of multiple with units on both
    ],
)
def test_port_count_extraction(text: str, expected: int) -> None:
    req = parser.parse(text)
    assert req.port_count.is_set()
    assert req.port_count.min == expected


# ---------------------------------------------------------------------------
# Integrated case: the originally-failing CLI query string from stage K notes.
# ---------------------------------------------------------------------------
def test_combined_requirement_parses_all_fields() -> None:
    req = parser.parse("48口千兆三层接入交换机,自主可控,1U,冗余电源,PoE")

    assert req.category == "交换机"
    assert req.port_count.min == 48
    assert req.port_speed.exact == "1G"
    assert req.layer.exact == "L3"
    assert req.rack_units.exact == 1
    assert req.poe.exact is True
    assert req.redundant_power.exact is True
    assert req.must_be_domestic is True
