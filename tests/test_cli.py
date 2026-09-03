"""Argument wiring and exit codes for  python -m buy_agent."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

import buy_agent.__main__ as main_module
from buy_agent.__main__ import NOTHING_FOUND, build_parser, main
from buy_agent.agent import ModelUnavailableError
from buy_agent.api import results_payload
from buy_agent.config import LIMITS, AgentConfig
from buy_agent.models import Product, RankedProduct
from buy_agent.providers import PROVIDERS, VLLM
from buy_agent.search import SearchError
from buy_agent.sources import Source

RANKED = [
    RankedProduct(product=Product(name="Sony WH-1000XM5", price=328.0), score=0.9, rank=1),
    RankedProduct(product=Product(name="Anker Q30", price=79.0), score=0.8, rank=2),
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

    monkeypatch.setattr("buy_agent.__main__.BuyAgent", Recorder)
    return captured


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
    """A run that worked and found nothing is not a run that failed.

    Both are non-zero, but a shell told they are the same 1 cannot tell "nobody
    sells this" from "the model server is down", and the two want different
    things done about them.
    """
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


def test_thinking_can_be_forced_on(fake_agent) -> None:
    main(["headphones", "--think"])
    assert fake_agent["config"].reasoning is True


def test_context_and_thinking_default_to_the_config(fake_agent) -> None:
    """Every flag defaults to its AgentConfig field, thinking mode included."""
    main(["headphones"])
    config = fake_agent["config"]
    assert config.num_ctx == 8192
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
        RankedProduct(product=Product(name="Thing"), score=0.123456789, rank=1)
    ]
    destination = tmp_path / "out.json"

    main(["headphones", "--json", str(destination)])

    assert json.loads(destination.read_text(encoding="utf-8"))[0]["score"] == 0.1235


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
    """One shaping for every way a run leaves the process. The file this writes,
    the API's answer and the file the page's Download results button hands over
    are the same document, so a field added to ``product_payload`` is in all
    three and the browser never has a shape of its own to write."""
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
    """A script waiting on this file wants an answer, not the absence of one.

    Skipped, the run leaves no file and no reason -- and leaves the *last* run's
    results sitting there looking current, which is worse than either.
    """
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
    """A mistyped ``--json`` path must not end a minute of work in a stack trace.

    The report is already on stderr by the time the file is written, so what
    failed is the copy. Exit 1 so a script notices, and say which path and why.
    """
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
    ],
)
def test_a_number_outside_its_range_is_a_usage_error(flag: str, value: str) -> None:
    """The same range the API holds a request to, refused before the run rather
    than after: --results 0 otherwise searches the web, reads ten pages and then
    asks the model for no products at all."""
    with pytest.raises(SystemExit) as exit_info:
        main(["headphones", flag, value])

    assert exit_info.value.code == 2


@pytest.mark.parametrize("field", ["num_products", "top_n", "temperature", "num_ctx"])
def test_the_bounds_are_the_config_s_own(field: str) -> None:
    """Written down here as well, the CLI would come to accept what the API refuses."""
    assert field in LIMITS


@pytest.mark.parametrize(("flag", "value"), [("--results", "50"), ("--temperature", "2")])
def test_the_edge_of_the_range_is_inside_it(fake_agent, flag: str, value: str) -> None:
    assert main(["headphones", flag, value]) == 0


def test_a_context_window_the_provider_ignores_is_called_out(fake_agent, caplog) -> None:
    """vLLM fixes its window with --max-model-len when it starts.

    The form disables the field and says so; the CLI has no field to disable, so
    it says it rather than dropping the number without a word.
    """
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
    """``choices`` never sees a default, so the environment used to walk past it.

    ``$BUY_AGENT_PROVIDER=olama`` reached ``AgentConfig`` -- at import time, in
    the module-level defaults, so the traceback came out before ``main`` ran and
    took ``--help`` with it. It is the CLI's own kind of mistake, so it gets the
    CLI's own answer: argparse's exit 2, carrying the servers that do exist.
    """
    monkeypatch.setattr(main_module, "DEFAULT_PROVIDER", "olama")
    parser = main_module.build_parser()

    with pytest.raises(SystemExit) as exit_code:
        parser.parse_args(["headphones"])

    assert exit_code.value.code == 2
    assert "ollama, vllm" in capsys.readouterr().err


def test_the_defaults_survive_a_provider_the_environment_got_wrong(monkeypatch) -> None:
    """The other half: the module still imports, so --help still lists them.

    Every field but the provider is a plain default that no environment variable
    can make unusable, and the one that can is refused above rather than here.
    """
    monkeypatch.setattr(main_module, "DEFAULT_PROVIDER", "olama")

    defaults = main_module._defaults()

    assert defaults.provider in PROVIDERS
    assert defaults.num_products == AgentConfig().num_products
