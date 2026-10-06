"""Argument wiring and exit codes for  python -m buy_agent."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest

import buy_agent.__main__ as main_module
from buy_agent.__main__ import NOTHING_FOUND, build_parser, main
from buy_agent.agent import ModelUnavailableError
from buy_agent.config import AgentConfig
from buy_agent.models import Product
from buy_agent.providers import PROVIDERS, VLLM
from buy_agent.rails import RAILS
from buy_agent.ranking import rank_products
from buy_agent.search import SearchError
from tests.conftest import (
    needs_ap2,
    payable_product,
    ranked_product,
)

RANKED = [
    ranked_product(Product(name="Sony WH-1000XM5", price=328.0), score=0.9, rank=1),
    ranked_product(Product(name="Anker Q30", price=79.0), score=0.8, rank=2),
]


@pytest.fixture
def fake_agent(monkeypatch):
    """Replace the agent with a recorder, so the CLI is tested on its own."""
    captured: dict = {}

    class Recorder:
        def __init__(self, config):
            captured["config"] = config

        def run(self, request, *, sort_by="score"):
            captured["request"] = request
            captured["sort_by"] = sort_by
            if isinstance(captured.get("result"), BaseException):
                raise captured["result"]
            return captured.get("result", RANKED)

        def close(self):
            captured["closed"] = captured.get("closed", 0) + 1

    monkeypatch.setattr("buy_agent.__main__.BuyAgent", Recorder)
    return captured


def test_a_run_that_failed_lets_go_of_its_agent_too(fake_agent) -> None:
    fake_agent["result"] = SearchError("DuckDuckGo is rate-limiting this")

    assert main(["gaming laptop"]) == 1
    assert fake_agent["closed"] == 1


def test_defaults_are_ten_products_and_a_top_three(fake_agent) -> None:
    assert main(["gaming laptop"]) == 0
    config = fake_agent["config"]
    assert (config.num_products, config.top_n) == (10, 3)
    assert fake_agent["request"] == "gaming laptop"


def test_flags_reach_the_config(fake_agent) -> None:
    main(
        [
            "espresso machine",
            "--model", "qwen2.5",
            "--results", "6",
            "--top", "2",
            "--region", "pl-pl",
            "--temperature", "0.4",
            "--sort-by", "price",
        ]
    )
    config = fake_agent["config"]
    assert config.model == "qwen2.5"
    assert config.num_products == 6
    assert config.top_n == 2
    assert config.region == "pl-pl"
    assert config.temperature == 0.4
    assert fake_agent["sort_by"] == "price"


def test_search_fetches_enough_results_for_the_requested_top_n(fake_agent) -> None:
    """Asking for a top 8 out of 3 products must not search for only 3 pages."""
    main(["tents", "--results", "3", "--top", "8"])
    assert fake_agent["config"].search_results == 8


def test_finding_nothing_has_an_exit_code_of_its_own(fake_agent) -> None:
    """A run that worked and found nothing is not a run that failed."""
    fake_agent["result"] = []

    assert main(["nonexistent gadget"]) == NOTHING_FOUND
    assert NOTHING_FOUND not in (0, 1, 2, 130), "the codes that already mean something"


def test_the_help_names_every_exit_code(fake_agent) -> None:
    """--help is the only documentation the CLI has, and these are branched on."""
    help_text = build_parser().format_help()

    for code in ("0", "1", "2", str(NOTHING_FOUND), "130"):
        assert f"  {code}  " in help_text, code


@pytest.mark.parametrize(
    "error",
    [
        ModelUnavailableError("no model"),
        SearchError("rate limited"),
        ValueError("empty request"),
    ],
)
def test_expected_failures_exit_one_with_a_logged_reason(fake_agent, caplog, error) -> None:
    fake_agent["result"] = error

    assert main(["headphones"]) == 1
    assert str(error) in caplog.text


def test_context_and_thinking_flags_reach_the_config(fake_agent) -> None:
    main(["headphones", "--num-ctx", "8192", "--no-think"])
    config = fake_agent["config"]
    assert config.num_ctx == 8192
    assert config.reasoning is False


def test_context_and_thinking_default_to_the_config(fake_agent) -> None:
    """Every flag defaults to its AgentConfig field, thinking mode included."""
    main(["headphones"])
    config = fake_agent["config"]
    assert config.num_ctx == 16384
    assert config.reasoning is False


def test_a_ctrl_c_exits_with_130(fake_agent) -> None:
    """130 is the shell's convention for "killed by SIGINT"."""
    fake_agent["result"] = KeyboardInterrupt()

    assert main(["headphones"]) == 130


