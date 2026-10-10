"""Reading options off a web request, and shaping the answer as JSON."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from buy_agent.agent import every_step_passes, journal_for
from buy_agent.api import (
    ApiError,
    bounds_payload,
    defaults_payload,
    installed_models,
    parse_options,
    pay_now,
    product_payload,
    rank_again,
    results_payload,
    run_search,
    screenshot,
)
from buy_agent.config import LIMITS, AgentConfig
from buy_agent.models import Offer, Product, nothing_recorded
from buy_agent.ranking import RankingWeights, rank_products
from buy_agent.providers import LITELLM
from buy_agent.sources import Source
from tests.conftest import (
    enrolled_key,
    needs_ap2,
    Photographer,
    open_mandate,
    payable_product,
    ranked_product,
    said,
)

RANKED = [
    ranked_product(
        Product(
            name="Sony WH-1000XM5",
            price=328.0,
            currency="USD",
            rating=4.7,
            review_count=12000,
            seller="Amazon",
            url="https://example.com/sony",
            opinions=said("the noise cancelling is uncanny", page="https://example.com/sony"),
        ),
        score=0.912345,
        rank=1,
    ),
    ranked_product(Product(name="Anker Q30"), score=0.5, rank=2),
]


def agent_returning(result):
    """An agent factory that records its config and answers with ``result``."""
    captured: dict = {}

    class Stub:
        def __init__(self, config):
            captured["config"] = config

        def run(
            self,
            request,
            *,
            sort_by="score",
            checkpoint=every_step_passes,
            record=nothing_recorded,
        ):
            captured["request"] = request
            captured["sort_by"] = sort_by
            captured["checkpoint"] = checkpoint
            if isinstance(result, BaseException):
                raise result
            return result

    captured["factory"] = Stub
    return captured


# -- parse_options -------------------------------------------------------------


def test_empty_request_data_gives_the_config_defaults() -> None:
    config, sort_by = parse_options({})
    assert config == AgentConfig()
    assert sort_by == "score"


# -- the region, which is the one option a typo makes look like an empty web ---


# -- the sources, which are the one option that is a list ----------------------


@pytest.mark.parametrize("blank", ["", "   ", [], [""], ["", "  "], ","])
def test_an_empty_sources_field_is_the_whole_web_too(blank) -> None:
    """A cleared form field means "unset", the same as every other option."""
    assert parse_options({"sources": blank})[0].sources == ()


def test_several_sources_arrive_as_one_separated_string() -> None:
    """Which is all a query string can carry, and what the form's field holds."""
    config, _ = parse_options({"sources": "rtings.com, @mkbhd"})

    assert config.sources == (
        Source(spec="rtings.com", domain="rtings.com"),
        Source(spec="@mkbhd", domain="youtube.com", term="@mkbhd"),
    )


def test_an_array_holding_something_that_is_not_text_is_refused_not_a_traceback() -> None:
    """A JSON array is whatever was posted, so its entries are rendered with ``str`` the
    way ``_present`` already reads them."""
    with pytest.raises(ApiError) as failure:
        parse_options({"sources": ["rtings.com", 5]})

    assert failure.value.status == 400
    assert failure.value.field == "sources"
    assert "'5'" in str(failure.value)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"results": 51}, "results must be between 1 and 50; got 51."),
        ({"top": 0}, "top must be between 1 and 50; got 0."),
        ({"num_ctx": 0}, "num_ctx must be between 1 and 1000000; got 0."),
        ({"results": "many"}, "results must be a whole number; got 'many'."),
        ({"temperature": 2.5}, "temperature must be between 0 and 2; got 2.5."),
        ({"temperature": "hot"}, "temperature must be a number; got 'hot'."),
        ({"think": "maybe"}, "think must be true or false; got 'maybe'."),
        ({"cpu_only": "sometimes"}, "cpu_only must be true or false; got 'sometimes'."),
        ({"sort_by": "cheapness"}, "sort_by must be one of score, price, rating; got 'cheapness'."),
        ({"provider": "llama.cpp"}, "provider must be one of ollama, vllm, litellm, trtllm; got 'llama.cpp'."),
    ],
)
def test_a_rejection_says_what_was_wrong_and_what_was_wanted(data: dict, message: str) -> None:
    """The message is the whole of a 400 response, so it is checked word for word."""
    with pytest.raises(ApiError) as excinfo:
        parse_options(data)

    assert str(excinfo.value) == message


