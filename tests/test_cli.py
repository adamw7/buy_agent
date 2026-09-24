"""Argument wiring and exit codes for  python -m buy_agent."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

import buy_agent.__main__ as main_module
from buy_agent.__main__ import NOTHING_FOUND, build_parser, main
from buy_agent.agent import ModelUnavailableError
from buy_agent.api import results_payload
from buy_agent.config import LIMITS, AgentConfig
from buy_agent.models import Product
from buy_agent.providers import LITELLM, PROVIDERS, VLLM
from buy_agent.rails import RAILS
from buy_agent.search import SearchError
from buy_agent.sources import Source
from tests.conftest import (
    enrolled_key,
    needs_ap2,
    open_mandate,
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


def test_a_run_lets_go_of_its_agent_however_it_went(fake_agent) -> None:
    """The process is about to end either way, so this is the smaller half of the
    rule -- but it is the same rule the server's door keeps, and one place for it
    to be true is not two places for it to disagree."""
    assert main(["gaming laptop"]) == 0

    assert fake_agent["closed"] == 1


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


def test_json_output_is_written_when_asked(fake_agent, tmp_path) -> None:
    destination = tmp_path / "products.json"

    assert main(["headphones", "--json", str(destination)]) == 0

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert [entry["rank"] for entry in payload] == [1, 2]
    assert payload[0]["name"] == "Sony WH-1000XM5"
    assert payload[0]["score"] == 0.9


def test_no_json_file_is_written_by_default(fake_agent, tmp_path) -> None:
    main(["headphones"])
    assert list(tmp_path.iterdir()) == []


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


def test_the_wait_on_the_model_reaches_the_config(fake_agent) -> None:
    main(["headphones", "--model-timeout", "45"])

    assert fake_agent["config"].model_timeout == 45.0


def test_the_wait_on_the_model_defaults_to_the_config_s_own(fake_agent) -> None:
    main(["headphones"])

    assert fake_agent["config"].model_timeout == AgentConfig().model_timeout


def test_thinking_can_be_forced_on(fake_agent) -> None:
    main(["headphones", "--think"])
    assert fake_agent["config"].reasoning is True


def test_context_and_thinking_default_to_the_config(fake_agent) -> None:
    """Every flag defaults to its AgentConfig field, thinking mode included."""
    main(["headphones"])
    config = fake_agent["config"]
    assert config.num_ctx == 16384
    assert config.reasoning is False


def test_the_cpu_only_switch_reaches_the_config(fake_agent) -> None:
    main(["headphones", "--cpu-only"])

    assert fake_agent["config"].cpu_only is True


def test_the_cpu_only_switch_defaults_to_the_config_s_own(fake_agent) -> None:
    main(["headphones"])

    assert fake_agent["config"].cpu_only is AgentConfig().cpu_only


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


def test_flag_defaults_are_the_config_s_own(fake_agent) -> None:
    """The two are wired together so they cannot drift apart."""
    main(["headphones"])
    config = fake_agent["config"]
    defaults = AgentConfig()

    assert config.model == defaults.model
    assert config.base_url == defaults.base_url
    assert config.region == defaults.region
    assert config.temperature == defaults.temperature


def test_no_source_flag_means_the_whole_web(fake_agent) -> None:
    main(["headphones"])

    assert fake_agent["config"].sources == ()


def test_a_source_flag_reaches_the_config_read_down_to_its_domain(fake_agent) -> None:
    main(["headphones", "--source", "https://www.rtings.com/headphones"])

    assert fake_agent["config"].sources == (
        Source(spec="https://www.rtings.com/headphones", domain="rtings.com", term="headphones"),
    )


def test_the_source_flag_repeats_to_name_several(fake_agent) -> None:
    main(["headphones", "--source", "rtings.com", "--source", "@mkbhd"])

    assert [source.domain for source in fake_agent["config"].sources] == [
        "rtings.com",
        "youtube.com",
    ]


def test_two_flags_naming_one_site_are_one_source(fake_agent) -> None:
    """Which is why the flags are parsed together: a second identical search
    would halve what the other sources are allowed to return."""
    main(["headphones", "--source", "@mkbhd", "--source", "youtube.com/@mkbhd"])

    assert fake_agent["config"].sources == (
        Source(spec="@mkbhd", domain="youtube.com", term="@mkbhd"),
    )


def test_one_flag_may_hold_several_the_way_the_web_form_does(fake_agent) -> None:
    main(["headphones", "--source", "rtings.com,notebookcheck.net"])

    assert [source.domain for source in fake_agent["config"].sources] == [
        "rtings.com",
        "notebookcheck.net",
    ]


def test_a_source_that_names_no_site_is_a_usage_error_that_says_what_does(capsys) -> None:
    """argparse throws a type function's ValueError away, so the reason is raised
    as the error it prints -- without it the shopper is told only "invalid value"."""
    with pytest.raises(SystemExit) as exit_info:
        main(["headphones", "--source", "Marques Brownlee"])

    assert exit_info.value.code == 2
    assert "@mkbhd" in capsys.readouterr().err


@pytest.mark.parametrize("spec", ["", "   ", ","])
def test_a_source_flag_naming_nothing_is_a_usage_error_too(spec: str, capsys) -> None:
    """And the one that used to be silent, rather than an empty report."""
    with pytest.raises(SystemExit) as exit_info:
        main(["headphones", "--source", spec])

    assert exit_info.value.code == 2
    printed = capsys.readouterr().err
    assert "cannot be blank" in printed
    assert "@mkbhd" in printed


def test_a_blank_source_among_real_ones_is_still_the_real_ones(fake_agent) -> None:
    """The refusal is per flag and about that flag, so a trailing separator inside
    a spec that does name sites is nothing to complain about."""
    main(["headphones", "--source", "rtings.com,", "--source", "@mkbhd"])

    assert [source.domain for source in fake_agent["config"].sources] == [
        "rtings.com",
        "youtube.com",
    ]


def test_a_region_that_is_not_a_region_is_a_usage_error_that_says_what_is(capsys) -> None:
    """The one search setting that otherwise fails by returning nothing, so it is
    refused before the run rather than blamed on the web afterwards (ADR-0031)."""
    with pytest.raises(SystemExit) as exit_info:
        main(["headphones", "--region", "us_en"])

    assert exit_info.value.code == 2
    assert "us-en" in capsys.readouterr().err


def test_a_region_reaches_the_config_lower_cased(fake_agent) -> None:
    """An engine is handed the halves as they were typed, so the case matters."""
    main(["headphones", "--region", "PL-PL"])

    assert fake_agent["config"].region == "pl-pl"


def test_the_base_url_flag_reaches_the_config(fake_agent) -> None:
    main(["headphones", "--base-url", "http://ollama.internal:11434"])

    assert fake_agent["config"].base_url == "http://ollama.internal:11434"


def test_the_provider_flag_reaches_the_config(fake_agent) -> None:
    main(["headphones", "--provider", "vllm"])

    assert fake_agent["config"].provider == "vllm"


def test_choosing_a_provider_brings_its_model_and_its_server_with_it(fake_agent) -> None:
    """--provider on its own is the whole command for someone running a vLLM: the
    two flags that would otherwise have to follow it default to that provider's
    own, because neither has one right answer until the provider is known."""
    main(["headphones", "--provider", "vllm"])
    config = fake_agent["config"]

    assert (config.model, config.base_url) == (VLLM.model, VLLM.base_url)


def test_choosing_a_litellm_proxy_brings_its_alias_and_its_address(fake_agent) -> None:
    """The same complete choice for the third server: the proxy's placeholder alias and
    port 4000, never the Ollama tag the run would otherwise start on (ADR-0068)."""
    main(["headphones", "--provider", "litellm"])
    config = fake_agent["config"]

    assert config.provider == "litellm"
    assert (config.model, config.base_url) == (LITELLM.model, LITELLM.base_url)


def test_a_named_model_still_wins_over_the_provider_default(fake_agent) -> None:
    main(["headphones", "--provider", "vllm", "--model", "meta-llama/Llama-3.1-8B"])

    assert fake_agent["config"].model == "meta-llama/Llama-3.1-8B"


def test_a_provider_nothing_can_serve_is_a_usage_error(capsys) -> None:
    """argparse chooses from the same table the config validates against, so the
    two cannot disagree about what is on offer."""
    with pytest.raises(SystemExit) as exit_info:
        main(["headphones", "--provider", "llama.cpp"])

    assert exit_info.value.code == 2
    assert "vllm" in capsys.readouterr().err


def test_there_is_no_flag_for_the_api_key() -> None:
    """A secret typed on a command line lands in a shell history. $VLLM_API_KEY is
    the only way in, which is what keeps it out of one."""
    flags = {option for action in build_parser()._actions for option in action.option_strings}

    assert not [flag for flag in flags if "key" in flag]


def test_the_help_names_every_provider_default_rather_than_one(capsys) -> None:
    """--model and --base-url have a default per provider, so quoting only the
    one the server started on would be wrong for whoever passes --provider."""
    with pytest.raises(SystemExit):
        main(["--help"])

    printed = capsys.readouterr().out
    for server in PROVIDERS.values():
        assert server.model in printed
        assert server.base_url in printed


def test_the_help_names_every_provider_s_variables_and_what_a_proxy_ignores(capsys) -> None:
    """--help is the CLI's only documentation, so every server's variables are in it,
    and so is why a proxy takes neither server-side setting."""
    with pytest.raises(SystemExit):
        main(["--help"])
    printed = " ".join(capsys.readouterr().out.split())

    for name in ("OLLAMA", "VLLM", "LITELLM"):
        assert f"${name}_MODEL" in printed and f"${name}_HOST" in printed
    assert printed.count("a LiteLLM proxy leaves it to the server it routes to") == 2


def test_fetching_is_on_unless_no_fetch_is_passed(fake_agent) -> None:
    main(["headphones"])
    assert fake_agent["config"].fetch_pages is True

    main(["headphones", "--no-fetch"])
    assert fake_agent["config"].fetch_pages is False


def test_sorting_defaults_to_the_blended_score(fake_agent) -> None:
    main(["headphones"])

    assert fake_agent["sort_by"] == "score"


def test_a_missing_request_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([])

    assert exit_info.value.code == 2


def test_an_unknown_sort_criterion_is_rejected() -> None:
    with pytest.raises(SystemExit):
        main(["headphones", "--sort-by", "colour"])


def test_a_non_numeric_count_is_rejected() -> None:
    with pytest.raises(SystemExit):
        main(["headphones", "--top", "a few"])


def test_verbose_reaches_the_logging_setup(fake_agent, monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        "buy_agent.__main__.configure_logging", lambda **kwargs: captured.update(kwargs)
    )

    main(["headphones", "-v"])

    assert captured == {"verbose": True}


def test_scores_are_rounded_in_the_json(fake_agent, tmp_path) -> None:
    fake_agent["result"] = [
        ranked_product(Product(name="Thing"), score=0.123456789, rank=1)
    ]
    destination = tmp_path / "out.json"

    main(["headphones", "--json", str(destination)])

    assert json.loads(destination.read_text(encoding="utf-8"))[0]["score"] == 0.1235


def test_the_json_writes_a_currency_sign_as_itself(fake_agent, tmp_path) -> None:
    """A file somebody opens reads "zł", not an escape sequence standing for it."""
    fake_agent["result"] = [ranked_product(Product(name="Słuchawki €"), score=0.5, rank=1)]
    destination = tmp_path / "out.json"

    main(["headphones", "--json", str(destination)])

    written = destination.read_text(encoding="utf-8")
    assert "Słuchawki €" in written
    assert json.loads(written)[0]["name"] == "Słuchawki €"


def test_the_json_carries_every_product_field(fake_agent, tmp_path) -> None:
    destination = tmp_path / "out.json"

    main(["headphones", "--json", str(destination)])

    entry = json.loads(destination.read_text(encoding="utf-8"))[0]
    assert set(entry) >= {
        "rank",
        "score",
        "name",
        "price",
        "currency",
        "rating",
        "review_count",
        "seller",
        "url",
        "opinions",
        "notes",
    }


def test_the_json_is_shaped_the_way_the_api_shapes_a_run(fake_agent, tmp_path) -> None:
    """One shaping for every way a run leaves the process."""
    destination = tmp_path / "out.json"

    main(["headphones", "--json", str(destination)])

    written = json.loads(destination.read_text(encoding="utf-8"))
    assert written == json.loads(json.dumps(results_payload(RANKED)))


def test_the_json_holds_every_product_not_just_the_top_n(fake_agent, tmp_path) -> None:
    destination = tmp_path / "out.json"

    main(["headphones", "--top", "1", "--json", str(destination)])

    assert len(json.loads(destination.read_text(encoding="utf-8"))) == 2


def test_no_json_is_written_when_the_run_fails(fake_agent, tmp_path) -> None:
    """The failure exit comes before the file is written."""
    fake_agent["result"] = SearchError("rate limited")
    destination = tmp_path / "out.json"

    assert main(["headphones", "--json", str(destination)]) == 1
    assert not destination.exists()


def test_an_empty_run_still_writes_the_json_it_was_asked_for(fake_agent, tmp_path) -> None:
    """A script waiting on this file wants an answer, not the absence of one."""
    fake_agent["result"] = []
    destination = tmp_path / "out.json"

    assert main(["nonexistent gadget", "--json", str(destination)]) == NOTHING_FOUND
    assert json.loads(destination.read_text(encoding="utf-8")) == []


def test_a_stale_json_file_is_overwritten_by_an_empty_run(fake_agent, tmp_path) -> None:
    destination = tmp_path / "out.json"
    destination.write_text('[{"name": "yesterday\'s answer"}]', encoding="utf-8")
    fake_agent["result"] = []

    main(["nonexistent gadget", "--json", str(destination)])

    assert "yesterday" not in destination.read_text(encoding="utf-8")


def test_an_unwritable_json_path_is_an_exit_code_not_a_traceback(
    fake_agent, tmp_path, caplog
) -> None:
    """A mistyped ``--json`` path must not end a minute of work in a stack trace."""
    destination = tmp_path / "no-such-directory" / "out.json"

    with caplog.at_level(logging.ERROR, logger="buy_agent"):
        assert main(["headphones", "--json", str(destination)]) == 1

    assert str(destination) in caplog.text
    assert not destination.exists()


def test_writing_the_json_is_logged(fake_agent, tmp_path, caplog) -> None:
    destination = tmp_path / "out.json"

    with caplog.at_level(logging.INFO, logger="buy_agent"):
        main(["headphones", "--json", str(destination)])

    assert "Wrote 2 products" in caplog.text


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--results", "0"),
        ("--results", "51"),
        ("--top", "0"),
        ("--temperature", "2.5"),
        ("--temperature", "-1"),
        ("--num-ctx", "0"),
        ("--model-timeout", "0"),
        ("--model-timeout", "3601"),
    ],
)
def test_a_number_outside_its_range_is_a_usage_error(flag: str, value: str) -> None:
    """The same range the API holds a request to, refused before the run rather
    than after: --results 0 otherwise searches the web, reads ten pages and then
    asks the model for no products at all."""
    with pytest.raises(SystemExit) as exit_info:
        main(["headphones", flag, value])

    assert exit_info.value.code == 2


def test_a_refused_number_is_told_the_range_and_what_it_gave(capsys) -> None:
    """Exit 2 alone is a shopper reading "invalid value" and guessing."""
    with pytest.raises(SystemExit):
        main(["headphones", "--results", "500"])

    error = capsys.readouterr().err
    minimum, maximum = LIMITS["num_products"]
    assert f"must be between {minimum} and {maximum}; got 500" in error


@pytest.mark.parametrize(
    "field", ["num_products", "top_n", "temperature", "num_ctx", "model_timeout"]
)
def test_the_bounds_are_the_config_s_own(field: str) -> None:
    """Written down here as well, the CLI would come to accept what the API refuses."""
    assert field in LIMITS


@pytest.mark.parametrize(("flag", "value"), [("--results", "50"), ("--temperature", "2")])
def test_the_edge_of_the_range_is_inside_it(fake_agent, flag: str, value: str) -> None:
    assert main(["headphones", flag, value]) == 0


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


def test_a_context_window_the_provider_takes_is_not_called_out(fake_agent, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        main(["headphones", "--num-ctx", "4096"])

    assert "ignored" not in caplog.text


def test_a_cpu_only_run_the_provider_ignores_is_called_out(fake_agent, caplog) -> None:
    """vLLM picks its device with --device when it starts."""
    with caplog.at_level(logging.WARNING):
        main(["headphones", "--provider", "vllm", "--cpu-only"])

    assert "--cpu-only is ignored" in caplog.text


def test_leaving_the_gpu_alone_is_not_called_out(fake_agent, caplog) -> None:
    """Only a switch the shopper actually threw is worth a warning."""
    with caplog.at_level(logging.WARNING):
        main(["headphones", "--provider", "vllm", "--no-cpu-only"])

    assert "ignored" not in caplog.text


def test_a_cpu_only_run_the_provider_takes_is_not_called_out(fake_agent, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        main(["headphones", "--cpu-only"])

    assert "ignored" not in caplog.text


@pytest.mark.parametrize(
    ("flag", "value"),
    [("--rail", "http"), ("--merchant-url", "https://pay.example"), ("--spend-limit", "250")],
)
def test_a_paying_flag_without_pay_is_called_out(
    fake_agent, caplog, flag: str, value: str
) -> None:
    """The form draws none of these until Pay for the top product is on."""
    with caplog.at_level(logging.WARNING):
        main(["headphones", flag, value])

    assert f"Nothing will be bought: {flag} does nothing without --pay." in caplog.text


def test_every_paying_flag_that_was_given_is_named_at_once(fake_agent, caplog) -> None:
    """One line for the lot: three warnings for one forgotten word is three
    things to read and one thing to fix."""
    with caplog.at_level(logging.WARNING):
        main(["headphones", "--rail", "http", "--spend-limit", "250"])

    assert "--rail, --spend-limit do nothing without --pay" in caplog.text


def test_the_paying_flags_are_not_called_out_on_a_run_that_pays(fake_agent, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        main(["headphones", "--pay", "--rail", "dry-run", "--spend-limit", "250"])

    assert "without --pay" not in caplog.text


def test_a_run_that_asked_for_none_of_them_is_not_called_out(fake_agent, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        main(["headphones"])

    assert "without --pay" not in caplog.text


def test_a_rail_the_environment_set_is_not_read_as_asking_to_buy(
    fake_agent, caplog, monkeypatch
) -> None:
    """``$BUY_AGENT_RAIL`` is how a machine is pointed at one counterparty for good, and a
    standing answer is not somebody asking to buy something."""
    monkeypatch.setattr(main_module, "DEFAULT_RAIL", "http")

    with caplog.at_level(logging.WARNING):
        main(["headphones"])

    assert "without --pay" not in caplog.text


def test_every_flag_is_documented_in_the_help() -> None:
    """--help is the only documentation the CLI has."""
    help_text = build_parser().format_help()

    for flag in (
        "--model",
        "--base-url",
        "--results",
        "--top",
        "--sort-by",
        "--region",
        "--source",
        "--temperature",
        "--num-ctx",
        "--think",
        "--no-think",
        "--cpu-only",
        "--no-cpu-only",
        "--no-fetch",
        "--json",
        "--verbose",
    ):
        assert flag in help_text, flag


def test_only_the_three_sort_criteria_are_offered() -> None:
    """Every choice here needs a branch in rank_products, so the set is closed."""
    action = {action.dest: action for action in build_parser()._actions}["sort_by"]

    assert set(action.choices) == {"score", "price", "rating"}


def test_the_module_is_runnable_as_a_script() -> None:
    """python -m buy_agent is the documented entry point; it must reach main()."""
    completed = subprocess.run(
        [sys.executable, "-m", "buy_agent"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 2, "no request is a usage error, not a traceback"
    assert "usage: buy_agent" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_a_misspelt_provider_environment_is_a_usage_error(monkeypatch, capsys) -> None:
    """``choices`` never sees a default, so the environment used to walk past it."""
    monkeypatch.setattr(main_module, "DEFAULT_PROVIDER", "olama")
    parser = main_module.build_parser()

    with pytest.raises(SystemExit) as exit_code:
        parser.parse_args(["headphones"])

    assert exit_code.value.code == 2
    assert "ollama, vllm" in capsys.readouterr().err


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


def test_the_bounds_reach_the_config(fake_agent) -> None:
    main(["headphones", "--max-price", "200", "--min-rating", "4.5", "--min-reviews", "100"])
    config = fake_agent["config"]

    assert (config.max_price, config.min_rating, config.min_reviews) == (200.0, 4.5, 100)


def test_no_bound_flags_means_no_bounds(fake_agent) -> None:
    main(["headphones"])
    config = fake_agent["config"]

    assert (config.max_price, config.min_rating, config.min_reviews) == (None, None, None)


@pytest.mark.parametrize(
    ("flag", "value"),
    [("--max-price", "0"), ("--min-rating", "6"), ("--min-reviews", "-1")],
)
def test_a_bound_outside_its_range_is_a_usage_error(flag: str, value: str, capsys) -> None:
    """A usage error rather than a minute wasted, and the same range the API
    holds it to -- both read ``config.LIMITS`` (ADR-0033)."""
    with pytest.raises(SystemExit) as excinfo:
        main(["headphones", flag, value])

    assert excinfo.value.code == 2
    assert "must be between" in capsys.readouterr().err


def test_the_cache_lifetime_is_the_flag_s_or_the_config_s_own(fake_agent) -> None:
    main(["headphones", "--cache-ttl", "0"])
    assert fake_agent["config"].cache_ttl == 0.0

    main(["headphones"])
    assert fake_agent["config"].cache_ttl == AgentConfig().cache_ttl


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


def test_nothing_is_paid_for_unless_it_was_asked_for(fake_agent, monkeypatch) -> None:
    """The switch is off, so a run is exactly the run it always was."""
    paid = []
    monkeypatch.setattr(main_module.payment, "pay_for", lambda *a, **k: paid.append(a))
    fake_agent["result"] = PAYABLE

    assert main(["headphones"]) == 0
    assert paid == []


@needs_ap2
def test_the_dry_run_pays_for_the_top_product_once_it_is_approved(
    fake_agent, monkeypatch, capsys
) -> None:
    fake_agent["result"] = PAYABLE
    monkeypatch.setattr(main_module.sys, "stdin", Typed("yes\n"))

    assert main(["headphones", "--pay"]) == 0
    assert "Type yes to authorise" in capsys.readouterr().err


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
    assert "will NOT be charged" in shown


def test_anything_but_yes_buys_nothing(fake_agent, monkeypatch, caplog) -> None:
    fake_agent["result"] = PAYABLE
    monkeypatch.setattr(main_module.sys, "stdin", Typed("no\n"))

    with caplog.at_level(logging.WARNING):
        assert main(["headphones", "--pay"]) == main_module.PAYMENT_FAILED

    assert "was not approved" in caplog.text


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


def test_a_product_no_source_priced_is_refused_with_the_reason(
    fake_agent, monkeypatch, caplog
) -> None:
    fake_agent["result"] = RANKED  # priced, but with no page and no currency
    monkeypatch.setattr(main_module.sys, "stdin", Typed("yes\n"))

    with caplog.at_level(logging.ERROR):
        assert main(["headphones", "--pay"]) == main_module.PAYMENT_FAILED

    assert "not an amount" in caplog.text


@needs_ap2
def test_an_open_mandate_pays_without_asking_anybody(
    fake_agent, monkeypatch, tmp_path, caplog
) -> None:
    agent, _issuer = open_mandate(tmp_path, monkeypatch)
    enrolled_key(tmp_path, monkeypatch, agent)
    fake_agent["result"] = PAYABLE
    # No stdin at all: the point is that nothing asks.
    monkeypatch.setattr(main_module.sys, "stdin", Typed("", tty=False))

    with caplog.at_level(logging.INFO):
        assert main(["headphones", "--pay"]) == 0

    assert "an open mandate" in caplog.text


@needs_ap2
def test_the_authorisation_is_logged_with_what_it_covers(
    fake_agent, monkeypatch, caplog
) -> None:
    fake_agent["result"] = PAYABLE
    monkeypatch.setattr(main_module.sys, "stdin", Typed("yes\n"))

    with caplog.at_level(logging.INFO):
        main(["headphones", "--pay"])

    assert "Authorisation" in caplog.text
    assert "329.99 USD" in caplog.text


def test_a_run_that_found_nothing_is_still_nothing_found_and_not_a_failed_payment(
    fake_agent,
) -> None:
    """There was nothing to buy, and the exit code a script branches on should
    say which of the two happened."""
    fake_agent["result"] = []

    assert main(["headphones", "--pay"]) == NOTHING_FOUND


def test_the_report_and_the_json_survive_a_payment_that_failed(
    fake_agent, monkeypatch, tmp_path
) -> None:
    """A purchase that fails must not cost the shopper the answer they already
    paid a minute of searching for."""
    fake_agent["result"] = PAYABLE
    monkeypatch.setattr(main_module.sys, "stdin", Typed("no\n"))
    out = tmp_path / "results.json"

    assert main(["headphones", "--pay", "--json", str(out)]) == main_module.PAYMENT_FAILED
    assert json.loads(out.read_text())[0]["name"] == "Sony WH-1000XM5"


def test_the_spend_limit_reaches_the_config(fake_agent) -> None:
    main(["headphones", "--spend-limit", "250"])

    assert fake_agent["config"].spend_limit == 250


def test_the_rail_and_its_address_reach_the_config(fake_agent) -> None:
    main(["headphones", "--rail", "http", "--merchant-url", "https://pay.example/"])

    config = fake_agent["config"]
    assert config.rail == "http"
    # The trailing slash is dropped where the address is resolved, so the rail
    # builds `{url}/checkout` and never `{url}//checkout`.
    assert config.merchant_url == "https://pay.example"


def test_a_rail_nothing_can_pay_through_is_a_usage_error(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["headphones", "--rail", "paypal"])

    assert "paypal" in capsys.readouterr().err


def test_a_paying_rail_with_nowhere_to_pay_is_a_usage_error(capsys) -> None:
    """The one refusal no single flag can make, said the way every other is."""
    with pytest.raises(SystemExit) as exit_code:
        main(["headphones", "--pay", "--rail", "http", "--merchant-url", ""])

    assert exit_code.value.code == 2, "a setting is wrong, not the run"
    said = capsys.readouterr().err
    assert "needs an address" in said
    assert "Traceback" not in said


def test_a_misspelt_rail_environment_is_a_usage_error(monkeypatch, capsys) -> None:
    """The rail's half of the provider mistake above, and the same answer."""
    monkeypatch.setattr(main_module, "DEFAULT_RAIL", "dryrun")
    parser = main_module.build_parser()

    with pytest.raises(SystemExit) as exit_code:
        parser.parse_args(["headphones"])

    assert exit_code.value.code == 2
    assert "dry-run, http" in capsys.readouterr().err


