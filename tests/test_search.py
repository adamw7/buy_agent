"""The table a search backend is one row of, with the network stubbed out (ADR-0057)."""

from __future__ import annotations

import logging

import httpx
import pytest
from ddgs.exceptions import DDGSException

from buy_agent.search import (
    _NO_RESULTS,
    _RETRY_WAIT,
    BACKENDS,
    BRAVE,
    DDG,
    SEARXNG,
    Backend,
    Query,
    SearchError,
    SearchResult,
    backend_for,
    backend_options,
    search_web,
)


def stub_ddgs(monkeypatch, *, results=None, error: Exception | None = None) -> dict:
    """Point search_web at a fake backend and return the call it records."""
    seen: dict = {}

    class FakeDDGS:
        def text(self, query: str, **kwargs) -> list[dict]:
            seen.update({"query": query, **kwargs})
            if error is not None:
                raise error
            return results or []

    monkeypatch.setattr("buy_agent.search.DDGS", FakeDDGS)
    return seen


def test_raw_results_are_mapped_onto_search_result(monkeypatch) -> None:
    stub_ddgs(
        monkeypatch,
        results=[{"title": "Sony XM5", "href": "https://shop/x", "body": "$328"}],
    )

    results = search_web("headphones")

    assert results[0].title == "Sony XM5"
    assert results[0].url == "https://shop/x"
    assert results[0].snippet == "$328"


def test_missing_fields_become_empty_strings(monkeypatch) -> None:
    stub_ddgs(monkeypatch, results=[{}])

    result = search_web("headphones")[0]

    assert (result.title, result.url, result.snippet) == ("", "", "")


def test_a_null_field_is_an_empty_string_and_not_the_word_none(monkeypatch) -> None:
    """A JSON ``null`` is a key that is there: read with a default it became "None", a
    title the model reads and an address the fetcher asks for."""
    stub_ddgs(monkeypatch, results=[{"title": None, "href": None, "body": None}])

    result = search_web("headphones")[0]

    assert (result.title, result.url, result.snippet) == ("", "", "")


def test_search_arguments_reach_the_backend(monkeypatch) -> None:
    seen = stub_ddgs(monkeypatch)

    search_web("laptops", max_results=4, region="pl-pl")

    assert seen == {"query": "laptops", "max_results": 4, "region": "pl-pl"}


def test_backend_failures_become_search_error(monkeypatch) -> None:
    stub_ddgs(monkeypatch, error=DDGSException("rate limit"))

    with pytest.raises(SearchError, match="rate limit"):
        search_web("headphones")


def test_prompt_block_shows_title_url_and_snippet(monkeypatch) -> None:
    stub_ddgs(monkeypatch, results=[{"title": "T", "href": "U", "body": "S"}])

    block = search_web("x")[0].as_prompt_block()

    assert block == "TITLE: T\nURL: U\nSNIPPET: S"


def test_the_prompt_block_includes_the_fetched_page_text() -> None:
    block = SearchResult(
        title="T", url="U", snippet="S", content="JBL Live 780NC\n$149"
    ).as_prompt_block()

    assert block == "TITLE: T\nURL: U\nSNIPPET: S\nPAGE:\nJBL Live 780NC\n$149"


def test_a_fresh_result_carries_no_page_content() -> None:
    """content is filled in later by fetch.enrich, if at all."""
    assert SearchResult(title="T").content == ""


def test_finding_nothing_is_an_empty_list_not_a_failure(monkeypatch) -> None:
    stub_ddgs(monkeypatch, results=[])

    assert search_web("something nobody sells") == []


def test_the_way_ddgs_itself_spells_finding_nothing_is_not_a_failure(monkeypatch) -> None:
    """ddgs raises rather than returning [], and that is not a backend failure."""
    stub_ddgs(monkeypatch, error=DDGSException(_NO_RESULTS))

    assert search_web("something nobody sells") == []


