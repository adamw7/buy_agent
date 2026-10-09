"""Page fetching and condensing, with httpx stubbed out."""

from __future__ import annotations

import contextlib
import importlib
import logging
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar

import httpx
import pytest

import buy_agent.fetch as fetch_module
from buy_agent import money
from buy_agent.cache import DiskCache
from buy_agent.fetch import (
    _MAX_RETRY_WAIT,
    _MAX_SEGMENT,
    _RETRY_WAIT,
    PageText,
    condense,
    describe_failure,
    enrich,
    fetch_page,
    html_to_text,
    quotes_a_figure,
    summarise_failures,
)
from buy_agent.search import SearchResult
from tests.conftest import unencodable

PAGE = """<html><body>
<h1>Best headphones</h1><script>var tracking = 1;</script><style>p {color: red}</style>
<div><h2>Sony WH-CH720N</h2><span>Now $129.99, rated 4.3 out of 5</span></div>
<p>Free shipping on all orders, no minimum</p>
<ul><li>JBL Tune 770NC</li><li>$99.95 (4.1/5 from 2,300 reviews)</li></ul>
<footer>Copyright 2026 AudioSite. All rights reserved.</footer>
</body></html>"""


def test_scripts_and_styles_are_stripped() -> None:
    text = html_to_text(PAGE)

    assert "tracking" not in text
    assert "color: red" not in text
    assert "Sony WH-CH720N" in text


def test_a_page_that_opens_with_an_xml_declaration_is_still_read() -> None:
    """XHTML pages carry one, and lxml refuses a *str* that does."""
    text = html_to_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        "<h2>Sony WH-CH720N</h2><span>$129.99</span></body></html>"
    )

    assert text.split() == ["Sony", "WH-CH720N", "$129.99"], "nothing of the declaration"


def test_a_line_as_short_as_the_floor_is_still_a_line() -> None:
    """The floor is the shortest line kept, not the longest dropped: "$129" is a whole
    price."""
    assert condense("$129", max_chars=1000) == "$129"


def test_repeated_lines_appear_once() -> None:
    repeated = "Sony WH-CH720N\n$129.00\n" * 5

    assert condense(repeated, max_chars=1000).count("$129.00") == 1


def test_the_same_figure_under_two_names_is_two_figures() -> None:
    """A repeat is the same words about the same thing, not the same words. Two
    products a page prices alike print that figure twice, and deduplicating by the
    words alone dropped the second -- leaving its name standing over the price of
    whatever came next."""
    page = "Sony WH-1000XM5\n$349.00\nBose QuietComfort Ultra\n$349.00"

    assert condense(page, max_chars=1000).splitlines() == [
        "Sony WH-1000XM5",
        "$349.00",
        "Bose QuietComfort Ultra",
        "$349.00",
    ]


def fake_client(handler, captured: dict | None = None):
    """A stand-in for ``httpx.Client`` that answers ``handler``."""

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            if captured is not None:
                captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

        @contextlib.contextmanager
        def stream(self, method: str, url: str):
            assert method == "GET"
            yield handler(url)

    return FakeClient


def stub_client(monkeypatch, handler) -> None:
    """Point buy_agent.fetch at a fake httpx client."""
    monkeypatch.setattr("buy_agent.fetch.httpx.Client", fake_client(handler))


def make_response(
    url: str,
    body: str,
    *,
    status: int = 200,
    content_type: str = "text/html",
    retry_after: str | None = None,
):
    headers = {"content-type": content_type}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    return httpx.Response(
        status, text=body, headers=headers, request=httpx.Request("GET", url),
    )


def one_reachable_one_not(monkeypatch) -> list[SearchResult]:
    """Two results, of which the second's page refuses the connection."""

    def handler(url: str):
        if "bad" in url:
            raise httpx.ConnectError("refused")
        return make_response(url, PAGE)

    stub_client(monkeypatch, handler)
    return [
        SearchResult(title="ok", url="https://good.example"),
        SearchResult(title="down", url="https://bad.example"),
    ]


