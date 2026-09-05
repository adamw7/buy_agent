"""Reading a trusted source out of what a shopper wrote, and holding results to it."""

from __future__ import annotations

import pytest

from buy_agent.sources import (
    Source,
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
        # Left on, the host reads "shopper" -- no dot, so no site, so a URL
        # pasted straight out of an address bar was refused as naming nothing.
        ("https://shopper@rtings.com/headphones", "rtings.com", "headphones"),
        ("https://shopper:hunter2@www.rtings.com:443/headphones", "rtings.com", "headphones"),
        ("shop.example.co.uk", "shop.example.co.uk", ""),
    ],
)
def test_a_site_is_read_down_to_its_domain_and_its_section(spec, domain, term) -> None:
    source = parse_source(spec)

    assert (source.domain, source.term) == (domain, term)
    assert source.spec == spec.strip()


@pytest.mark.parametrize(
    ("spec", "term"),
    [
        ("@mkbhd", "@mkbhd"),
        ("@mkbhd/videos", "@mkbhd"),
        ("youtube.com/@mkbhd", "@mkbhd"),
        ("https://www.youtube.com/@mkbhd/videos", "@mkbhd"),
        # The three ways YouTube spells one channel. Only the last segment names it.
        ("youtube.com/c/MKBHD", "MKBHD"),
        ("youtube.com/user/marquesbrownlee", "marquesbrownlee"),
        ("youtube.com/channel/UCBJycsmduvYEL83R_U4JriQ", "UCBJycsmduvYEL83R_U4JriQ"),
    ],
)
def test_an_influencer_is_a_handle_on_the_one_site_handles_live_on(spec, term) -> None:
    """A handle names a person, and a person is not a domain -- so it is a term."""
    source = parse_source(spec)

    assert (source.domain, source.term) == ("youtube.com", term)


@pytest.mark.parametrize(
    ("spec", "term"),
    [
        ("reddit.com/r/headphones", "headphones"),
        ("https://www.reddit.com/r/BuyItForLife/top", "BuyItForLife"),
        ("reddit.com/u/someone", "someone"),
    ],
)
def test_a_subreddit_is_named_by_the_segment_after_the_route(spec, term) -> None:
    """``/r/`` routes to a subreddit the way ``/c/`` routes to a channel.

    Read as the term itself it narrowed every search on the phrase "r" and threw
    away the only word the shopper actually typed.
    """
    source = parse_source(spec)

    assert (source.domain, source.term) == ("reddit.com", term)


def test_a_subreddit_search_asks_for_the_subreddit() -> None:
    assert parse_source("reddit.com/r/headphones").site_query("anc") == (
        'anc site:reddit.com "headphones"'
    )


def test_a_query_string_names_a_page_and_is_not_part_of_the_source() -> None:
    assert parse_source("youtube.com/channel/UC123?tab=videos#top").term == "UC123"


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


def test_a_whole_site_narrows_the_query_with_the_site_operator() -> None:
    assert parse_source("rtings.com").site_query("headphones") == "headphones site:rtings.com"


def test_a_channel_goes_in_as_a_phrase_beside_the_domain() -> None:
    """There is no operator for "published by": a handle can only be searched for."""
    assert parse_source("@mkbhd").site_query("laptops") == 'laptops site:youtube.com "@mkbhd"'


# -- and holding the results to it ---------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://rtings.com/headphones/1",
        "https://www.rtings.com/headphones/1",
        "http://reviews.rtings.com/x",
        "https://RTINGS.com/x",
    ],
)
def test_a_page_on_the_domain_or_under_it_is_covered(url) -> None:
    assert parse_source("rtings.com").covers(url)


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


def test_a_handle_is_never_enforced_against_a_url() -> None:
    """A video's address says which video it is, not who published it -- so the
    domain is all that can be checked, and every video stays reachable."""
    assert parse_source("@mkbhd").covers("https://www.youtube.com/watch?v=dQw4w9WgXcQ")


# -- several of them -----------------------------------------------------------