def test_a_real_backend_failure_is_still_a_failure(monkeypatch) -> None:
    """The message is the discriminator, so anything else keeps raising."""
    stub_ddgs(monkeypatch, error=DDGSException("No results found. (engine timed out)"))

    with pytest.raises(SearchError):
        search_web("headphones")


def test_the_search_and_its_result_count_are_logged(monkeypatch, caplog) -> None:
    stub_ddgs(monkeypatch, results=[{"title": "A"}])

    with caplog.at_level(logging.INFO, logger="buy_agent.search"):
        search_web("headphones")

    assert "headphones" in caplog.text
    assert "Search returned 1 results" in caplog.text


def test_the_default_is_ten_results_from_the_us(monkeypatch) -> None:
    seen = stub_ddgs(monkeypatch)

    search_web("headphones")

    assert seen == {"query": "headphones", "max_results": 10, "region": "us-en"}


def test_an_unexpected_error_is_not_disguised_as_a_search_failure(monkeypatch) -> None:
    """Only backend failures become SearchError; a bug must not look like one."""
    stub_ddgs(monkeypatch, error=TypeError("bug in the wrapper"))

    with pytest.raises(TypeError, match="bug in the wrapper"):
        search_web("headphones")


def stub_sequence(monkeypatch, *answers) -> list[str]:
    """Point ``search_web`` at a backend giving each answer in turn."""
    remaining = list(answers)
    asked: list[str] = []

    class FakeDDGS:
        def text(self, query: str, **_: object) -> list[dict]:
            asked.append(query)
            answer = remaining.pop(0) if len(remaining) > 1 else remaining[0]
            if isinstance(answer, Exception):
                raise answer
            return answer

    monkeypatch.setattr("buy_agent.search.DDGS", FakeDDGS)
    return asked


def test_a_failed_search_is_asked_once_more(monkeypatch) -> None:
    """One search is the whole of a run's input, so the one that failed is worth
    asking twice -- a lost page leaves nine, a lost search leaves nothing (ADR-0053)."""
    asked = stub_sequence(
        monkeypatch,
        DDGSException("rate limit"),
        [{"title": "Sony XM5", "href": "https://shop/x", "body": "$328"}],
    )
    waits: list[float] = []

    results = search_web("headphones", wait=waits.append)

    assert [result.title for result in results] == ["Sony XM5"]
    assert waits == [_RETRY_WAIT]
    assert len(asked) == 2


def test_a_search_that_fails_twice_is_the_failure_it_always_was(monkeypatch) -> None:
    """The second failure is final, and carries its own message: a backend that is
    down stays down, and the sentence the shopper reads is the last thing it said."""
    asked = stub_sequence(
        monkeypatch, DDGSException("rate limit"), DDGSException("still rate limited")
    )

    with pytest.raises(SearchError, match="still rate limited"):
        search_web("headphones", wait=lambda _: None)

    assert len(asked) == 2


def test_a_search_is_asked_once_where_there_is_nothing_to_wait_by(monkeypatch) -> None:
    """The default: a step handed no clock does not wait, and the failure is immediate."""
    asked = stub_sequence(monkeypatch, DDGSException("rate limit"))

    with pytest.raises(SearchError, match="rate limit"):
        search_web("headphones")

    assert len(asked) == 1


def test_a_search_that_matched_nothing_is_never_asked_again(monkeypatch) -> None:
    """It worked."""
    asked = stub_sequence(monkeypatch, DDGSException(_NO_RESULTS))
    waits: list[float] = []

    assert search_web("headphones", wait=waits.append) == []
    assert (waits, len(asked)) == ([], 1)


def test_a_search_that_matched_nothing_on_the_second_try_is_still_an_answer(
    monkeypatch,
) -> None:
    """The no-results check is inside the loop, so it is asked of both attempts."""
    asked = stub_sequence(monkeypatch, DDGSException("rate limit"), DDGSException(_NO_RESULTS))

    assert search_web("headphones", wait=lambda _: None) == []
    assert len(asked) == 2