def test_an_interruption_is_reported_rather_than_traced(fake_agent, caplog) -> None:
    fake_agent["result"] = KeyboardInterrupt()

    with caplog.at_level(logging.WARNING, logger="buy_agent"):
        main(["headphones"])

    assert "Interrupted" in caplog.text


def test_an_unexpected_failure_is_not_swallowed(fake_agent) -> None:
    """Only the three documented failures are handled; a bug must surface as one."""
    fake_agent["result"] = RuntimeError("something nobody planned for")

    with pytest.raises(RuntimeError, match="something nobody planned for"):
        main(["headphones"])


def test_the_source_flag_repeats_to_name_several(fake_agent) -> None:
    main(["headphones", "--source", "rtings.com", "--source", "@mkbhd"])

    assert [source.domain for source in fake_agent["config"].sources] == [
        "rtings.com",
        "youtube.com",
    ]


def test_a_source_that_names_no_site_is_a_usage_error_that_says_what_does(capsys) -> None:
    """argparse throws a type function's ValueError away, so the reason is raised
    as the error it prints -- without it the shopper is told only "invalid value"."""
    with pytest.raises(SystemExit) as exit_info:
        main(["headphones", "--source", "Marques Brownlee"])

    assert exit_info.value.code == 2
    assert "@mkbhd" in capsys.readouterr().err


def test_the_help_names_every_provider_s_variables_and_what_a_proxy_ignores(capsys) -> None:
    """--help is the CLI's only documentation, so every server's variables are in it,
    and so is why a proxy takes neither server-side setting."""
    with pytest.raises(SystemExit):
        main(["--help"])
    printed = " ".join(capsys.readouterr().out.split())

    for name in ("OLLAMA", "VLLM", "LITELLM"):
        assert f"${name}_MODEL" in printed and f"${name}_HOST" in printed
    assert printed.count("a LiteLLM proxy leaves it to the server it routes to") == 2


def test_sorting_defaults_to_the_blended_score(fake_agent) -> None:
    main(["headphones"])

    assert fake_agent["sort_by"] == "score"


def test_verbose_reaches_the_logging_setup(fake_agent, monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        "buy_agent.__main__.configure_logging", lambda **kwargs: captured.update(kwargs)
    )

    main(["headphones", "-v"])

    assert captured == {"verbose": True}


def test_the_json_is_written_for_a_person_to_read(fake_agent, tmp_path) -> None:
    """Indented, since the file is opened by somebody as often as by a script."""
    destination = tmp_path / "out.json"

    main(["headphones", "--json", str(destination)])

    assert destination.read_text(encoding="utf-8").startswith('[\n  {\n    "')


def test_the_json_is_counted_on_the_currency_the_run_was_told_to(
    fake_agent, tmp_path
) -> None:
    """The file is what a script buys from, so it is on the scale the ranking used and
    not a second vote of the set -- which here would have been dollars (ADR-0056)."""
    fake_agent["result"] = [
        ranked_product(payable_product(name="Bose QC", currency="USD"), score=0.6, rank=1),
        ranked_product(payable_product(name="JBL Live", currency="USD"), score=0.5, rank=2),
        ranked_product(payable_product(name="Sony XM5", currency="EUR"), score=0.4, rank=3),
    ]
    destination = tmp_path / "out.json"

    main(["headphones", "--currency", "EUR", "--json", str(destination)])

    written = {entry["name"]: entry for entry in json.loads(destination.read_text("utf-8"))}
    assert written["Sony XM5"]["pay_currency"] == "EUR"
    assert written["Bose QC"]["cannot_pay"]