# -- the ranges the form is shipped --------------------------------------------


# -- the sources check, which is the one rule the form cannot apply itself ------


# -- the listings a product was priced at (ADR-0058) ---------------------------


def test_a_products_offers_carry_the_amount_written_out() -> None:
    """How money is written is Python's, so the card never has two spellings of one
    figure (ADR-0012)."""
    priced = Product(
        name="Sony WH-1000XM5",
        price=349.0,
        currency="USD",
        offers=[
            Offer(price=349.0, currency="USD", seller="ShopA", url="https://a.example/p"),
            Offer(price=329.0, currency="USD"),
        ],
    )

    payload = product_payload(ranked_product(priced, score=0.9, rank=1), "USD")

    assert payload["offers"] == [
        {
            "price": 349.0,
            "currency": "USD",
            "seller": "ShopA",
            "url": "https://a.example/p",
            "availability": None,
            "condition": None,
            "price_label": "349.00 USD",
        },
        {
            "price": 329.0,
            "currency": "USD",
            "seller": None,
            "url": None,
            "availability": None,
            "condition": None,
            "price_label": "329.00 USD",
        },
    ]
    assert payload["offers_label"] == "2 listings, 329.00-349.00 USD"


# -- what the request itself asks for, which is offered and never applied ------


def test_a_figure_at_the_top_of_the_range_still_is() -> None:
    """The bound is inclusive at both doors, and this is the third."""
    assert bounds_payload(f"a house under ${LIMITS['max_price'][1]}")["noticed"]


def test_a_figure_at_the_bottom_of_the_range_still_is() -> None:
    """...at both ends of it: a budget of the smallest the box takes is one it takes."""
    assert bounds_payload(f"a charging cable under ${LIMITS['max_price'][0]}")["noticed"]


# -- run_search ----------------------------------------------------------------


def test_a_run_answers_with_ranked_products() -> None:
    captured = agent_returning(RANKED)
    payload = run_search(
        "  headphones  ",
        AgentConfig(top_n=2),
        sort_by="price",
        agent_factory=captured["factory"],
    )
    assert payload["request"] == "headphones"
    assert payload["count"] == 2
    assert payload["top_n"] == 2
    assert payload["sort_by"] == "price"
    assert [p["rank"] for p in payload["products"]] == [1, 2]
    assert captured["sort_by"] == "price"


def test_a_product_carries_both_the_figures_and_their_labels() -> None:
    """The browser should not have to reinvent how an unknown price reads."""
    payload = product_payload(RANKED[0])
    assert payload["price"] == 328.0
    assert payload["price_label"] == "328.00 USD"
    assert payload["rating_label"] == "4.7/5 (12,000 reviews)"
    assert payload["score"] == 0.9123  # rounded for display


def test_a_second_run_of_one_search_says_what_moved_since_the_first() -> None:
    """The journal is opened before the run and written after it, so the second run of
    a search is answered with the first as what it was compared against -- which is
    everything the page's "since last time" panel has to draw (ADR-0060)."""
    config = AgentConfig()
    run_search("headphones", config, agent_factory=agent_returning(RANKED)["factory"])
    cheaper = [
        ranked_product(
            RANKED[0].product.model_copy(update={"price": 299.0}), score=0.9, rank=1
        ),
        RANKED[1],
    ]

    payload = run_search(
        "headphones", config, agent_factory=agent_returning(cheaper)["factory"]
    )

    assert payload["compared_with"]
    assert {
        change["name"]: change["movement"] for change in payload["changes"]
    }["Sony WH-1000XM5"] == "cheaper"


# -- rank_again, the one entry point that runs no pipeline ---------------------