def test_one_string_can_hold_several_however_they_are_separated() -> None:
    """Which is how the web form sends them: one field, typed by hand."""
    sources = parse_sources("rtings.com, @mkbhd\nnotebookcheck.net  ")

    assert [source.domain for source in sources] == [
        "rtings.com",
        "youtube.com",
        "notebookcheck.net",
    ]


def test_a_list_of_them_is_read_the_same_way() -> None:
    """Which is what a repeated command line flag builds up."""
    assert parse_sources(["rtings.com", "", "@mkbhd"]) == (
        Source(spec="rtings.com", domain="rtings.com"),
        Source(spec="@mkbhd", domain="youtube.com", term="@mkbhd"),
    )


def test_a_list_entry_may_itself_hold_several() -> None:
    """One ``--source`` with a comma in it gets what the form would have given,
    rather than an error about a hostname with a comma in it."""
    assert [source.domain for source in parse_sources(["a.com,b.com", "c.com"])] == [
        "a.com",
        "b.com",
        "c.com",
    ]


def test_two_spellings_of_one_source_are_one_source() -> None:
    """Otherwise the run spends a second identical search, and the other sources
    are left with half the results they were promised."""
    assert parse_sources("@mkbhd youtube.com/@mkbhd") == (
        Source(spec="@mkbhd", domain="youtube.com", term="@mkbhd"),
    )


def test_naming_none_is_not_an_error_but_the_whole_web() -> None:
    assert parse_sources("") == ()
    assert parse_sources([]) == ()


@pytest.mark.parametrize("spec", ["", "   ", ",", " , , ", [""], ["", "  "], []])
def test_asking_to_narrow_to_nothing_is_a_refusal(spec: str | list[str]) -> None:
    """The widening half of the hole ``@`` went through, and the worse half.

    A spec that identifies nothing searched for a phrase no page contains, and
    the answer was an empty report. A spec that is *blank* does not even do that:
    it comes back as no sources at all, which is the whole web -- so a shopper who
    asked for rtings.com and mistyped got facts from every site there is, reported
    as if they had asked for them. Named sources have no fall back to the wider
    web (ADR-0027), and this is the one way to reach one.
    """
    with pytest.raises(ValueError, match="cannot be blank"):
        parse_named_sources(spec)


def test_narrowing_to_a_real_source_is_the_same_answer_as_parsing_it() -> None:
    """The refusal is the only difference between the two: everything about which
    sources these are, how they are separated and which repeats collapse stays
    :func:`parse_sources`'s to answer."""
    specs = "rtings.com, @mkbhd rtings.com"

    assert parse_named_sources(specs) == parse_sources(specs)


def test_one_unusable_source_fails_the_lot_rather_than_being_dropped() -> None:
    """Silently ignoring it would search the two sites the shopper named and
    quietly leave out the third, which reads as a working run."""
    with pytest.raises(ValueError):
        parse_sources("rtings.com nonsense @mkbhd")


def test_sources_are_written_back_the_way_they_were_given() -> None:
    """What the web form is handed to put in its field, and would send back."""
    specs = "rtings.com @mkbhd"

    assert format_sources(parse_sources(specs)) == specs


def test_no_sources_is_an_empty_field_rather_than_a_word_meaning_none() -> None:
    assert format_sources(()) == ""


@pytest.mark.parametrize("spec", ["@", "@@@", "@/", "@/videos"])
def test_an_at_sign_naming_no_channel_is_not_a_source(spec: str) -> None:
    """The one shape that used to name its site without naming anything on it.

    Every other spec goes through the hostname check; this branch went through
    nothing, so ``--source @`` parsed and then searched YouTube for the literal
    phrase "@". Named sources have no fall back to the wider web (ADR-0027), so
    what the shopper got was an empty report with nothing to say about why.
    """
    with pytest.raises(ValueError, match="does not name a source"):
        parse_source(spec)


@pytest.mark.parametrize("spec", ["@mkbhd", "@mkbhd/videos", "@marques.brownlee", "@a_b-1"])
def test_a_handle_that_names_a_channel_is_still_a_source(spec: str) -> None:
    source = parse_source(spec)

    assert source.domain == "youtube.com"
    assert source.term == spec.split("/")[0]