def answering(*responses):
    """A handler giving each answer in turn, and the list of what it was asked."""
    answers = list(responses)
    asked: list[str] = []

    def handler(url: str):
        asked.append(url)
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return handler, asked


def test_a_malformed_href_does_not_bring_the_run_down() -> None:
    """The real client, on the real parse, with no stub in the way."""
    results = [
        SearchResult(title="bad port", url="http://[::1/", snippet="s"),
        SearchResult(title="bad idna", url="http://\udcff.example/", snippet="s"),
    ]

    enriched = enrich(results, max_chars=1000, timeout=0.01)

    assert [result.content for result in enriched] == ["", ""]
    assert [result.title for result in enriched] == ["bad port", "bad idna"]


def test_non_html_responses_are_ignored(monkeypatch) -> None:
    stub_client(
        monkeypatch, lambda url: make_response(url, "%PDF-1.4", content_type="application/pdf")
    )
    with httpx.Client() as client:
        page = fetch_page(client, "https://manual.example/x.pdf", max_chars=1000)

    assert page == PageText("", "did not answer with HTML")


def test_the_price_pattern_is_built_from_the_currency_tables() -> None:
    """Derived, not copied (ADR-0054)."""
    assert not quotes_a_figure("it costs 42 QUATLOOS")

    original = money.WORDS
    try:
        money.WORDS = (*original, "QUATLOOS")
        assert importlib.reload(fetch_module).quotes_a_figure("it costs 42 QUATLOOS")
    finally:
        money.WORDS = original
        importlib.reload(fetch_module)

    assert not fetch_module.quotes_a_figure("it costs 42 QUATLOOS")


def test_a_response_without_a_content_type_is_read_as_html(monkeypatch) -> None:
    """Shops that omit the header still serve pages worth reading."""
    stub_client(
        monkeypatch,
        lambda url: httpx.Response(
            200, content=PAGE.encode(), request=httpx.Request("GET", url)
        ),
    )

    with httpx.Client() as client:
        assert "$129.99" in fetch_page(client, "https://shop.example", max_chars=1000).text


def test_enrich_reports_how_many_pages_were_usable(monkeypatch, caplog) -> None:
    results = one_reachable_one_not(monkeypatch)

    with caplog.at_level(logging.INFO, logger="buy_agent.fetch"):
        enrich(results, max_chars=1000)

    assert "Got usable page text from 1 of 2 result(s)" in caplog.text
    assert "1 could not be reached" in caplog.text


def test_a_tally_with_nothing_going_wrong_ends_at_the_count(monkeypatch, caplog) -> None:
    """No colon introducing a list of failures that is not there."""
    stub_client(monkeypatch, lambda url: make_response(url, PAGE))

    with caplog.at_level(logging.INFO, logger="buy_agent.fetch"):
        enrich([SearchResult(url="https://good.example")], max_chars=1000)

    assert [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Got usable page text")
    ] == ["Got usable page text from 1 of 1 result(s)"]


# -- why the pages that yielded nothing yielded nothing --------------------------


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ConnectTimeout("slow"), "timed out"),
        (httpx.ReadTimeout("slow"), "timed out"),
        (httpx.TooManyRedirects("round and round"), "redirected in a loop"),
        (httpx.InvalidURL("bad port"), "had an address that cannot be fetched"),
        (httpx.UnsupportedProtocol("gopher://"), "had an address that cannot be fetched"),
        (unencodable("shop..example"), "had an address that cannot be fetched"),
        (httpx.ConnectError("refused"), "could not be reached"),
        (httpx.ReadError("reset"), "could not be reached"),
        (httpx.ProxyError("no proxy"), "could not be reached"),
        (httpx.RemoteProtocolError("garbage"), "failed mid-transfer"),
        (httpx.DecodingError("bad gzip"), "failed mid-transfer"),
    ],
)
def test_each_kind_of_failure_gets_its_own_words(exc: Exception, expected: str) -> None:
    """Grouped by what it means, not by httpx's class tree."""
    assert describe_failure(exc) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "refused (401)"),
        (403, "refused (403)"),
        (404, "not found (404)"),
        (410, "gone (410)"),
        (429, "rate-limited (429)"),
        (402, "rejected (402)"),
        (500, "failed (500)"),
        (503, "failed (503)"),
    ],
)
def test_a_status_code_is_named_and_quoted(status: int, expected: str) -> None:
    """The word says what it means; the number is there for the codes it does not."""
    response = make_response("https://shop.example", "", status=status)
    exc = httpx.HTTPStatusError("nope", request=response.request, response=response)

    assert describe_failure(exc) == expected