def posted(**overrides) -> dict:
    """A finished run posted back the way the page holds it: the payload it was
    sent, extra keys and all."""
    return {
        "request": " headphones ",
        "products": results_payload(RANKED),
        "top": 1,
        **overrides,
    }


def test_a_run_lets_go_of_its_agent_once_the_answer_is_shaped() -> None:
    """One request, one agent, and the connection it opened closed here rather than
    whenever the last reference to it happens to fall."""
    captured = agent_returning(RANKED)
    closed: list[bool] = []
    captured["factory"].close = lambda _self: closed.append(True)

    run_search("headphones", AgentConfig(), agent_factory=captured["factory"])

    assert closed == [True]


def test_a_re_sort_that_was_told_nothing_else_answers_with_the_defaults() -> None:
    """The products are the one thing it needs; everything else has the answer a run
    with nothing set would have given."""
    payload = rank_again({"products": results_payload(RANKED)})

    assert payload["request"] == ""
    assert payload["top_n"] == AgentConfig().top_n
    assert payload["sort_by"] == "score"


def test_reordering_keeps_the_request_and_the_count_it_was_given() -> None:
    payload = rank_again(posted())

    assert payload["request"] == "headphones", "stripped, as a run's own answer is"
    assert payload["count"] == 2
    assert payload["top_n"] == 1, "how many to highlight is the page's to carry over"


def test_reordering_refuses_a_criterion_nothing_sorts_by() -> None:
    with pytest.raises(ApiError) as excinfo:
        rank_again(posted(sort_by="cheapness"))

    assert excinfo.value.status == 400
    assert excinfo.value.field == "sort_by"


def test_reordering_holds_the_highlight_count_to_the_range_a_run_holds_it_to() -> None:
    """The same range on the same key: an endpoint that took 500 here would be a
    second door accepting what the first refuses."""
    with pytest.raises(ApiError) as excinfo:
        rank_again(posted(top=LIMITS["top_n"][1] + 1))

    assert excinfo.value.field == "top"


@pytest.mark.parametrize("products", ["Sony", {"name": "Sony"}, None, 3])
def test_reordering_refuses_anything_that_is_not_a_list_of_products(products) -> None:
    with pytest.raises(ApiError) as excinfo:
        rank_again(posted(products=products))

    assert excinfo.value.status == 400
    assert excinfo.value.field == "products"


def test_reordering_says_which_product_it_could_not_read() -> None:
    """One of ten came back wrong, and "products is invalid" is not enough to
    find it -- the index is what a bug report is built out of."""
    with pytest.raises(ApiError) as excinfo:
        rank_again(posted(products=[*results_payload(RANKED), {"price": 3.0}]))

    assert "products[2]" in str(excinfo.value)
    assert excinfo.value.field == "products"


def test_paying_refuses_a_figure_that_is_not_a_number_at_the_door() -> None:
    """The same door, and the one where it costs more than a broken page: an
    infinite price clears ``payable``, so the card would offer a Pay button for
    an amount ``minor_units`` refuses once the cart is built."""
    with pytest.raises(ApiError) as excinfo:
        pay_now(json.loads('{"products": [{"name": "Sony", "price": Infinity,'
                           ' "currency": "USD", "url": "https://example.com/s"}]}'))

    assert excinfo.value.status == 400
    assert excinfo.value.field == "products"


# -- the rest ------------------------------------------------------------------


def test_choosing_a_litellm_proxy_brings_its_own_pair() -> None:
    config, _ = parse_options({"provider": "litellm", "model": "", "base_url": ""})

    assert config.provider == "litellm"
    assert (config.model, config.base_url) == (LITELLM.model, LITELLM.base_url)


def test_defaults_payload_matches_the_config() -> None:
    payload = defaults_payload()
    defaults = AgentConfig()
    assert payload["provider"] == defaults.provider
    assert payload["model"] == defaults.model
    assert payload["results"] == defaults.num_products
    assert payload["top"] == defaults.top_n
    assert payload["cpu_only"] == defaults.cpu_only
    assert payload["sort_options"] == ["score", "price", "rating"]
    # The order the form starts in is the one a run ranks by when asked for none.
    assert payload["sort_by"] == "score"
    # One text field holding all of them, which is what the form sends back.
    assert payload["sources"] == ""