def test_the_retry_says_what_it_is_waiting_for(monkeypatch, caplog) -> None:
    """At WARNING: this is the failure the run would have ended on, and the line is
    what tells a run that took two seconds longer from one that nearly stopped."""
    stub_sequence(monkeypatch, DDGSException("rate limit"), [])

    with caplog.at_level(logging.WARNING):
        search_web("headphones", wait=lambda _: None)

    assert "asking again in 2s" in caplog.text


# -- the table itself ----------------------------------------------------------


def test_the_default_backend_is_the_one_that_needs_nothing(monkeypatch, caplog) -> None:
    """ADR-0057: a run that was told nothing searches the way it always did.

    Asked of a run rather than read off ``search_web.__kwdefaults__``: the mutation run
    tests a copy of the package in which every function sits behind mutmut's trampoline,
    which carries none of the defaults declared under it, so a rule read off the function
    object fails on the copy while saying nothing at all about the code.
    """
    seen = stub_ddgs(monkeypatch)

    with caplog.at_level(logging.INFO):
        search_web("headphones")

    # ``ddgs`` is the one row that reaches for it; the others go through ``httpx``.
    assert seen["query"] == "headphones"
    assert DDG.label in caplog.text
    assert BACKENDS["ddg"] is DDG
    assert DDG.configured and not DDG.needs_key


def test_an_unknown_backend_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="Unknown search backend 'bing'"):
        backend_for("bing")


def test_the_picker_is_offered_every_row_and_never_a_key() -> None:
    """The rows go to a browser, so the one secret on them may not (ADR-0057)."""
    offered = backend_options()

    assert [row["name"] for row in offered] == list(BACKENDS)
    assert all("api_key" not in row for row in offered)


def test_a_backend_that_needs_a_key_and_has_none_is_not_configured() -> None:
    """What the picker marks a row with, decided here rather than in TypeScript.

    Built rather than read off the shipped row: its key comes from the environment this
    process started in, so a developer holding one would be testing the other answer.
    """
    assert BRAVE.needs_key
    assert not _with_key(BRAVE, "").configured
    assert _with_key(BRAVE, "secret").configured


@pytest.mark.parametrize(
    ("region", "country", "language"),
    [("us-en", "US", "en"), ("pl-pl", "PL", "pl"), ("hk-tzh", "HK", "tzh")],
)
def test_a_region_splits_into_the_halves_each_backend_asks_for(
    region: str, country: str, language: str
) -> None:
    """ADR-0031 spells a region one way and the backends want it in halves, so the
    splitting is done once above the rows."""
    asked = Query(text="headphones", max_results=3, region=region)

    assert (asked.country, asked.language) == (country, language)


def test_a_region_with_no_language_half_is_used_whole() -> None:
    """Nothing can reach this through a door -- ``parse_region`` refuses it -- and a
    row asking for a blank language would search for nothing rather than for less."""
    assert Query(text="x", max_results=1, region="us").language == "us"


# -- the two backends asked over HTTP ------------------------------------------


def stub_http(monkeypatch, *, payload=None, text="", error: Exception | None = None) -> dict:
    """Point the addressed rows at a fake far end and return the request it saw."""
    seen: dict = {}

    def get(url: str, **kwargs) -> httpx.Response:
        seen.update({"url": url, **kwargs})
        if error is not None:
            raise error
        request = httpx.Request("GET", url)
        if payload is None:
            return httpx.Response(200, text=text, request=request)
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr("buy_agent.search.httpx.get", get)
    return seen


def test_a_searxng_answer_becomes_search_results(monkeypatch) -> None:
    seen = stub_http(
        monkeypatch,
        payload={
            "results": [
                {"title": "Sony XM5", "url": "https://shop/x", "content": "$328"},
                {"title": "Bose", "url": "https://shop/b", "content": "$279"},
            ]
        },
    )

    results = search_web("headphones", max_results=1, region="pl-pl", backend=SEARXNG)

    assert [(r.title, r.url, r.snippet) for r in results] == [
        ("Sony XM5", "https://shop/x", "$328")
    ]
    assert seen["url"] == "http://localhost:8080/search"
    assert seen["params"] == {
        "q": "headphones",
        "format": "json",
        "language": "pl",
        "safesearch": 0,
    }