def test_the_kinds_of_failure_are_counted_commonest_first() -> None:
    problems = ["timed out", "refused (403)", "refused (403)", "timed out", "refused (403)"]

    assert summarise_failures(problems) == "3 refused (403), 2 timed out"


def test_reading_nothing_at_all_is_a_warning(monkeypatch, caplog) -> None:
    """Every figure in the report ahead is about to be blanked by grounding, so
    this is the run saying why -- not another step of its narration."""

    def explode(url: str):
        raise httpx.ConnectError("refused")

    stub_client(monkeypatch, explode)

    with caplog.at_level(logging.INFO, logger="buy_agent.fetch"):
        enrich([SearchResult(url="https://bad.example")], max_chars=1000)

    tally = [record for record in caplog.records if "Got usable page text" in record.message]
    assert [record.levelno for record in tally] == [logging.WARNING]


def test_fetching_nothing_is_not_a_failure_to_fetch(monkeypatch, caplog) -> None:
    """No results is the search's news to report, not the fetcher's."""
    stub_client(monkeypatch, lambda url: make_response(url, PAGE))

    with caplog.at_level(logging.INFO, logger="buy_agent.fetch"):
        enrich([], max_chars=1000)

    tally = [record for record in caplog.records if "Got usable page text" in record.message]
    assert [record.levelno for record in tally] == [logging.INFO]


def test_a_url_that_could_not_be_fetched_is_still_named_at_debug(monkeypatch, caplog) -> None:
    """The tally is the summary; which page it was stays where it was."""

    def explode(url: str):
        raise httpx.ConnectError("refused")

    stub_client(monkeypatch, explode)

    with caplog.at_level(logging.DEBUG, logger="buy_agent.fetch"):
        enrich([SearchResult(url="https://bad.example")], max_chars=1000)

    assert "Could not fetch https://bad.example: refused" in caplog.text


#: Stands in for whatever the caller is carrying while it waits on ``enrich`` --
#: the browser's stream is the one that matters, and this is the shape of it.
_WATCHING: ContextVar[str | None] = ContextVar("watching", default=None)


def test_a_page_is_read_in_the_context_its_caller_is_running_in(monkeypatch) -> None:
    """This is the one step that fans out into threads, and a thread starts with a context
    of its own."""
    seen: list[str | None] = []

    def read(url: str):
        seen.append(_WATCHING.get())
        return make_response(url, PAGE)

    stub_client(monkeypatch, read)
    results = [SearchResult(url=f"https://{name}.example") for name in ("a", "b", "c")]

    token = _WATCHING.set("this run")
    try:
        enrich(results, max_chars=200, workers=3)
    finally:
        _WATCHING.reset(token)

    assert seen == ["this run"] * 3


def test_pages_are_requested_as_a_browser_would(monkeypatch) -> None:
    """Many shops answer python-httpx with a 403."""
    captured: dict = {}

    monkeypatch.setattr(
        "buy_agent.fetch.httpx.Client",
        fake_client(lambda url: make_response(url, PAGE), captured),
    )

    enrich([SearchResult(url="https://shop.example")], max_chars=100, timeout=2.5)

    assert captured["timeout"] == 2.5
    assert captured["follow_redirects"] is True
    assert "Mozilla" in captured["headers"]["User-Agent"]


