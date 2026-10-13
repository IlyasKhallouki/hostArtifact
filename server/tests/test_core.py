import pytest

from publisher.html_utils import apply_edits, normalize
from publisher.models import Edit, PublisherError
from publisher.slugs import slugify, token


def test_normalization_and_atomic_edits():
    page = normalize("<p>Hello</p>", "Test <title>", 2000)
    assert "&lt;title&gt;" in page
    assert apply_edits(page, [Edit(find="Hello", replace="World")]).count("World") == 1
    with pytest.raises(PublisherError, match="matched 0"):
        apply_edits(page, [Edit(find="missing", replace="x")])
    with pytest.raises(PublisherError, match="TOO_LARGE"):
        normalize("a" * 500, "Title", 100)
    with pytest.raises(PublisherError, match="only work inside Claude"):
        normalize("<script>window.claude()</script>", "Title", 2000)


def test_slugs():
    assert slugify("Acme Élite!") == "acme-elite"
    assert slugify("mcp") != "mcp"
    assert len(token()) == 10