@pytest.mark.parametrize("keyed", [False, True], ids=["searxng", "brave"])
def test_a_backend_asked_over_http_is_asked_with_a_bound_on_the_wait(
    monkeypatch, keyed: bool
) -> None:
    """httpx waits forever when told ``None``, and an instance that took the request
    and went quiet would hang the run on its first step."""
    seen = stub_http(monkeypatch, payload={"results": [], "web": {"results": []}})

    search_web("headphones", backend=_with_key(BRAVE, "k") if keyed else SEARXNG)

    assert isinstance(seen["timeout"], (int, float)) and seen["timeout"] > 0


def test_an_address_written_with_a_trailing_slash_is_the_same_address(monkeypatch) -> None:
    """``$SEARXNG_HOST`` copied out of an address bar ends in one."""
    seen = stub_http(monkeypatch, payload={"results": []})

    search_web("headphones", backend=_at(SEARXNG, "http://searx.lan:8888/"))

    assert seen["url"] == "http://searx.lan:8888/search"


def test_ddg_answering_more_than_was_asked_is_cut_to_what_was_asked(monkeypatch) -> None:
    """``max_results`` is a request to the library, not a promise it keeps."""
    stub_ddgs(
        monkeypatch,
        results=[{"title": f"Hit {n}", "href": f"https://x/{n}", "body": ""} for n in range(3)],
    )

    assert [r.title for r in search_web("headphones", max_results=2)] == ["Hit 0", "Hit 1"]


def test_brave_answering_more_than_was_asked_is_cut_to_what_was_asked(monkeypatch) -> None:
    stub_http(
        monkeypatch,
        payload={
            "web": {
                "results": [
                    {"title": f"Hit {n}", "url": f"https://x/{n}", "description": ""}
                    for n in range(3)
                ]
            }
        },
    )

    found = search_web("headphones", max_results=2, backend=_with_key(BRAVE, "k"))

    assert [r.title for r in found] == ["Hit 0", "Hit 1"]


def test_a_brave_answer_becomes_search_results(monkeypatch) -> None:
    seen = stub_http(
        monkeypatch,
        payload={
            "web": {"results": [{"title": "Sony XM5", "url": "https://shop/x", "description": "$328"}]}
        },
    )
    keyed = _with_key(BRAVE, "secret")

    results = search_web("headphones", max_results=4, region="pl-pl", backend=keyed)

    assert [(r.title, r.url, r.snippet) for r in results] == [
        ("Sony XM5", "https://shop/x", "$328")
    ]
    assert seen["headers"]["X-Subscription-Token"] == "secret"
    assert seen["params"] == {
        "q": "headphones",
        "count": 4,
        "country": "PL",
        "search_lang": "pl",
    }


def test_brave_is_never_asked_for_more_than_it_answers(monkeypatch) -> None:
    """A run may ask for fifty results; Brave answers at most twenty and refuses a larger
    ``count`` rather than answering fewer."""
    seen = stub_http(monkeypatch, payload={"web": {"results": []}})

    search_web("headphones", max_results=50, backend=_with_key(BRAVE, "k"))

    assert seen["params"]["count"] == 20


def test_brave_without_a_key_says_which_variable_to_set(monkeypatch) -> None:
    """Not a transport failure, so it is not asked twice: a second keyless request is
    a second refusal."""
    asked = stub_http(monkeypatch, payload={"web": {"results": []}})

    with pytest.raises(SearchError, match=r"\$BRAVE_API_KEY"):
        search_web("headphones", backend=_with_key(BRAVE, ""), wait=lambda _: None)

    assert asked == {}


def test_a_brave_answer_with_no_web_block_is_no_results(monkeypatch) -> None:
    """Readable, and simply holding nothing: an answer, not a failure."""
    stub_http(monkeypatch, payload={"query": {"original": "headphones"}})

    assert search_web("headphones", backend=_with_key(BRAVE, "k")) == []