def test_a_line_that_exactly_fills_the_budget_is_kept() -> None:
    """The budget is what may be spent, not what must be left over."""
    text = "Price $10\nPrice $20"
    assert len(text) == 19

    assert condense(text, max_chars=19) == text


def test_a_spent_budget_stops_the_sweep_rather_than_skipping_the_line() -> None:
    """A page is read top down, and the prompt is an excerpt rather than a best-fit
    selection."""
    text = "Price $10\nA rather longer line about this one at $20 here\nPrice $30"

    condensed = condense(text, max_chars=25)

    assert condensed == "Price $10"


def test_a_line_exactly_at_the_ceiling_is_still_a_line() -> None:
    """The ceiling excludes walls of boilerplate, and 300 characters is not one --
    the two length checks it is spelt in have to agree about that, or a page's
    longest kept line is dropped by the second after passing the first."""
    padding = "a" * (_MAX_SEGMENT - len("Sony WH-CH720N costs $129.00 ") - 1)
    line = f"Sony WH-CH720N costs $129.00 {padding}."
    assert len(line) == _MAX_SEGMENT

    assert condense(line, max_chars=1000) == line


def test_the_budget_counts_the_newline_between_segments() -> None:
    """The budget is for the text as joined, so each segment costs its newline too."""
    text = "Price $10\nPrice $20\nPrice $30"

    assert condense(text, max_chars=28).splitlines() == ["Price $10", "Price $20"]
    assert len(condense(text, max_chars=28)) <= 28


def test_pages_are_asked_for_in_english(monkeypatch) -> None:
    """A shop that answers in the local language yields prices in another currency."""
    captured: dict = {}

    monkeypatch.setattr(
        "buy_agent.fetch.httpx.Client",
        fake_client(lambda url: make_response(url, PAGE), captured),
    )

    enrich([SearchResult(url="https://shop.example")], max_chars=100)

    assert captured["headers"]["Accept-Language"].startswith("en")


def test_the_fetch_defaults_are_the_ones_the_agent_relies_on(monkeypatch) -> None:
    """The eight-second cap and the eight-way pool are the defaults, not just kwargs."""
    captured: dict = {}

    pool = ThreadPoolExecutor

    def record_pool(*, max_workers: int, **kwargs):
        captured["max_workers"] = max_workers
        return pool(max_workers=max_workers, **kwargs)

    monkeypatch.setattr(
        "buy_agent.fetch.httpx.Client",
        fake_client(lambda url: make_response(url, PAGE), captured),
    )
    monkeypatch.setattr("buy_agent.fetch.ThreadPoolExecutor", record_pool)

    enrich([SearchResult(url="https://shop.example")], max_chars=100)

    assert captured["timeout"] == 8.0
    assert captured["max_workers"] == 8


# -- the opinions ---------------------------------------------------------------

REVIEW = """Sony WH-CH720N
Now $129.00
Reviewers found the noise cancelling uncanny for the money.
Free returns within 30 days of delivery
The downside is a case too bulky for a coat pocket.
Copyright 2026 AudioSite. All rights reserved.
"""


def test_leaving_the_opinions_unread_reaches_the_pages(monkeypatch) -> None:
    """The other half of that: a budget of nothing is passed on as nothing, rather than
    each page falling back to the default and reading them anyway."""
    stub_client(monkeypatch, lambda url: make_response(url, f"<p>{REVIEW}</p>"))

    enriched = enrich(
        [SearchResult(url="https://audiosite.example")], max_chars=1000, opinion_chars=0
    )

    assert "$129.00" in enriched[0].content
    assert "uncanny" not in enriched[0].content


#: A page arriving in three pieces, each a line with a price of its own.
_CHUNKS = (b"<p>Sony WH-CH720N $129.00</p>", b"<p>Bose QC Ultra $379.00</p>", b"<p>JBL $99.00</p>")