def test_the_module_still_imports_on_a_rail_the_environment_got_wrong() -> None:
    """The other half, and the only way to ask it: a real variable, a real import."""
    completed = subprocess.run(
        [sys.executable, "-m", "buy_agent", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        env={**os.environ, "BUY_AGENT_RAIL": "dryrun"},
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stderr
    assert "--rail" in completed.stdout, "the help still lists the flag that would fix it"


def test_a_rail_the_environment_got_right_is_the_one_the_flags_default_to(
    monkeypatch,
) -> None:
    """The half the fallback above must not swallow: ``$BUY_AGENT_RAIL`` is how
    an operator points a whole machine at their own counterparty."""
    other = next(name for name in RAILS if name != next(iter(RAILS)))
    monkeypatch.setattr(main_module, "DEFAULT_RAIL", other)

    assert main_module._defaults().rail == other


def test_a_spend_limit_outside_its_range_is_a_usage_error(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["headphones", "--spend-limit", "0"])

    assert "between" in capsys.readouterr().err


def test_the_exit_codes_the_help_lists_are_the_ones_main_returns() -> None:
    """`--help` is the CLI's only documentation, so a sixth code added without a
    line there is one a script cannot branch on."""
    epilog = build_parser().epilog or ""

    assert f"  {main_module.PAYMENT_FAILED}  " in epilog
    assert f"  {NOTHING_FOUND}  " in epilog


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


@pytest.mark.parametrize("answer", ["n", "no", "", "yeah", "yes please"])
def test_anything_that_is_not_yes_is_not_yes(fake_agent, monkeypatch, answer) -> None:
    """Consent is the narrow reading, deliberately: "yeah" is somebody typing
    while thinking, and this is the last chance to be sure."""
    fake_agent["result"] = PAYABLE
    monkeypatch.setattr(main_module.sys, "stdin", Typed(answer))

    assert main(["headphones", "--pay"]) == main_module.PAYMENT_FAILED


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


def test_the_prompt_says_which_page_the_product_came_off(
    fake_agent, monkeypatch, capsys
) -> None:
    """The merchant is the site the page came from, so the page is the one thing
    that lets somebody check who they are about to pay before they say yes."""
    fake_agent["result"] = PAYABLE
    monkeypatch.setattr(main_module.sys, "stdin", Typed("no\n"))

    main(["headphones", "--pay"])

    assert "https://audiosite.example/xm5" in capsys.readouterr().err


def test_a_spend_limit_the_top_product_breaks_buys_nothing(
    fake_agent, monkeypatch, caplog
) -> None:
    fake_agent["result"] = PAYABLE
    monkeypatch.setattr(main_module.sys, "stdin", Typed("yes\n"))

    with caplog.at_level(logging.ERROR):
        assert main(["headphones", "--pay", "--spend-limit", "100"]) == main_module.PAYMENT_FAILED

    assert "spend limit" in caplog.text


def test_nothing_is_asked_before_the_product_is_known_to_be_payable(
    fake_agent, monkeypatch, capsys
) -> None:
    """The cart is built first, so a product nothing can pay for is refused with
    its reason rather than after somebody has been made to type yes."""
    fake_agent["result"] = RANKED
    monkeypatch.setattr(main_module.sys, "stdin", Typed("yes\n"))

    main(["headphones", "--pay"])

    assert "Type yes to authorise" not in capsys.readouterr().err


# -- the currency and the backend (ADR-0056, ADR-0057) -------------------------


def test_a_named_currency_reaches_the_config_as_a_code(fake_agent) -> None:
    """Folded the way a page's spelling is, so what a shopper types is what they read."""
    main(["headphones", "--currency", "pln"])

    assert fake_agent["config"].currency == "PLN"


def test_no_currency_flag_leaves_the_set_to_vote(fake_agent) -> None:
    main(["headphones"])

    assert fake_agent["config"].currency == ""


def test_a_currency_this_run_cannot_place_is_a_usage_error_that_names_some(capsys) -> None:
    """Refused where a region is, and for the same reason: left to the run it is not a
    failure at all, only a report scored on nothing (ADR-0056)."""
    with pytest.raises(SystemExit) as exit_info:
        main(["headphones", "--currency", "dollarydoos"])

    assert exit_info.value.code == 2
    assert "USD" in capsys.readouterr().err


def test_a_named_backend_reaches_the_config(fake_agent) -> None:
    main(["headphones", "--backend", "searxng"])

    assert fake_agent["config"].backend == "searxng"
    assert fake_agent["config"].search_backend.label == "SearXNG"


def test_a_backend_nothing_can_search_is_a_usage_error(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["headphones", "--backend", "bing"])

    assert exit_info.value.code == 2
    assert "bing" in capsys.readouterr().err


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


def test_a_bound_the_flag_would_refuse_is_not_offered(fake_agent, caplog) -> None:
    """The form drops a figure outside the setting's range (``api.bounds_payload``), and
    so does this door: offered, "under $0.50" named a ``--max-price 0.5`` that the flag
    itself refuses as a usage error."""
    with caplog.at_level(logging.INFO, logger="buy_agent"):
        main(["a cable under $0.50"])

    assert "--max-price" not in caplog.text
    assert fake_agent["config"].max_price is None


def test_a_bound_already_set_is_not_offered_back(fake_agent, caplog) -> None:
    """Saying it again would read as the run having taken the words for the number."""
    with caplog.at_level(logging.INFO, logger="buy_agent"):
        main(["headphones under $200", "--max-price", "150"])

    assert "--max-price" not in caplog.text
    assert fake_agent["config"].max_price == 150


def test_a_number_about_something_else_is_not_offered_as_a_bound(
    fake_agent, caplog
) -> None:
    with caplog.at_level(logging.INFO, logger="buy_agent"):
        main(["headphones with 200 hours of battery"])

    assert "would enforce it" not in caplog.text


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


def test_the_comparison_is_not_printed_unless_it_was_asked_for(
    fake_agent, capsys
) -> None:
    """The report is the products; this is a second block somebody opted into."""
    main(["espresso machine"])
    capsys.readouterr()

    main(["espresso machine"])

    assert "WHAT CHANGED" not in capsys.readouterr().out


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