def test_the_json_writes_a_currency_sign_as_itself(fake_agent, tmp_path) -> None:
    """A file somebody opens reads "zł", not an escape sequence standing for it."""
    fake_agent["result"] = [ranked_product(Product(name="Słuchawki €"), score=0.5, rank=1)]
    destination = tmp_path / "out.json"

    main(["headphones", "--json", str(destination)])

    written = destination.read_text(encoding="utf-8")
    assert "Słuchawki €" in written
    assert json.loads(written)[0]["name"] == "Słuchawki €"


def test_a_json_path_with_no_directory_is_a_usage_error_before_the_run(
    fake_agent, tmp_path, capsys
) -> None:
    """The file is written once the run is over, so a typo in its directory used to
    cost the whole run -- minutes, on a real model -- before being mentioned at all.
    Refused at the door instead, the rule every other value on this command line
    keeps."""
    destination = tmp_path / "no-such-directory" / "out.json"

    with pytest.raises(SystemExit) as exit_info:
        main(["headphones", "--json", str(destination)])

    assert exit_info.value.code == 2
    assert "config" not in fake_agent, "no agent was built, so nothing was searched"
    error = capsys.readouterr().err
    assert "--json" in error and "no-such-directory" in error and "out.json" in error


def test_a_json_path_naming_a_directory_is_a_usage_error_too(
    fake_agent, tmp_path, capsys
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["headphones", "--json", str(tmp_path)])

    assert exit_info.value.code == 2
    assert f"{str(tmp_path)!r} is a directory" in capsys.readouterr().err


def test_an_unwritable_json_path_is_an_exit_code_not_a_traceback(
    fake_agent, tmp_path, caplog, monkeypatch
) -> None:
    """A file its directory will not take once the run is over -- a full disk, a
    read-only share -- must not end a minute of work in a stack trace."""
    destination = tmp_path / "out.json"

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "Permission denied", str(destination))

    monkeypatch.setattr(Path, "write_text", refuse)

    with caplog.at_level(logging.ERROR, logger="buy_agent"):
        assert main(["headphones", "--json", str(destination)]) == 1

    # Named by the line itself: the error's own words repeat the path, and not every
    # ``OSError`` carries one.
    assert f"Could not write {destination} (" in caplog.text
    assert not destination.exists()


def test_writing_the_json_is_logged(fake_agent, tmp_path, caplog) -> None:
    destination = tmp_path / "out.json"

    with caplog.at_level(logging.INFO, logger="buy_agent"):
        main(["headphones", "--json", str(destination)])

    assert "Wrote 2 products" in caplog.text


def test_a_context_window_the_provider_ignores_is_called_out(fake_agent, caplog) -> None:
    """vLLM fixes its window with --max-model-len when it starts."""
    with caplog.at_level(logging.WARNING):
        main(["headphones", "--provider", "vllm", "--num-ctx", "4096"])

    assert "--num-ctx 4096 is ignored" in caplog.text


def test_the_default_context_window_is_not_called_out(fake_agent, caplog) -> None:
    """Only a number the shopper actually typed is worth a warning."""
    with caplog.at_level(logging.WARNING):
        main(["headphones", "--provider", "vllm"])

    assert "ignored" not in caplog.text


def test_a_cpu_only_run_the_provider_ignores_is_called_out(fake_agent, caplog) -> None:
    """vLLM picks its device with --device when it starts."""
    with caplog.at_level(logging.WARNING):
        main(["headphones", "--provider", "vllm", "--cpu-only"])

    assert "--cpu-only is ignored" in caplog.text


@pytest.mark.parametrize(
    ("flag", "value"),
    [("--rail", "http"), ("--merchant-url", "https://pay.example"), ("--spend-limit", "250")],
)
def test_a_paying_flag_without_pay_is_called_out(
    fake_agent, caplog, flag: str, value: str
) -> None:
    """The form draws none of these until its paying switch is ticked."""
    with caplog.at_level(logging.WARNING):
        main(["headphones", flag, value])

    assert f"Nothing will be bought: {flag} does nothing without --pay." in caplog.text


