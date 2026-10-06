"""Reading a trusted source out of what a shopper wrote, and holding results to it."""

from __future__ import annotations

import pytest

from buy_agent.sources import (
    format_sources,
    parse_named_sources,
    parse_source,
    parse_sources,
)


# -- what a source may look like -----------------------------------------------


@pytest.mark.parametrize(
    ("spec", "domain", "term"),
    [
        ("rtings.com", "rtings.com", ""),
        ("RTINGS.COM", "rtings.com", ""),
        ("www.rtings.com", "rtings.com", ""),
        ("rtings.com/", "rtings.com", ""),
        ("rtings.com/headphones", "rtings.com", "headphones"),
        ("https://www.rtings.com/headphones/reviews/best", "rtings.com", "headphones"),
        ("http://rtings.com", "rtings.com", ""),
        ("//rtings.com/headphones", "rtings.com", "headphones"),
        ("rtings.com:443/headphones", "rtings.com", "headphones"),
        # Credentials sit in front of the host and name a *reader*, not a site.
        ("https://shopper@rtings.com/headphones", "rtings.com", "headphones"),
        ("https://shopper:hunter2@www.rtings.com:443/headphones", "rtings.com", "headphones"),
        ("shop.example.co.uk", "shop.example.co.uk", ""),
    ],
)
def test_a_site_is_read_down_to_its_domain_and_its_section(spec, domain, term) -> None:
    source = parse_source(spec)

    assert (source.domain, source.term) == (domain, term)
    assert source.spec == spec.strip()


def test_a_query_string_names_a_page_and_is_not_part_of_the_source() -> None:
    assert parse_source("youtube.com/channel/UC123?tab=videos#top").term == "UC123"


def test_a_fragment_names_a_place_on_a_page_and_is_not_part_of_the_source() -> None:
    """With no query string ahead of it, which is the case the line above cannot
    tell apart from one that splits on ``?`` alone."""
    assert parse_source("rtings.com/headphones#reviews").term == "headphones"


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "   ",
        "Marques Brownlee",
        "rtings",
        "the review site with the good graphs",
        "/headphones",
        "-rtings.com",
        "https://",
    ],
)
def test_something_that_names_no_site_is_refused_with_the_shapes_that_work(spec) -> None:
    """A source has to be searchable. A famous name is not, however well known."""
    with pytest.raises(ValueError) as failure:
        parse_source(spec)

    assert "blank" in str(failure.value) or "rtings.com" in str(failure.value)


# -- narrowing the search ------------------------------------------------------


# -- and holding the results to it ---------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://notrtings.com/headphones",
        "https://rtings.com.phishing.example/x",
        "https://example.com/rtings.com",
        "",
        "not a url at all",
        # An unbracketed IPv6 literal: urlsplit raises rather than answering.
        "http://[::1/headphones",
    ],
)
def test_a_page_from_anywhere_else_is_not(url) -> None:
    """Whole labels, not substrings: a domain inside another domain is another site."""
    assert not parse_source("rtings.com").covers(url)


# -- several of them -----------------------------------------------------------


def test_a_separator_in_front_of_the_first_one_names_nothing_and_stops_nothing() -> None:
    """``, rtings.com`` is a field somebody started typing into after a comma, and
    the empty piece in front of it is skipped rather than read as the end."""
    assert [source.domain for source in parse_sources(", rtings.com @mkbhd")] == [
        "rtings.com",
        "youtube.com",
    ]


@pytest.mark.parametrize("spec", ["", "   ", ",", " , , ", [""], ["", "  "], []])
def test_asking_to_narrow_to_nothing_is_a_refusal(spec: str | list[str]) -> None:
    """The widening half of the hole ``@`` went through, and the worse half."""
    with pytest.raises(ValueError, match="cannot be blank"):
        parse_named_sources(spec)


def test_sources_are_written_back_the_way_they_were_given() -> None:
    """What the web form is handed to put in its field, and would send back."""
    specs = "rtings.com @mkbhd"

    assert format_sources(parse_sources(specs)) == specs


@pytest.mark.parametrize("spec", ["@", "@@@", "@/", "@/videos"])
def test_an_at_sign_naming_no_channel_is_not_a_source(spec: str) -> None:
    """The one shape that used to name its site without naming anything on it."""
    with pytest.raises(ValueError, match="does not name a source"):
        parse_source(spec)


@pytest.mark.parametrize("spec", ["@mkbhd", "@mkbhd/videos", "@marques.brownlee", "@a_b-1"])
def test_a_handle_that_names_a_channel_is_still_a_source(spec: str) -> None:
    source = parse_source(spec)

    assert source.domain == "youtube.com"
    assert source.term == spec.split("/")[0]
