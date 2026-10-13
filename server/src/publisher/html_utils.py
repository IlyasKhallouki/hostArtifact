import html as html_lib
import re

from publisher.models import Edit, PublisherError


def normalize(html: str, title: str, maximum: int) -> str:
    if not html.strip():
        raise PublisherError("INVALID_INPUT: Provide non-empty HTML.")
    if re.search(
        r"(?:window\s*\.\s*claude|window\s*\[\s*['\"]claude|window\s*\.\s*storage\s*\.)",
        html,
    ):
        raise PublisherError(
            "INVALID_INPUT: This page uses features that only work inside Claude. Remove or replace them before publishing."
        )
    if not re.search(r"<html(?:\s|>)", html, re.I):
        html = f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{html_lib.escape(title)}</title></head><body>{html}</body></html>'
    size = len(html.encode("utf-8"))
    if size > maximum:
        raise PublisherError(
            f"TOO_LARGE: HTML is {size} bytes; limit is {maximum}. Reduce embedded assets."
        )
    return html


def apply_edits(html: str, edits: list[Edit]) -> str:
    if not edits:
        raise PublisherError("INVALID_INPUT: Provide at least one edit.")
    for index, edit in enumerate(edits):
        actual = html.count(edit.find)
        if actual != edit.expected_count:
            raise PublisherError(
                f"INVALID_INPUT: Edit {index} matched {actual} times; expected {edit.expected_count}. Load the current HTML and retry."
            )
        html = html.replace(edit.find, edit.replace)
    return html