def test_every_paying_flag_that_was_given_is_named_at_once(fake_agent, caplog) -> None:
    """One line for the lot: three warnings for one forgotten word is three
    things to read and one thing to fix."""
    with caplog.at_level(logging.WARNING):
        main(["headphones", "--rail", "http", "--spend-limit", "250"])

    assert "--rail, --spend-limit do nothing without --pay" in caplog.text


def test_the_defaults_survive_a_provider_the_environment_got_wrong(monkeypatch) -> None:
    """The other half: the module still imports, so --help still lists them."""
    monkeypatch.setattr(main_module, "DEFAULT_PROVIDER", "olama")

    defaults = main_module._defaults()

    assert defaults.provider in PROVIDERS
    assert defaults.num_products == AgentConfig().num_products


def test_a_provider_the_environment_got_right_is_the_one_the_flags_default_to(
    monkeypatch,
) -> None:
    """The half the fallback above must not swallow."""
    other = next(name for name in PROVIDERS if name != next(iter(PROVIDERS)))
    monkeypatch.setattr(main_module, "DEFAULT_PROVIDER", other)

    defaults = main_module._defaults()

    assert defaults.provider == other
    assert (defaults.model, defaults.base_url) == (VLLM.model, VLLM.base_url)


@pytest.mark.parametrize(("flag", "setting"), [("--model", "model"), ("--base-url", "base_url")])
def test_the_help_names_every_provider_s_own_default(flag: str, setting: str) -> None:
    """Neither flag has one default, so the help lists the lot."""
    listed = main_module._provider_defaults(setting)

    assert listed.split(", ") == [
        f"{getattr(server, setting)} for {name}" for name, server in PROVIDERS.items()
    ], "one comma-separated entry per provider, in the table's order"

    action = next(a for a in build_parser()._actions if flag in a.option_strings)
    assert listed in action.help


# -- the shopper's bounds and the cache, as flags ------------------------------


def test_the_temperature_help_says_only_a_run_at_0_is_remembered() -> None:
    """ADR-0044's gate, which only --cache-ttl's help used to mention: a reader raising
    the temperature lost the cache with nothing beside the flag to warn them."""
    action = next(a for a in build_parser()._actions if "--temperature" in a.option_strings)

    assert "--cache-ttl" in action.help
    assert "Above 0" in action.help


# -- paying --------------------------------------------------------------------


PAYABLE = [ranked_product(payable_product(), score=0.9, rank=1)]


class Typed:
    """A terminal somebody is sitting at, answering the approval prompt."""

    def __init__(self, answer: str, *, tty: bool = True) -> None:
        self.answer = answer
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty

    def readline(self) -> str:
        return self.answer


class Interrupted(Typed):
    """A terminal whose reader pressed Ctrl-C at the prompt instead of answering."""

    def __init__(self) -> None:
        super().__init__("")

    def readline(self) -> str:
        raise KeyboardInterrupt


@needs_ap2
def test_a_purchase_ends_with_the_line_that_points_back_at_its_authorisation(
    fake_agent, monkeypatch, caplog
) -> None:
    """What was bought, for how much and from whom, beside the hash of the mandate
    chain -- the one handle on it a receipt is allowed to carry (ADR-0046)."""
    fake_agent["result"] = PAYABLE
    monkeypatch.setattr(main_module.sys, "stdin", Typed("yes\n"))

    with caplog.at_level(logging.INFO, logger="buy_agent"):
        main(["headphones", "--pay"])

    covers = [record.getMessage() for record in caplog.records if " covers " in record.getMessage()]
    assert len(covers) == 1
    reference, rest = covers[0].removeprefix("Authorisation ").split(" covers ")
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}=?", reference), "a SHA-256, base64url"
    assert rest == "Sony WH-1000XM5 at 329.99 USD from AudioSite"