@pytest.mark.parametrize(
    ("ceiling", "third_read"),
    [
        # Exactly the first two pieces: reached, so the third is never asked for...
        (len(_CHUNKS[0]) + len(_CHUNKS[1]), False),
        # ...and a byte more than them: not yet reached, so it is.
        (len(_CHUNKS[0]) + len(_CHUNKS[1]) + 1, True),
    ],
)
def test_the_ceiling_is_counted_across_every_piece_that_arrived(
    monkeypatch, ceiling: int, third_read: bool
) -> None:
    """Every byte counted once, from nothing, until the total reaches the ceiling --
    which one piece at a time is the only way a page larger than it arrives."""
    monkeypatch.setattr("buy_agent.fetch._MAX_PAGE_BYTES", ceiling)
    stub_client(
        monkeypatch,
        lambda url: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=iter(_CHUNKS),
            request=httpx.Request("GET", url),
        ),
    )

    with httpx.Client() as client:
        page = fetch_page(client, "https://huge.example", max_chars=1000)

    assert "$379.00" in page.text
    assert ("$99.00" in page.text) is third_read


def test_the_pieces_of_a_page_are_joined_with_nothing_between_them(monkeypatch) -> None:
    """A network hands a page over wherever its packets fall, the middle of a price
    included."""
    stub_client(
        monkeypatch,
        lambda url: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=iter((b"<p>Sony WH-CH720N $12", b"9.00</p>")),
            request=httpx.Request("GET", url),
        ),
    )

    with httpx.Client() as client:
        page = fetch_page(client, "https://split.example", max_chars=1000)

    assert "Sony WH-CH720N $129.00" in page.text


def test_a_byte_the_declared_encoding_lacks_does_not_cost_it_the_page(monkeypatch) -> None:
    """Windows-1252 leaves five bytes unassigned. One of them in a footer is replaced
    where it stands, rather than throwing the whole page over to UTF-8, which cannot
    read its accents either."""
    stub_client(
        monkeypatch,
        lambda url: httpx.Response(
            200,
            headers={"content-type": "text/html; charset=windows-1252"},
            content="<p>Café Noir espresso machine £129.00</p>".encode("windows-1252")
            + b"<p>\x81</p>",
            request=httpx.Request("GET", url),
        ),
    )

    with httpx.Client() as client:
        page = fetch_page(client, "https://cafe.example", max_chars=1000)

    assert "Café Noir espresso machine £129.00" in page.text


@pytest.mark.parametrize("charset", ["base64", "zlib", "idna", "undefined"])
def test_a_charset_that_reads_no_text_is_read_as_utf8(monkeypatch, charset: str) -> None:
    """httpx accepts any name ``codecs`` can find, and these refuse to decode text even
    replacing: one page declaring one ended the run -- as a 500 where the refusal was a
    ``LookupError``, none of the run's three failures. Read as UTF-8, a stray byte is
    replaced there too, rather than raised past every handler."""
    stub_client(
        monkeypatch,
        lambda url: httpx.Response(
            200,
            headers={"content-type": f"text/html; charset={charset}"},
            content="<p>Café Noir espresso machine €129.00</p>".encode("utf-8")
            + b"<p>\xff</p>",
            request=httpx.Request("GET", url),
        ),
    )

    with httpx.Client() as client:
        page = fetch_page(client, "https://odd.example", max_chars=1000)

    assert "Café Noir espresso machine €129.00" in page.text


# -- the page cache, where a run reads the pages it read last time (ADR-0040) ---


def test_markup_that_will_not_parse_is_neither_stored_nor_called_a_failure(
    monkeypatch, tmp_path
) -> None:
    """Empty text with no phrase beside it would break ``PageText``'s own rule --
    and would put an empty entry in the cache for a page nobody could read."""
    cache = DiskCache(tmp_path, ttl=3600)
    stub_client(monkeypatch, lambda url: make_response(url, "   "))

    with httpx.Client() as client:
        page = fetch_page(client, "https://empty.example", max_chars=1000, cache=cache)

    assert page == PageText("", "quoted no prices and no verdicts")
    assert cache.get("https://empty.example") is None


