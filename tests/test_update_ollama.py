"""The script that brings Ollama's models up to date.

`scripts/update_ollama.py` decides what "updated" means -- a digest that moved
between two listings, not the `success` a pull prints either way -- and by the
rule the rest of this codebase follows, whatever decides an answer is tested.

The client is faked rather than reached: no test here talks to Ollama.
"""

from __future__ import annotations

from typing import Any

import pytest
from ollama import RequestError, ResponseError

from scripts.update_ollama import (
    Outcome,
    describe,
    digests,
    hint,
    main,
    report,
    short,
    status,
    stream,
    summary,
    update,
)


class FakeModel:
    def __init__(self, model: str, digest: str) -> None:
        self.model = model
        self.digest = digest


class FakeListing:
    def __init__(self, models: list[FakeModel]) -> None:
        self.models = models


class FakeProgress:
    def __init__(self, status: str) -> None:
        self.status = status


class FakeClient:
    """An Ollama whose pulls land the digests it was told they would.

    ``installed`` is what the server holds now; ``pulls`` is the digest a tag
    gets when it is pulled (a tag missing from it is already current); ``fails``
    is the error a pull of that tag raises instead.
    """

    def __init__(
        self,
        installed: dict[str, str] | None = None,
        pulls: dict[str, str] | None = None,
        fails: dict[str, Exception] | None = None,
    ) -> None:
        self.installed = dict(installed or {})
        self.pulls = dict(pulls or {})
        self.fails = dict(fails or {})
        self.pulled: list[str] = []
        self.listings = 0

    def list(self) -> FakeListing:
        self.listings += 1
        return FakeListing([FakeModel(tag, digest) for tag, digest in self.installed.items()])

    def pull(self, model: str, *, stream: bool = False) -> Any:
        self.pulled.append(model)
        if model in self.fails:
            raise self.fails[model]
        self.installed[model] = self.pulls.get(model, self.installed.get(model, "sha256:new"))
        return iter(
            [
                FakeProgress("pulling manifest"),
                FakeProgress("pulling manifest"),
                FakeProgress(""),
                FakeProgress("success"),
            ]
        )


def test_a_listing_is_read_as_tag_to_digest() -> None:
    """The digest is what a pull can be measured against; the tag alone is not."""
    client = FakeClient({"llama3.2:latest": "sha256:aaa", "qwen2.5:7b": "sha256:bbb"})
    assert digests(client) == {"llama3.2:latest": "sha256:aaa", "qwen2.5:7b": "sha256:bbb"}


def test_a_model_ollama_reports_without_a_name_is_not_a_model() -> None:
    """``list_models`` drops these too -- there is nothing to pull for a blank tag."""
    client = FakeClient()
    client.installed = {"": "sha256:aaa", "llama3.2:latest": ""}
    assert digests(client) == {"llama3.2:latest": ""}


def test_a_pull_reports_each_status_once_as_it_arrives() -> None:
    """A multi-gigabyte pull repeats a status hundreds of times; the changes are the news."""
    client = FakeClient({"llama3.2:latest": "sha256:aaa"})
    assert list(stream(client, "llama3.2:latest")) == ["pulling manifest", "success"]


def test_naming_no_model_updates_everything_installed_in_order() -> None:
    client = FakeClient({"qwen2.5:7b": "sha256:bbb", "llama3.2:latest": "sha256:aaa"})
    update(client, echo=lambda line: None)
    assert client.pulled == ["llama3.2:latest", "qwen2.5:7b"]


def test_naming_models_pulls_those_and_nothing_else() -> None:
    """Including a tag Ollama does not have yet -- naming it is how it is installed."""
    client = FakeClient({"qwen2.5:7b": "sha256:bbb"})
    update(client, ["llama3.2:latest"], echo=lambda line: None)
    assert client.pulled == ["llama3.2:latest"]


def test_a_digest_that_moved_is_an_update() -> None:
    client = FakeClient({"llama3.2:latest": "sha256:aaa"}, {"llama3.2:latest": "sha256:ccc"})
    (outcome,) = update(client, echo=lambda line: None)
    assert (outcome.before, outcome.after) == ("sha256:aaa", "sha256:ccc")
    assert status(outcome) == "updated"


def test_a_digest_that_did_not_move_is_already_current() -> None:
    """Which is the question `ollama pull` cannot answer: it says success either way."""
    client = FakeClient({"llama3.2:latest": "sha256:aaa"})
    (outcome,) = update(client, echo=lambda line: None)
    assert status(outcome) == "current"


def test_a_tag_that_was_not_there_is_installed_rather_than_updated() -> None:
    client = FakeClient()
    (outcome,) = update(client, ["llama3.2:latest"], echo=lambda line: None)
    assert status(outcome) == "installed"


def test_a_refused_pull_is_recorded_and_the_rest_still_run() -> None:
    """One unknown tag should not cost the models named after it their update."""
    client = FakeClient(
        {"llama3.2:latest": "sha256:aaa", "nope:1b": "sha256:bbb"},
        fails={"nope:1b": ResponseError("model not found", 404)},
    )
    outcomes = {outcome.model: outcome for outcome in update(client, echo=lambda line: None)}
    assert client.pulled == ["llama3.2:latest", "nope:1b"]
    assert outcomes["nope:1b"].error == "model not found"
    assert status(outcomes["llama3.2:latest"]) == "current"