def test_the_prompt_restates_the_cart_and_whether_anybody_is_charged(
    fake_agent, monkeypatch, capsys
) -> None:
    """This is the Trusted Surface, small: what it shows is what the mandates
    will carry, not the request that found it."""
    fake_agent["result"] = PAYABLE
    monkeypatch.setattr(main_module.sys, "stdin", Typed("yes\n"))

    main(["headphones", "--pay"])

    shown = capsys.readouterr().err
    assert "329.99 USD" in shown
    assert "Sony WH-1000XM5" in shown
    assert "AudioSite" in shown
    # The end of the rail's line, word for word: the sentence a keypress follows.
    assert "-- you will NOT be charged\n" in shown


def test_the_prompt_on_a_rail_that_moves_money_says_somebody_will_be_charged(
    fake_agent, monkeypatch, capsys
) -> None:
    """The half of that line that matters most: said wrongly on a real counterparty, the
    one sentence standing between a keypress and a charge promises there is none."""
    fake_agent["result"] = PAYABLE
    monkeypatch.setattr(main_module.sys, "stdin", Typed("no\n"))

    main(["headphones", "--pay", "--rail", "http", "--merchant-url", "https://pay.example"])

    shown = capsys.readouterr().err
    assert "you will be charged" in shown
    assert "NOT" not in shown


def test_anything_but_yes_buys_nothing(fake_agent, monkeypatch, caplog) -> None:
    fake_agent["result"] = PAYABLE
    monkeypatch.setattr(main_module.sys, "stdin", Typed("no\n"))

    with caplog.at_level(logging.WARNING):
        assert main(["headphones", "--pay"]) == main_module.PAYMENT_FAILED

    assert "Not paid: Sony WH-1000XM5 was not approved." in caplog.text


def test_ctrl_c_at_the_approval_prompt_is_interrupted_and_not_a_traceback(
    fake_agent, monkeypatch, caplog
) -> None:
    """The prompt is where a shopper hesitates, so it is where Ctrl-C lands --
    and a traceback there reads as a crash at the one moment somebody needs to
    know whether any money moved."""
    fake_agent["result"] = PAYABLE
    monkeypatch.setattr(main_module.sys, "stdin", Interrupted())

    with caplog.at_level(logging.WARNING):
        assert main(["headphones", "--pay"]) == 130

    assert "Nothing was bought" in caplog.text


def test_a_run_with_nothing_to_type_into_is_refused_rather_than_assumed(
    fake_agent, monkeypatch, caplog
) -> None:
    """Silence is not consent: a script that piped in nothing would otherwise
    have bought something."""
    fake_agent["result"] = PAYABLE
    monkeypatch.setattr(main_module.sys, "stdin", Typed("", tty=False))

    with caplog.at_level(logging.ERROR):
        assert main(["headphones", "--pay"]) == main_module.PAYMENT_FAILED

    assert "no terminal to ask at" in caplog.text


def test_the_top_product_is_paid_for_in_the_currency_the_run_was_counted_in(
    fake_agent, monkeypatch, caplog
) -> None:
    """One euro listing and one dollar listing tie, and the run counts in euros, the one
    it saw first. Voting again over the ranking, where the dollar Sony comes first, the
    cart was in dollars: the Sony was bought at a price the run had never placed, and so
    never held to a budget either (ADR-0056)."""
    bose = Product(name="Bose QC45", price=299.0, currency="EUR", url="https://shop.example/b")
    sony = payable_product(rating=4.8, review_count=3200)
    fake_agent["result"] = rank_products([bose, sony])
    monkeypatch.setattr(main_module.sys, "stdin", Typed("yes\n"))

    with caplog.at_level(logging.ERROR):
        assert main(["headphones", "--pay"]) == main_module.PAYMENT_FAILED

    assert "this run counts in EUR" in caplog.text


def test_a_paying_rail_with_nowhere_to_pay_is_a_usage_error(capsys) -> None:
    """The one refusal no single flag can make, said the way every other is."""
    with pytest.raises(SystemExit) as exit_code:
        main(["headphones", "--pay", "--rail", "http", "--merchant-url", ""])

    assert exit_code.value.code == 2, "a setting is wrong, not the run"
    said = capsys.readouterr().err
    assert "needs an address" in said
    assert "Traceback" not in said


