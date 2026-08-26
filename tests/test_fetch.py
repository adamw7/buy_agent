"""Page fetching and condensing, with httpx stubbed out."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from buy_agent.fetch import condense, enrich, fetch_page, html_to_text
from buy_agent.search import SearchResult

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


def test_elements_stay_on_separate_lines() -> None:
    """text_content() would glue the product name onto its price."""
    assert "Sony WH-CH720N\n" in html_to_text(PAGE)


def test_unparseable_markup_yields_no_text() -> None:
    assert html_to_text("") == ""


def test_only_lines_with_figures_are_kept() -> None:
    condensed = condense(html_to_text(PAGE), max_chars=1000)

    assert "$129.99" in condensed
    assert "$99.95" in condensed
    assert "Free shipping" not in condensed
    assert "All rights reserved" not in condensed


def test_the_line_above_a_price_is_kept_as_context() -> None:
    """A price is useless without the product name it sits under."""
    condensed = condense(html_to_text(PAGE), max_chars=1000)

    assert "Sony WH-CH720N" in condensed
    assert "JBL Tune 770NC" in condensed


def test_the_character_budget_is_respected() -> None:
    assert len(condense(html_to_text(PAGE), max_chars=50)) <= 51


def test_repeated_lines_appear_once() -> None:
    repeated = "Sony WH-CH720N\n$129.00\n" * 5

    assert condense(repeated, max_chars=1000).count("$129.00") == 1


def test_boilerplate_without_figures_condenses_to_nothing() -> None:
    assert condense("About us\nContact\nPrivacy policy\n", max_chars=1000) == ""


def stub_client(monkeypatch, handler) -> None:
    """Point buy_agent.fetch at a fake httpx client."""

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

        def get(self, url: str):
            return handler(url)

    monkeypatch.setattr("buy_agent.fetch.httpx.Client", FakeClient)


def make_response(url: str, body: str, *, status: int = 200, content_type: str = "text/html"):
    return httpx.Response(
        status, text=body, headers={"content-type": content_type},
        request=httpx.Request("GET", url),
    )


def test_fetch_page_condenses_a_live_page(monkeypatch) -> None:
    stub_client(monkeypatch, lambda url: make_response(url, PAGE))
    with httpx.Client() as client:
        assert "$129.99" in fetch_page(client, "https://shop.example", max_chars=1000)


def test_a_failed_request_yields_no_content(monkeypatch) -> None:
    def explode(url: str):
        raise httpx.ConnectTimeout("too slow")

    stub_client(monkeypatch, explode)
    with httpx.Client() as client:
        assert fetch_page(client, "https://slow.example", max_chars=1000) == ""


def test_a_url_httpx_will_not_parse_yields_no_content(monkeypatch) -> None:
    """``InvalidURL`` is not an ``HTTPError``, so it needs naming separately.

    httpx raises it out of parsing rather than out of the transport, which puts
    it under ``Exception`` and outside the tuple that catches everything else.
    """

    def explode(url: str):
        raise httpx.InvalidURL("Invalid port: ':1'")

    stub_client(monkeypatch, explode)
    with httpx.Client() as client:
        assert fetch_page(client, "http://[::1/", max_chars=1000) == ""


def test_a_malformed_href_does_not_bring_the_run_down() -> None:
    """The real client, on the real parse, with no stub in the way.

    A DuckDuckGo href with a bad port, an unbracketed IPv6 literal or a hostname
    IDNA refuses used to escape the pool and out of ``BuyAgent.run`` -- a
    traceback on the CLI and a 500 in the browser, from a link the agent only
    ever meant to skip. Nothing here reaches the network: httpx refuses these
    while building the URL, before there is a socket to open.
    """
    results = [
        SearchResult(title="bad port", url="http://[::1/", snippet="s"),
        SearchResult(title="bad idna", url="http://\udcff.example/", snippet="s"),
    ]

    enriched = enrich(results, max_chars=1000, timeout=0.01)

    assert [result.content for result in enriched] == ["", ""]
    assert [result.title for result in enriched] == ["bad port", "bad idna"]


def test_an_error_status_yields_no_content(monkeypatch) -> None:
    stub_client(monkeypatch, lambda url: make_response(url, PAGE, status=403))
    with httpx.Client() as client:
        assert fetch_page(client, "https://blocked.example", max_chars=1000) == ""


def test_non_html_responses_are_ignored(monkeypatch) -> None:
    stub_client(
        monkeypatch, lambda url: make_response(url, "%PDF-1.4", content_type="application/pdf")
    )
    with httpx.Client() as client:
        assert fetch_page(client, "https://manual.example/x.pdf", max_chars=1000) == ""


def test_enrich_attaches_content_to_each_result(monkeypatch) -> None:
    stub_client(monkeypatch, lambda url: make_response(url, PAGE))
    results = [
        SearchResult(title="A", url="https://a.example", snippet="s"),
        SearchResult(title="B", url="https://b.example", snippet="s"),
    ]

    enriched = enrich(results, max_chars=1000)

    assert all("$129.99" in result.content for result in enriched)
    assert [result.title for result in enriched] == ["A", "B"]
    assert results[0].content == "", "the originals must not be mutated"


def test_one_unreachable_page_does_not_lose_the_others(monkeypatch) -> None:
    def handler(url: str):
        if "bad" in url:
            raise httpx.ConnectError("refused")
        return make_response(url, PAGE)

    stub_client(monkeypatch, handler)
    results = [
        SearchResult(title="ok", url="https://good.example"),
        SearchResult(title="down", url="https://bad.example"),
    ]

    enriched = enrich(results, max_chars=1000)

    assert "$129.99" in enriched[0].content
    assert enriched[1].content == ""


@pytest.mark.parametrize(
    "line",
    ["Sony WH-CH720N EUR 129 today", "Sony deal 4.5 stars", "Price: 250 PLN here"],
)
def test_prices_and_ratings_are_recognised_in_several_shapes(line: str) -> None:
    assert condense(line, max_chars=200) == line


def test_a_wall_of_text_is_dropped_even_when_it_quotes_a_price() -> None:
    """The ceiling is what keeps paragraphs of boilerplate out of the prompt."""
    wall = "Terms and conditions apply to this offer. " * 10 + "$129"
    assert len(wall) > 300

    assert condense(wall, max_chars=5000) == ""


def test_a_stray_fragment_is_too_short_to_keep() -> None:
    assert condense("$1", max_chars=100) == ""


def test_condensing_stops_once_the_budget_is_spent() -> None:
    condensed = condense("Sony WH-1\n$100.00\nJBL T7\n$200.00", max_chars=20)

    assert "$100.00" in condensed
    assert "$200.00" not in condensed


def test_nothing_to_condense_is_empty() -> None:
    assert condense("", max_chars=1000) == ""


def test_a_line_without_a_figure_is_dropped_even_next_to_one() -> None:
    """Only the immediately preceding line rides along as context."""
    condensed = condense("Unrelated banner\nSony WH-CH720N\n$129.00", max_chars=1000)

    assert "Unrelated banner" not in condensed
    assert "Sony WH-CH720N" in condensed


def test_markup_without_a_body_still_yields_its_text() -> None:
    assert "Sony XM5" in html_to_text("<div>Sony XM5</div><span>$328</span>")


def test_noscript_and_svg_are_stripped_too() -> None:
    text = html_to_text(
        "<html><body><noscript>Enable JavaScript</noscript>"
        "<svg><title>cart icon</title></svg><p>Sony XM5</p></body></html>"
    )

    assert "Enable JavaScript" not in text
    assert "cart icon" not in text
    assert "Sony XM5" in text


def test_a_response_without_a_content_type_is_read_as_html(monkeypatch) -> None:
    """Shops that omit the header still serve pages worth reading."""
    stub_client(
        monkeypatch,
        lambda url: httpx.Response(
            200, content=PAGE.encode(), request=httpx.Request("GET", url)
        ),
    )

    with httpx.Client() as client:
        assert "$129.99" in fetch_page(client, "https://shop.example", max_chars=1000)


def test_a_page_with_no_figures_yields_no_content(monkeypatch) -> None:
    stub_client(monkeypatch, lambda url: make_response(url, "<html><p>About us</p></html>"))

    with httpx.Client() as client:
        assert fetch_page(client, "https://about.example", max_chars=1000) == ""


def test_enriching_nothing_fetches_nothing(monkeypatch) -> None:
    def explode(url: str):
        raise AssertionError(f"nothing should have been fetched, got {url}")

    stub_client(monkeypatch, explode)

    assert enrich([], max_chars=1000) == []


def test_enrich_reports_how_many_pages_were_usable(monkeypatch, caplog) -> None:
    def handler(url: str):
        if "bad" in url:
            raise httpx.ConnectError("refused")
        return make_response(url, PAGE)

    stub_client(monkeypatch, handler)
    results = [
        SearchResult(title="ok", url="https://good.example"),
        SearchResult(title="down", url="https://bad.example"),
    ]

    with caplog.at_level(logging.INFO, logger="buy_agent.fetch"):
        enrich(results, max_chars=1000)

    assert "Got usable page text from 1 of 2 result(s)" in caplog.text


def test_pages_are_requested_as_a_browser_would(monkeypatch) -> None:
    """Many shops answer python-httpx with a 403."""
    captured: dict = {}

    class Recorder:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

        def get(self, url: str):
            return make_response(url, PAGE)

    monkeypatch.setattr("buy_agent.fetch.httpx.Client", Recorder)

    enrich([SearchResult(url="https://shop.example")], max_chars=100, timeout=2.5)

    assert captured["timeout"] == 2.5
    assert captured["follow_redirects"] is True
    assert "Mozilla" in captured["headers"]["User-Agent"]


def test_the_character_budget_is_per_page(monkeypatch) -> None:
    stub_client(monkeypatch, lambda url: make_response(url, PAGE))
    results = [
        SearchResult(url="https://a.example"),
        SearchResult(url="https://b.example"),
    ]

    enriched = enrich(results, max_chars=40)

    assert all(len(result.content) <= 41 for result in enriched)


def test_the_budget_counts_the_newline_between_segments() -> None:
    """The budget is for the text as joined, so each segment costs its newline too."""
    text = "Price $10\nPrice $20\nPrice $30"

    assert condense(text, max_chars=28).splitlines() == ["Price $10", "Price $20"]
    assert len(condense(text, max_chars=28)) <= 28


def test_pages_are_asked_for_in_english(monkeypatch) -> None:
    """A shop that answers in the local language yields prices in another currency."""
    captured: dict = {}

    class Recorder:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

        def get(self, url: str):
            return make_response(url, PAGE)

    monkeypatch.setattr("buy_agent.fetch.httpx.Client", Recorder)

    enrich([SearchResult(url="https://shop.example")], max_chars=100)

    assert captured["headers"]["Accept-Language"].startswith("en")


def test_the_fetch_defaults_are_the_ones_the_agent_relies_on(monkeypatch) -> None:
    """The eight-second cap and the eight-way pool are the defaults, not just kwargs.

    Every other test here passes ``timeout`` explicitly, which pins the argument
    and leaves the default free to drift -- and the default is what a caller that
    omits it actually gets.
    """
    captured: dict = {}

    class Recorder:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

        def get(self, url: str):
            return make_response(url, PAGE)

    pool = ThreadPoolExecutor

    def record_pool(*, max_workers: int, **kwargs):
        captured["max_workers"] = max_workers
        return pool(max_workers=max_workers, **kwargs)

    monkeypatch.setattr("buy_agent.fetch.httpx.Client", Recorder)
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


def test_what_the_page_says_about_a_product_is_kept_too() -> None:
    """A price says what it costs; only these lines say whether to want it."""
    condensed = condense(REVIEW, max_chars=1000)

    assert "noise cancelling uncanny" in condensed
    assert "too bulky for a coat pocket" in condensed


def test_a_line_that_is_neither_a_figure_nor_a_judgement_is_still_dropped() -> None:
    """The vocabulary is one of judgement, not of shopping: a delivery promise
    reads like a page about a product and says nothing about one."""
    condensed = condense("Free returns within 30 days of delivery", max_chars=1000)

    assert condensed == ""


def test_a_bare_pros_heading_is_too_short_to_be_an_opinion() -> None:
    """Its content is the lines below it, which no rule here brings along, so on
    its own it is a word of budget spent on nothing."""
    assert condense("Pros", max_chars=1000) == ""


def test_lines_come_back_in_the_order_the_page_had_them() -> None:
    """Two sweeps, one excerpt: read back the other way round it would suggest the
    verdicts belong to whichever product was priced last."""
    condensed = condense(REVIEW, max_chars=1000).splitlines()

    assert condensed == [
        "Sony WH-CH720N",
        "Now $129.00",
        "Reviewers found the noise cancelling uncanny for the money.",
        "Free returns within 30 days of delivery",
        "The downside is a case too bulky for a coat pocket.",
    ]


def test_the_line_above_a_verdict_is_kept_the_way_the_line_above_a_price_is() -> None:
    """Review pages put the verdict under the heading that names the product."""
    condensed = condense("Sony WH-CH720N\nWe loved the noise cancelling on these.", max_chars=100)

    assert "Sony WH-CH720N" in condensed


def test_a_page_of_prices_cannot_crowd_out_the_verdicts() -> None:
    """Separate budgets, which is the whole reason there are two sweeps: a shop
    page listing forty prices would otherwise spend the lot before the verdict."""
    prices = "\n".join(f"Model {index} costs ${index}00.00" for index in range(40))
    condensed = condense(f"{prices}\nWe found the fit uncomfortable after an hour.", max_chars=200)

    assert "uncomfortable after an hour" in condensed
    assert len(condensed) > 200


def test_the_verdicts_cannot_crowd_out_the_price_either() -> None:
    """The other direction, which is the reason the pages are fetched at all."""
    verdicts = "\n".join(
        f"Reviewers found version {index} disappointing to listen to." for index in range(40)
    )
    condensed = condense(f"{verdicts}\nSony WH-CH720N\nNow $129.00", max_chars=200)

    assert "$129.00" in condensed


def test_the_opinions_can_be_left_unread() -> None:
    """``opinion_chars=0``: back to the figures alone, for a smaller prompt."""
    condensed = condense(REVIEW, max_chars=1000, opinion_chars=0)

    assert "$129.00" in condensed
    assert "uncanny" not in condensed


def test_the_opinion_budget_is_spent_and_then_the_sweep_stops() -> None:
    verdicts = "\n".join(
        f"Reviewers found version {index} disappointing to listen to." for index in range(10)
    )

    assert len(condense(verdicts, max_chars=0, opinion_chars=120)) <= 120


def test_a_page_read_twice_is_not_quoted_twice() -> None:
    """A line that is both a price and a verdict is taken by the first sweep and
    paid for there; the second finds it already kept."""
    text = "Sony WH-CH720N\nExcellent value for money at $129.00\n"

    assert condense(text, max_chars=1000).count("$129.00") == 1


def test_the_opinion_budget_reaches_the_pages(monkeypatch) -> None:
    """``enrich`` hands each page both budgets, or the second sweep is off by default."""
    stub_client(monkeypatch, lambda url: make_response(url, f"<p>{REVIEW}</p>"))

    enriched = enrich([SearchResult(url="https://audiosite.example")], max_chars=1000)

    assert "uncanny" in enriched[0].content


def test_a_product_called_pro_is_not_mistaken_for_a_pros_list() -> None:
    """Half the products on a headphone page are a "Pro" of something."""
    assert condense("Apple AirPods Pro (2nd generation)", max_chars=1000) == ""
    assert "Pros and cons" in condense("Pros and cons of the WH-CH720N", max_chars=1000)