def test_a_refusal_with_nothing_to_say_falls_back_to_its_status() -> None:
    """An empty message would print ``failed --`` and leave the reader guessing."""
    client = FakeClient({"nope:1b": "sha256:bbb"}, fails={"nope:1b": ResponseError("", 500)})
    (outcome,) = update(client, echo=lambda line: None)
    assert outcome.error == "HTTP 500"


def test_the_digests_are_read_once_either_side_rather_than_per_model() -> None:
    """Two listings answer the same question as one listing per model."""
    client = FakeClient({"a:1": "sha256:aaa", "b:1": "sha256:bbb", "c:1": "sha256:ccc"})
    update(client, echo=lambda line: None)
    assert client.listings == 2


def test_a_server_that_is_not_there_reaches_the_caller() -> None:
    """A transport failure is not one model's problem; the pulls after it are pointless."""
    client = FakeClient({"llama3.2:latest": "sha256:aaa"}, fails={"llama3.2:latest": RequestError("gone")})
    with pytest.raises(RequestError):
        update(client, echo=lambda line: None)


def test_the_pulls_are_narrated_as_they_happen() -> None:
    """A pull can run for minutes; a silent script reads as a hung one."""
    client = FakeClient({"llama3.2:latest": "sha256:aaa"})
    lines: list[str] = []
    update(client, echo=lines.append)
    assert lines == ["llama3.2:latest: pulling", "  pulling manifest", "  success"]


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (Outcome("a:1", "sha256:aaa", "sha256:ccc"), "updated (aaa -> ccc)"),
        (Outcome("a:1", "sha256:aaa", "sha256:aaa"), "already current (aaa)"),
        (Outcome("a:1", "", "sha256:ccc"), "installed (ccc)"),
        (Outcome("a:1", "sha256:aaa", "", "model not found"), "failed -- model not found"),
    ],
)
def test_every_outcome_says_which_build_it_ended_on(outcome: Outcome, expected: str) -> None:
    assert describe(outcome) == expected


def test_a_digest_is_shortened_to_the_part_that_identifies_a_build() -> None:
    assert short("sha256:3f2a1b9c1d2e4f5a6b") == "3f2a1b9c1d2e"


def test_a_missing_digest_is_named_rather_than_printed_blank() -> None:
    assert short("") == "unknown"


def test_the_summary_counts_each_kind_and_skips_the_kinds_with_none() -> None:
    outcomes = [
        Outcome("a:1", "sha256:aaa", "sha256:ccc"),
        Outcome("b:1", "sha256:bbb", "sha256:bbb"),
        Outcome("c:1", "sha256:ccc", "sha256:ccc"),
    ]
    assert summary(outcomes) == "3 model(s): 1 updated, 2 already current."


def test_the_report_lines_up_the_models_it_lists() -> None:
    lines, passed = report(
        [Outcome("a:1", "sha256:aaa", "sha256:aaa"), Outcome("longer:1", "", "sha256:ccc")]
    )
    assert lines[0] == "a:1       already current (aaa)"
    assert lines[1] == "longer:1  installed (ccc)"
    assert passed


def test_the_report_fails_when_any_model_did_not_pull() -> None:
    _, passed = report(
        [Outcome("a:1", "sha256:aaa", "sha256:aaa"), Outcome("b:1", "sha256:bbb", "", "nope")]
    )
    assert not passed


def test_a_run_that_updated_everything_prints_the_report_and_succeeds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient({"llama3.2:latest": "sha256:aaa"}, {"llama3.2:latest": "sha256:ccc"})
    assert main([], lambda base_url: client) == 0
    assert "llama3.2:latest  updated (aaa -> ccc)" in capsys.readouterr().out


def test_a_run_with_a_refused_pull_says_so_and_fails() -> None:
    client = FakeClient({"nope:1b": "sha256:bbb"}, fails={"nope:1b": ResponseError("model not found")})
    assert main([], lambda base_url: client) == 1


def test_the_models_named_on_the_command_line_are_the_ones_pulled() -> None:
    client = FakeClient({"qwen2.5:7b": "sha256:bbb"})
    assert main(["llama3.2:latest"], lambda base_url: client) == 0
    assert client.pulled == ["llama3.2:latest"]


def test_the_base_url_is_what_the_client_is_built_with() -> None:
    seen: list[str] = []

    def factory(base_url: str) -> FakeClient:
        seen.append(base_url)
        return FakeClient({"llama3.2:latest": "sha256:aaa"})

    assert main(["--base-url", "http://10.0.0.5:11434"], factory) == 0
    assert seen == ["http://10.0.0.5:11434"]


def test_a_server_that_is_not_answering_is_reported_as_something_to_start(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The same failure the agent translates, and for the same reason."""

    def factory(base_url: str) -> FakeClient:
        raise RequestError("connection refused")

    assert main([], factory) == 1
    assert "ollama serve" in capsys.readouterr().err


def test_an_ollama_with_nothing_pulled_says_what_to_pull(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Nothing failed, so this is not an error -- but a silent run would read as one."""
    assert main([], lambda base_url: FakeClient()) == 0
    assert "ollama pull" in capsys.readouterr().out


def test_the_hint_names_the_server_that_did_not_answer() -> None:
    message = hint("http://10.0.0.5:11434", RequestError("connection refused"))
    assert "http://10.0.0.5:11434" in message and "connection refused" in message