def test_a_rail_the_environment_got_right_is_the_one_the_flags_default_to(
    monkeypatch,
) -> None:
    """The half the fallback above must not swallow: ``$BUY_AGENT_RAIL`` is how
    an operator points a whole machine at their own counterparty."""
    other = next(name for name in RAILS if name != next(iter(RAILS)))
    monkeypatch.setattr(main_module, "DEFAULT_RAIL", other)

    assert main_module._defaults().rail == other


@needs_ap2
@pytest.mark.parametrize("answer", ["y", "yes", "YES", " Yes \n"])
def test_the_short_and_the_shouted_yes_both_authorise(
    fake_agent, monkeypatch, answer
) -> None:
    """Somebody is being asked a yes/no question at a terminal, so the answers a
    terminal gets are the answers this takes."""
    fake_agent["result"] = PAYABLE
    monkeypatch.setattr(main_module.sys, "stdin", Typed(answer))

    assert main(["headphones", "--pay"]) == 0


@needs_ap2
def test_the_receipt_is_logged_with_the_merchant_and_what_became_of_it(
    fake_agent, monkeypatch, caplog
) -> None:
    """The report on stdout is the products; what happened to the money goes to
    the progress stream, and it has to say who was paid and whether they were."""
    fake_agent["result"] = PAYABLE
    monkeypatch.setattr(main_module.sys, "stdin", Typed("yes\n"))

    with caplog.at_level(logging.INFO):
        main(["headphones", "--pay"])

    assert "AudioSite" in caplog.text
    assert "Nothing was charged" in caplog.text


# -- the currency and the backend (ADR-0056, ADR-0057) -------------------------


# -- what the request itself asks for (ADR-0059) -------------------------------


def test_a_bound_the_request_asks_for_is_offered_and_not_applied(
    fake_agent, caplog
) -> None:
    """The line names the words it read and the flag that would enforce them; the run
    is the run somebody asked for."""
    with caplog.at_level(logging.INFO, logger="buy_agent"):
        main(["headphones under $200"])

    assert 'Your request says "under $200"' in caplog.text
    assert "--max-price 200 is what would enforce it" in caplog.text
    assert fake_agent["config"].max_price is None


def test_a_bound_already_set_does_not_stop_the_next_one_being_offered(
    fake_agent, caplog
) -> None:
    """Skipped one at a time: the budget was given as a flag, the rating was not."""
    with caplog.at_level(logging.INFO, logger="buy_agent"):
        main(["headphones under $200 with at least 4.5 stars", "--max-price", "150"])

    assert "--max-price" not in caplog.text
    assert "--min-rating 4.5 is what would enforce it" in caplog.text


# -- the run journal (ADR-0060) ------------------------------------------------


def test_a_run_is_written_down_and_the_next_one_says_what_moved(
    fake_agent, capsys
) -> None:
    """The reason to run the same search twice."""
    fake_agent["result"] = [
        ranked_product(Product(name="Sage", price=349.0, currency="USD"), score=0.9, rank=1)
    ]
    main(["espresso machine"])

    fake_agent["result"] = [
        ranked_product(Product(name="Sage", price=329.0, currency="USD"), score=0.9, rank=1)
    ]
    capsys.readouterr()
    assert main(["espresso machine", "--compare"]) == 0

    report = capsys.readouterr().out
    assert "WHAT CHANGED SINCE" in report
    assert "20.00 USD cheaper" in report


def test_a_run_with_no_journal_writes_nothing_down(fake_agent, capsys, caplog) -> None:
    main(["espresso machine", "--no-journal"])
    capsys.readouterr()

    with caplog.at_level(logging.INFO, logger="buy_agent"):
        main(["espresso machine", "--compare", "--no-journal"])

    assert "--compare has nothing to read" in caplog.text
    assert "WHAT CHANGED" not in capsys.readouterr().out


def test_comparing_a_search_never_run_before_says_so(fake_agent, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="buy_agent"):
        main(["espresso machine", "--compare"])

    assert "Nothing to compare" in caplog.text
    assert "--compare has nothing to read" not in caplog.text, "the journal is on"