def test_a_cached_page_with_nothing_worth_keeping_is_still_marked_cached(
    monkeypatch, tmp_path
) -> None:
    """The tally counts what was read off disk, not what survived condensing."""
    cache = DiskCache(tmp_path, ttl=3600)
    cache.put("https://about.example", "About us. Our story. Careers.")
    stub_client(monkeypatch, _refuses_to_be_called)

    with httpx.Client() as client:
        page = fetch_page(client, "https://about.example", max_chars=1000, cache=cache)

    assert page == PageText("", "quoted no prices and no verdicts", True)


def test_enrich_says_how_many_pages_came_off_disk(monkeypatch, tmp_path, caplog) -> None:
    """A run that read nine of its ten pages out of the cache took seconds where
    it takes a minute, and this line is what tells that from a fast web."""
    monkeypatch.setenv("BUY_AGENT_CACHE_DIR", str(tmp_path))
    stub_client(monkeypatch, lambda url: make_response(url, PAGE))
    results = [
        SearchResult(title="t", url="https://shop.example", snippet="s"),
        SearchResult(title="t", url="https://other.example", snippet="s"),
    ]
    enrich(results, max_chars=1000, cache_ttl=3600)

    with caplog.at_level(logging.INFO, logger="buy_agent.fetch"):
        enrich(results, max_chars=1000, cache_ttl=3600)

    assert "from 2 of 2 result(s), 2 from cache" in caplog.text


def _refuses_to_be_called(url: str):
    """A transport nothing may reach: a cache hit must not open a socket."""
    raise AssertionError(f"{url} was fetched when it should have come off the cache")


def test_a_rate_limited_page_is_asked_again_after_the_wait_it_asked_for(monkeypatch) -> None:
    """A 429 is "later", not "no" -- and the shop said how much later (ADR-0053)."""
    handler, asked = answering(
        make_response("https://shop.example", PAGE, status=429, retry_after="2"),
        make_response("https://shop.example", PAGE),
    )
    stub_client(monkeypatch, handler)
    waits: list[float] = []

    with httpx.Client() as client:
        page = fetch_page(client, "https://shop.example", max_chars=1000, wait=waits.append)

    assert "$129.99" in page.text
    assert page.problem is None
    assert waits == [2.0], "the wait the shop asked for, and one of them"
    assert asked == ["https://shop.example"] * 2, "and the second time, the same page"


@pytest.mark.parametrize(
    ("asked_for", "waited"),
    [
        ("3600", _MAX_RETRY_WAIT),  # an hour is a page to go without
        ("-5", 0.0),  # whatever a shop sends, this is a wait
        ("Wed, 21 Oct 2026 07:28:00 GMT", _RETRY_WAIT),  # the date form needs a clock
        ("soon", _RETRY_WAIT),  # and so does nonsense, to the same answer
        # ``float`` reads this one, and then it passes the floor and the cap alike --
        # every comparison against a NaN being false -- and reaches ``time.sleep``,
        # which refuses it and ends the run over one shop's header.
        ("nan", _RETRY_WAIT),
    ],
)
def test_what_a_retry_after_header_is_allowed_to_ask_for(
    monkeypatch, asked_for: str, waited: float
) -> None:
    """The header is whatever the shop chose to send, so every shape of it has an
    answer here -- and none of them is "wait as long as you are told"."""
    handler, _ = answering(
        make_response("https://shop.example", PAGE, status=429, retry_after=asked_for),
        make_response("https://shop.example", PAGE),
    )
    stub_client(monkeypatch, handler)
    waits: list[float] = []

    with httpx.Client() as client:
        fetch_page(client, "https://shop.example", max_chars=1000, wait=waits.append)

    assert waits == [waited]