def test_installed_models_asks_a_proxy_which_aliases_answer(monkeypatch) -> None:
    """Each alias's mode, so an embedding one reaches the picker marked (ADR-0032)."""
    info = [{"model_name": "embedder", "model_info": {"mode": "embedding"}}]

    def get(url, **_kwargs):
        assert url == "http://localhost:4000/model/info", url
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": info})

    monkeypatch.setattr("buy_agent.providers.httpx.get", get)

    assert installed_models("litellm", "http://localhost:4000/v1") == {
        "provider": "litellm",
        "label": "LiteLLM",
        "base_url": "http://localhost:4000/v1",
        "reachable": True,
        "models": [{"name": "embedder", "completion": False}],
    }


def test_an_unreachable_ollama_is_a_status_not_an_error(monkeypatch) -> None:
    """The form still renders when Ollama is down; it just says so."""

    def explode(url, **_kwargs):
        raise ConnectionError("connection refused")

    monkeypatch.setattr("buy_agent.providers.httpx.get", explode)
    payload = installed_models("ollama", "http://localhost:11434")
    assert payload["reachable"] is False
    assert payload["models"] == []
    assert "refused" in payload["detail"]


def test_an_unreachable_server_says_how_to_start_it(monkeypatch) -> None:
    """The reason alone leaves the shopper where they were: the pill is the one moment
    where the fix is a single command, and the provider already writes it."""

    def explode(url, **_kwargs):
        raise ConnectionError("connection refused")

    monkeypatch.setattr("buy_agent.providers.httpx.get", explode)
    hint = installed_models("ollama", "http://localhost:11434")["hint"]

    assert "http://localhost:11434" in hint, "the address it could not reach"
    assert "connection refused" in hint, "and the transport's own reason with it"
    assert "ollama serve" in hint


# -- the shopper's bounds and the cache, over the wire -------------------------


# -- the score's parts, on the way to the card (ADR-0041) ----------------------


# -- paying --------------------------------------------------------------------


PAYABLE = payable_product()

APPROVED = {"title": PAYABLE.name, "price": PAYABLE.price, "currency": PAYABLE.currency}


def paying(**extra: object) -> dict:
    """A pay request the way the page sends one: the run, which product, the echo."""
    return {
        "products": [PAYABLE.model_dump()],
        "rank": 1,
        "approved": APPROVED,
        **extra,
    }


def test_a_rank_past_the_end_of_the_run_is_refused() -> None:
    with pytest.raises(ApiError) as excinfo:
        pay_now(paying(rank=4))

    assert excinfo.value.status == 400
    assert excinfo.value.field == "rank"


def test_paying_for_nothing_is_refused() -> None:
    with pytest.raises(ApiError, match="no products to pay for"):
        pay_now({"products": [], "approved": APPROVED})


@needs_ap2
def test_a_price_spelled_differently_is_the_same_approval() -> None:
    """Compared as numbers rather than as text: a browser writing 329.990 agreed
    to the same thing as one writing 329.99."""
    assert pay_now(paying(approved={**APPROVED, "price": "329.990"}))["receipt"]["paid"] is False


def test_a_price_that_is_not_a_number_is_not_an_approval() -> None:
    with pytest.raises(ApiError, match="not what this would buy"):
        pay_now(paying(approved={**APPROVED, "price": "about three hundred"}))


