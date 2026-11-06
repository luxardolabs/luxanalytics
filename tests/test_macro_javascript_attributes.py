"""A value handed to a macro reaches the browser in a data-* attribute, never in JavaScript.

The browser decodes an attribute's entities before Alpine or an on* handler runs it, so autoescaping
does not protect a value written into one. A value in a data-* attribute stays text.
"""

from html.parser import HTMLParser

import pytest

from app.web.templates import templates

HOSTILE = "x');alert(1);//"
JAVASCRIPT_ATTRIBUTE_PREFIXES = ("x-", ":", "@", "on")


class _Elements(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.attributes: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.attributes.append({name: value or "" for name, value in attrs})


def _elements(source: str) -> list[dict[str, str]]:
    parser = _Elements()
    parser.feed(templates.env.from_string(source).render(hostile=HOSTILE))
    return parser.attributes


def _javascript(elements: list[dict[str, str]]) -> list[str]:
    return [
        value
        for attributes in elements
        for name, value in attributes.items()
        if name.startswith(JAVASCRIPT_ATTRIBUTE_PREFIXES)
    ]


@pytest.mark.parametrize(
    "call",
    [
        "copy_button(hostile)",
        "copy_button('id', text=hostile)",
        "copy_button('id', success_text=hostile)",
        "copy_button('id', timeout=hostile)",
    ],
)
def test_copy_button_keeps_every_value_out_of_javascript(call: str) -> None:
    elements = _elements(
        f'{{% from "macros/buttons.html" import copy_button %}}{{{{ {call} }}}}'
    )

    assert not [value for value in _javascript(elements) if HOSTILE in value]
    assert [a for a in elements if HOSTILE in a.values()]


def test_copy_button_reads_what_it_needs_from_its_data_attributes() -> None:
    elements = _elements(
        '{% from "macros/buttons.html" import copy_button %}'
        "{{ copy_button('dsn-1', text='Copy', success_text='Done', timeout=1500) }}"
    )

    button = next(a for a in elements if "data-copy-target" in a)
    label = next(a for a in elements if "data-copied-label" in a)
    assert (button["data-copy-target"], button["data-copy-timeout"]) == (
        "dsn-1",
        "1500",
    )
    assert (label["data-label"], label["data-copied-label"]) == ("Copy", "Done")


def test_time_pills_keep_the_selected_period_out_of_javascript() -> None:
    elements = _elements(
        '{% from "macros/analytics.html" import time_pills %}'
        "{{ time_pills(hostile, '#page-content', '/dashboard/overview/content') }}"
    )

    assert not [value for value in _javascript(elements) if HOSTILE in value]
    assert next(a for a in elements if "data-selected" in a)["data-selected"] == HOSTILE
    assert [a["data-hours"] for a in elements if "data-hours" in a] == [
        "1",
        "6",
        "24",
        "168",
        "720",
        "2160",
        "8760",
        "0",
    ]


@pytest.mark.parametrize(
    ("arguments", "onclick", "alpine_click"),
    [
        ("", "closeModal('panel-container')", None),
        ("onclick='closeThing()'", "closeThing()", None),
        ("alpine_click='show = false'", None, "show = false"),
    ],
)
def test_the_close_button_runs_the_action_it_was_given(
    arguments: str, onclick: str | None, alpine_click: str | None
) -> None:
    elements = _elements(
        '{% from "macros/buttons.html" import icon_close_modal %}'
        f"{{{{ icon_close_modal({arguments}) }}}}"
    )

    button = elements[0]
    assert button.get("onclick") == onclick
    assert button.get("@click") == alpine_click


@pytest.mark.parametrize(
    ("source", "rgb"),
    [
        (
            '{% from "macros/badges.html" import info_box %}'
            "{% call info_box(variant='info') %}text{% endcall %}",
            "59, 130, 246",
        ),
        (
            '{% from "macros/cards.html" import stat_card %}'
            "{{ stat_card('Title', 1, color='blue') }}",
            "59, 130, 246",
        ),
    ],
)
def test_hover_glow_reads_its_color_from_a_data_attribute(
    source: str, rgb: str
) -> None:
    elements = _elements(source)

    glowing = next(a for a in elements if "onmouseenter" in a)
    assert glowing["data-rgb"] == rgb
    assert rgb not in glowing["onmouseenter"]
    assert rgb not in glowing["onmouseleave"]
    assert "this.dataset.rgb" in glowing["onmouseenter"]
    assert "this.dataset.rgb" in glowing["onmouseleave"]