@pytest.mark.parametrize(
    "answer",
    [
        make_response("https://shop.example", PAGE, status=403),
        make_response("https://shop.example", PAGE, status=404),
        httpx.ConnectError("refused"),
        httpx.ConnectTimeout("too slow"),
    ],
)
def test_an_answer_that_is_not_come_back_later_is_asked_once(monkeypatch, answer) -> None:
    """A refusal, a missing page and a server that is not there are all answers."""
    handler, asked = answering(answer, make_response("https://shop.example", PAGE))
    stub_client(monkeypatch, handler)
    waits: list[float] = []

    with httpx.Client() as client:
        page = fetch_page(client, "https://shop.example", max_chars=1000, wait=waits.append)

    assert page.text == ""
    assert waits == []
    assert len(asked) == 1


def test_the_second_answer_is_the_last_one(monkeypatch) -> None:
    """Asked again and refused again, the page is gone and says how."""
    handler, asked = answering(
        make_response("https://shop.example", PAGE, status=429),
        httpx.ConnectError("refused"),
    )
    stub_client(monkeypatch, handler)

    with httpx.Client() as client:
        page = fetch_page(client, "https://shop.example", max_chars=1000, wait=lambda _: None)

    assert page == PageText("", "could not be reached")
    assert len(asked) == 2


def test_the_waiting_is_said_out_loud(monkeypatch, caplog) -> None:
    """At INFO, unlike every other per-page line: this one is time the shopper is
    spending rather than a page they are not getting."""
    handler, _ = answering(
        make_response("https://shop.example", PAGE, status=429, retry_after="2"),
        make_response("https://shop.example", PAGE),
    )
    stub_client(monkeypatch, handler)

    with caplog.at_level(logging.INFO), httpx.Client() as client:
        fetch_page(client, "https://shop.example", max_chars=1000, wait=lambda _: None)

    assert "asked to be tried again; waiting 2.0s" in caplog.text


def test_enrich_hands_every_page_the_wait(monkeypatch) -> None:
    """The whole point of the parameter: ``BuyAgent`` passes one clock and every
    page in the pool fetches by it."""
    handler, asked = answering(
        make_response("https://a.example", PAGE, status=429),
        make_response("https://a.example", PAGE),
    )
    stub_client(monkeypatch, handler)
    waits: list[float] = []

    enriched = enrich(
        [SearchResult(url="https://a.example")], max_chars=1000, wait=waits.append
    )

    assert "$129.99" in enriched[0].content
    assert waits == [_RETRY_WAIT]
    assert len(asked) == 2


# -- what a page declares (ADR-0078) and how a listing stands (ADR-0079) -------


DECLARING = """<html><head>
<script type="Application/LD+JSON">
{"@type": "Product", "name": "Sony WH-1000XM5",
 "offers": {"@type": "Offer", "price": 348, "priceCurrency": "USD",
            "availability": "https://schema.org/OutOfStock"}}
</script>
<script>var tracking = "Sony WH-1000XM5: 1.00 USD";</script>
</head><body><p>Our review of the headphones.</p></body></html>"""


def test_what_a_page_declares_comes_first_and_its_scripts_do_not() -> None:
    """First, so the budget reaches it first; a script's own text never shows."""
    lines = html_to_text(DECLARING).splitlines()

    assert lines[0] == "Sony WH-1000XM5: 348.00 USD, out of stock"
    assert "Our review of the headphones." in lines
    assert not any("tracking" in line for line in lines)


def test_a_page_declaring_nothing_reads_as_it_did() -> None:
    assert html_to_text("<p>Hello</p>") == "Hello"


def test_a_line_saying_how_a_listing_stands_is_kept_with_the_line_above() -> None:
    text = "Sony WH-1000XM5\nOut of stock\nShipping and returns\nRefurbished by the maker"

    kept = condense(text, max_chars=1200).splitlines()

    assert kept == ["Sony WH-1000XM5", "Out of stock", "Shipping and returns", "Refurbished by the maker"]
    assert quotes_a_figure("In stock")
    assert not quotes_a_figure("Shipping and returns")