@needs_ap2
def test_an_open_mandate_needs_no_echo_from_the_page(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """That is the whole meaning of the autonomous mode: the mandate is the
    authority, and its constraints are what the cart is held to."""
    agent, _issuer = open_mandate(tmp_path, monkeypatch)
    enrolled_key(tmp_path, monkeypatch, agent)
    body = paying()
    del body["approved"]

    assert pay_now(body)["receipt"]["autonomous"] is True


@needs_ap2
def test_a_merchant_that_answers_with_something_unreadable_is_a_502_as_well(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Answering is not answering the question. A page of HTML where JSON was asked for
    is the counterparty's failure exactly as a refused connection is, so it lands on the
    same status rather than marking a form with nothing wrong with it."""
    from buy_agent import rails

    enrolled_key(tmp_path, monkeypatch)

    class Page:
        """A merchant answering 200 with a login page, which is how a misconfigured
        one fails rather than by refusing the connection."""

        def raise_for_status(self) -> None:
            """It answered, so there is no status to raise."""

        def json(self) -> object:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(rails.httpx, "post", lambda *_a, **_k: Page())

    with pytest.raises(ApiError) as excinfo:
        pay_now(paying(rail="http", merchant_url="https://pay.example"))

    assert excinfo.value.status == 502


def test_a_paying_rail_with_no_address_is_refused_before_anything_is_asked() -> None:
    """The one refusal a config makes that neither door has already made, on the one
    request that actually pays. The page sends no ``pay`` here, so read off the payload
    that guard stood down and the empty endpoint reached the rail instead -- a 502
    naming no address, for a mistake the form can make (ADR-0033)."""
    with pytest.raises(ApiError) as excinfo:
        pay_now(paying(rail="http", merchant_url=""))

    assert excinfo.value.status == 400
    assert excinfo.value.field == "merchant_url"
    assert "needs an address" in str(excinfo.value)


def test_the_spend_limit_travels_with_the_payment() -> None:
    with pytest.raises(ApiError) as excinfo:
        pay_now(paying(spend_limit=100))

    assert excinfo.value.field == "spend_limit"
    assert "spend limit" in str(excinfo.value)


def test_the_products_carry_the_money_a_purchase_would_be_in() -> None:
    """Which is frequently not the product's own: a page that printed a bare "179.00" is
    priced in the run's currency (ADR-0043), so ``currency`` is null while the cart is
    in USD."""
    bare = Product(name="Sennheiser Accentum", price=179.0, url="https://x.example/s")
    ranked = rank_products([PAYABLE, bare], weights=RankingWeights())

    by_name = {entry["name"]: entry for entry in results_payload(ranked)}
    accentum = by_name["Sennheiser Accentum"]

    assert accentum["currency"] is None
    assert accentum["price_label"] == "179.00"
    assert accentum["pay_currency"] == "USD"
    assert accentum["pay_label"] == "179.00 USD"


def test_the_products_carry_who_a_purchase_would_pay() -> None:
    """The cart's merchant, which is the site wherever no page printed a seller."""
    anonymous = Product(
        name="Sennheiser Accentum", price=179.0, currency="USD", url="https://x.example/s"
    )
    ranked = rank_products([PAYABLE, anonymous], weights=RankingWeights())

    by_name = {entry["name"]: entry for entry in results_payload(ranked)}

    assert by_name["Sony WH-1000XM5"]["pay_merchant"] == "AudioSite"
    assert by_name["Sennheiser Accentum"]["seller"] is None
    assert by_name["Sennheiser Accentum"]["pay_merchant"] == "x.example"


def test_a_product_that_cannot_be_bought_names_no_amount_either() -> None:
    """All three come out of the same check ``cannot_pay`` does, so there is never
    a button drawn on an amount nothing would authorise."""
    unpriced = Product(name="Anker Q30", url="https://x.example/a")

    payload = results_payload(rank_products([unpriced], weights=RankingWeights()))[0]

    assert payload["cannot_pay"] is not None
    assert payload["pay_currency"] is None
    assert payload["pay_label"] is None
    assert payload["pay_merchant"] is None


# -- which product, and what was actually approved -----------------------------


def test_an_approval_that_is_not_an_object_buys_nothing() -> None:
    with pytest.raises(ApiError) as excinfo:
        pay_now(paying(approved="yes"))

    assert excinfo.value.field == "approved"
    assert "send back the title" in str(excinfo.value)


def test_a_price_a_hundredth_out_is_not_the_same_approval() -> None:
    """The tolerance is there for how a number was spelled, not for a different
    price: a cent is a cent."""
    with pytest.raises(ApiError, match="not what this would buy"):
        pay_now(paying(approved={**APPROVED, "price": 329.98}))


def test_paying_for_nothing_names_the_field_the_run_should_have_filled() -> None:
    with pytest.raises(ApiError) as excinfo:
        pay_now({"products": [], "approved": APPROVED})

    assert excinfo.value.field == "products"


# -- what a run says it took out (ADR-0055) ------------------------------------


# -- the currency a run counts itself in (ADR-0056) ---------------------------


def test_a_re_sort_is_counted_on_the_scale_the_run_was_counted_on() -> None:
    """Letting the set vote again would answer a different ordering for one run
    (ADR-0035, ADR-0056)."""
    euros = Product(name="Sony XM5", price=329.0, currency="EUR", url="https://shop/x")
    dollars = Product(name="Bose QC", price=279.0, currency="USD", url="https://shop/b")
    products = results_payload(rank_products([dollars, euros]))

    reordered = rank_again({"products": products, "sort_by": "price", "currency": "EUR"})

    # The dollar price is not on the scale, so it sinks below the one that is.
    assert [entry["name"] for entry in reordered["products"]] == ["Sony XM5", "Bose QC"]


def test_a_re_sort_refuses_a_currency_the_way_a_run_does() -> None:
    """With the sentence ``parse_currency`` writes and the box it belongs under, since
    the page posts back what the form held (ADR-0033, ADR-0056)."""
    with pytest.raises(ApiError) as refused:
        rank_again({"products": results_payload(RANKED), "currency": "bucks"})

    assert (refused.value.status, refused.value.field) == (400, "currency")
    assert "'bucks' is not a currency this run can count in" in str(refused.value)


#: One listing in each currency: a tie, which the vote gives to whichever comes first --
#: the euro listing in the search's order, the dollar one once the Sony outranks it.
_TIED = [
    Product(name="Bose QC45", price=299.0, currency="EUR", url="https://shop.example/b"),
    Product(
        name="Sony XM5",
        price=329.0,
        currency="USD",
        rating=4.8,
        review_count=3200,
        url="https://audio.example/s",
    ),
]


def test_a_run_answers_in_the_currency_it_was_ranked_in_when_the_set_voted() -> None:
    """Voted again over the ranking, the tie went the other way: the payload counted in
    dollars a run ranked and bounded in euros, and offered to buy the one price that run
    could not place (ADR-0056)."""
    ranked = rank_products(_TIED)
    assert [entry.product.name for entry in ranked] == ["Sony XM5", "Bose QC45"]

    payload = run_search(
        "headphones", AgentConfig(), agent_factory=agent_returning(ranked)["factory"]
    )
    by_name = {entry["name"]: entry for entry in payload["products"]}

    assert payload["scale"] == "EUR"
    assert by_name["Bose QC45"]["pay_currency"] == "EUR"
    assert "this run counts in EUR" in by_name["Sony XM5"]["cannot_pay"]


def test_a_re_sort_handed_the_scale_a_run_voted_for_is_counted_in_it() -> None:
    """Folded as a page's spelling is, and taken as the run's own: voting again, the
    re-sort counted the set in dollars."""
    products = results_payload(rank_products(_TIED))

    reordered = rank_again({"products": products, "sort_by": "price", "scale": "eur"})

    assert reordered["scale"] == "EUR"
    # The euro price is the one on the scale, so the dollar one sinks below it.
    assert [entry["name"] for entry in reordered["products"]] == ["Bose QC45", "Sony XM5"]


def test_a_payment_handed_the_scale_a_run_voted_for_is_counted_in_it() -> None:
    """Voting again, the cart was in dollars, and the Sony was bought at a price the run
    had never placed -- past a budget read in euros, it had not been judged at all."""
    products = results_payload(rank_products(_TIED))
    approved = {"title": "Sony XM5", "price": 329.0, "currency": "USD"}

    with pytest.raises(ApiError) as refused:
        pay_now({"products": products, "rank": 1, "scale": "EUR", "approved": approved})

    assert (refused.value.status, refused.value.field) == (400, "products")
    assert "this run counts in EUR" in str(refused.value)


# -- the search backend a run asks (ADR-0057) ---------------------------------


# -- a picture of the page a card links to (ADR-0065) ---------------------------


def test_the_form_is_told_whether_this_server_takes_screenshots() -> None:
    """Not a setting: the server either has a camera or it has not, and says which."""
    assert defaults_payload()["screenshots"] is False
    assert defaults_payload(screenshots=True)["screenshots"] is True


@pytest.mark.parametrize(
    "address",
    [
        # This machine's own disk, which a browser that will draw anything draws too.
        "file:///C:/Users/me/.ssh/id_rsa",
        "javascript:alert(1)",
        "data:text/html,<h1>hi</h1>",
        "https://",
        "",
    ],
)
def test_only_a_web_page_is_photographed(address: str) -> None:
    camera = Photographer()

    with pytest.raises(ApiError) as refused:
        screenshot(address, camera)

    assert refused.value.status == 400
    assert camera.asked == [], "the browser was pointed at it anyway"


def test_a_page_served_without_tls_is_still_a_web_page() -> None:
    """Plenty of shops still answer on plain http, and the card links to them."""
    camera = Photographer()

    assert screenshot("http://shop.example/xm5", camera) == b"jpeg of http://shop.example/xm5"


# -- a listing's standing (ADR-0079) and the price alert (ADR-0080) -------------


def test_a_product_carries_its_listing_label_in_pythons_words() -> None:
    standing = Product(
        name="Sony WH-1000XM5", price=299.0, currency="USD", availability="in stock",
        condition="used",
    )

    payload = product_payload(ranked_product(standing, score=0.9, rank=1), "USD")

    assert payload["availability"] == "in stock"
    assert payload["condition"] == "used"
    assert payload["listing_label"] == "In stock, used"
    assert product_payload(RANKED[1])["listing_label"] is None


def test_an_out_of_stock_product_says_why_it_cannot_be_bought() -> None:
    gone = payable_product(availability="out of stock")

    payload = product_payload(ranked_product(gone, score=0.9, rank=1), "USD")

    assert "out of stock" in payload["cannot_pay"]
    assert payload["pay_label"] is None


def test_a_run_given_a_price_alert_says_whether_it_was_met() -> None:
    payload = run_search(
        "headphones",
        AgentConfig(alert_below=330),
        agent_factory=agent_returning(RANKED)["factory"],
    )

    assert payload["alert"] == {
        "below": 330,
        "below_label": "330.00 USD",
        "met": ["Sony WH-1000XM5"],
        "detail": "At or under 330.00 USD: Sony WH-1000XM5 at 328.00 USD.",
    }
    assert payload["count"] == 2, "told, and nothing removed"


def test_a_run_given_no_alert_answers_none() -> None:
    payload = run_search(
        "headphones", AgentConfig(), agent_factory=agent_returning(RANKED)["factory"]
    )

    assert payload["alert"] is None


def test_a_re_sort_answers_no_alert_of_its_own() -> None:
    """It ran no pipeline and was given no setting; the page keeps the run's."""
    assert rank_again({"products": results_payload(RANKED)})["alert"] is None


def test_the_alert_is_a_setting_both_doors_read_and_refuse_alike() -> None:
    config, _ = parse_options({"request": "x", "alert_below": "180.5"})

    assert config.alert_below == 180.5
    assert parse_options({"request": "x", "alert_below": " "})[0].alert_below is None
    assert defaults_payload()["alert_below"] is None
    assert defaults_payload()["limits"]["alert_below"] == {"min": 1, "max": 10_000_000}
    with pytest.raises(ApiError) as refused:
        parse_options({"request": "x", "alert_below": "0"})
    assert refused.value.field == "alert_below"


def test_the_alert_is_not_part_of_what_the_journal_keys_a_search_by() -> None:
    """An alert changes what is said about a run, not what was searched (ADR-0060)."""
    assert (
        journal_for("headphones", AgentConfig(alert_below=100)).key
        == journal_for("headphones", AgentConfig()).key
    )
