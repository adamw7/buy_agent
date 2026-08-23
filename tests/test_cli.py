"""Argument wiring and exit codes for  python -m buy_agent."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from buy_agent.__main__ import build_parser, main
from buy_agent.agent import OllamaUnavailableError
from buy_agent.config import AgentConfig
from buy_agent.models import Product, RankedProduct
from buy_agent.search import SearchError

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


def test_empty_result_exits_nonzero(fake_agent) -> None:
    fake_agent["result"] = []
    assert main(["nonexistent gadget"]) == 1


@pytest.mark.parametrize(
    "error",
    [
        OllamaUnavailableError("no model"),
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


def test_context_and_thinking_are_unset_by_default(fake_agent) -> None:
    """Unset must stay unset, so the default model keeps behaving as it did."""
    main(["headphones"])
    config = fake_agent["config"]
    assert config.num_ctx is None
    assert config.reasoning is None


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


def test_the_base_url_flag_reaches_the_config(fake_agent) -> None:
    main(["headphones", "--base-url", "http://ollama.internal:11434"])

    assert fake_agent["config"].base_url == "http://ollama.internal:11434"


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
    assert set(entry) == {
        "rank",
        "score",
        "name",
        "price",
        "currency",
        "rating",
        "review_count",
        "seller",
        "url",
        "notes",
    }


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


def test_writing_the_json_is_logged(fake_agent, tmp_path, caplog) -> None:
    destination = tmp_path / "out.json"

    with caplog.at_level(logging.INFO, logger="buy_agent"):
        main(["headphones", "--json", str(destination)])

    assert "Wrote 2 products" in caplog.text


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