def test_an_answer_that_is_not_json_is_the_backend_s_own_failure(monkeypatch) -> None:
    """The rule ``rails._post`` follows: HTML where JSON was asked for is the far end
    being configured wrongly, and a second identical request gets the same page."""
    stub_http(monkeypatch, text="<html>search</html>")

    with pytest.raises(SearchError, match="not JSON"):
        search_web("headphones", backend=SEARXNG, wait=lambda _: None)


def test_an_answer_that_is_not_an_object_is_the_same_failure(monkeypatch) -> None:
    stub_http(monkeypatch, payload=["one", "two"])

    with pytest.raises(SearchError, match="answered with list, not an object"):
        search_web("headphones", backend=SEARXNG)


def test_entries_that_are_not_objects_are_skipped(monkeypatch) -> None:
    """One malformed row is not worth losing the nine beside it."""
    stub_http(monkeypatch, payload={"results": ["nonsense", {"title": "Sony"}]})

    assert [r.title for r in search_web("x", backend=SEARXNG)] == ["Sony"]


def test_an_unreachable_instance_names_its_address_and_the_way_back(monkeypatch) -> None:
    stub_http(monkeypatch, error=httpx.ConnectError("refused"))

    with pytest.raises(SearchError) as failure:
        search_web("headphones", backend=SEARXNG)

    said = str(failure.value)
    assert "http://localhost:8080" in said
    assert "(refused)" in said, "with what the transport said"
    assert "$SEARXNG_HOST" in said
    assert DDG.label in said


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_brave_key_is_said_as_a_refused_key(monkeypatch, status: int) -> None:
    """Neither is "the address is wrong", which is what the other sentence says: an
    unknown key and one past its plan are both the key."""
    request = httpx.Request("GET", BRAVE.endpoint)
    refused = httpx.HTTPStatusError(
        str(status), request=request, response=httpx.Response(status, request=request)
    )
    stub_http(monkeypatch, error=refused)

    with pytest.raises(SearchError, match="refused the key"):
        search_web("headphones", backend=_with_key(BRAVE, "wrong"))


def test_a_brave_outage_falls_back_to_the_address_sentence(monkeypatch) -> None:
    """Any status that is not a refused key is the far end being missing."""
    request = httpx.Request("GET", BRAVE.endpoint)
    down = httpx.HTTPStatusError(
        "503", request=request, response=httpx.Response(503, request=request)
    )
    stub_http(monkeypatch, error=down)

    with pytest.raises(SearchError, match=r"\(503\)\. Check the address in \$BRAVE_HOST"):
        search_web("headphones", backend=_with_key(BRAVE, "k"))


def test_the_backend_being_asked_is_named_in_the_line_that_says_so(
    monkeypatch, caplog
) -> None:
    """Two backends can be configured on one machine, so which one a run asked is the
    half of that line a reader cannot work out."""
    stub_http(monkeypatch, payload={"results": []})

    with caplog.at_level(logging.INFO, logger="buy_agent.search"):
        search_web("headphones", backend=SEARXNG)

    assert SEARXNG.label in caplog.text


def _with_key(backend: Backend, key: str) -> Backend:
    """``backend`` as it would have been built with that key in its environment."""
    return Backend(
        name=backend.name,
        label=backend.label,
        endpoint=backend.endpoint,
        api_key=key,
        needs_key=backend.needs_key,
        find=backend.find,
        transport_errors=backend.transport_errors,
        hint=backend.hint,
    )


def _at(backend: Backend, endpoint: str) -> Backend:
    """``backend`` as it would have been built with that address in its environment."""
    return Backend(
        name=backend.name,
        label=backend.label,
        endpoint=endpoint,
        api_key=backend.api_key,
        needs_key=backend.needs_key,
        find=backend.find,
        transport_errors=backend.transport_errors,
        hint=backend.hint,
    )
