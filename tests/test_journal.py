"""The run journal: what it keeps, what it forgets, and what it says moved (ADR-0060)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from buy_agent.agent import journal_for
from buy_agent.cache import default_dir
from buy_agent.config import AgentConfig
from buy_agent.journal import (
    MAX_RUNS,
    RUNS,
    Entry,
    Journal,
    Recorded,
    _forget_the_least_recent,
    compare,
    open_journal,
)
from buy_agent.models import Product
from buy_agent.sources import parse_sources


def priced(name: str, price: float | None, currency: str | None = "USD") -> Product:
    return Product(name=name, price=price, currency=currency if price is not None else None)


def journal(tmp_path: Path, request: str = "espresso machine", **asked: object) -> Journal:
    """A journal of one search, kept where this test can see it."""
    return open_journal(request, asked=asked, keeping=True, directory=tmp_path)


# -- what it keeps -------------------------------------------------------------


def test_a_first_run_has_nothing_to_compare_against(tmp_path: Path) -> None:
    kept = journal(tmp_path)

    assert kept.compared_with() is None
    assert kept.against([priced("Sage Bambino", 349.0)]) == []


def test_the_next_run_of_the_same_search_is_compared_with_the_last(tmp_path: Path) -> None:
    journal(tmp_path).against([priced("Sage Bambino", 349.0)])

    changes = journal(tmp_path).against([priced("Sage Bambino", 329.0)])

    assert [(change.movement, change.delta) for change in changes] == [("cheaper", -20.0)]
    assert "20.00 USD cheaper" in changes[0].detail


def test_a_journal_that_is_off_remembers_nothing_and_compares_nothing(
    tmp_path: Path,
) -> None:
    """One setting, and a run that keeps no history is not a failure of one."""
    open_journal("espresso", asked={}, keeping=False, directory=tmp_path).against(
        [priced("Sage Bambino", 349.0)]
    )
    again = open_journal("espresso", asked={}, keeping=False, directory=tmp_path)

    assert not again.keeping
    assert again.against([priced("Sage Bambino", 329.0)]) == []
    assert list(tmp_path.glob("*.json")) == []


def test_a_run_that_found_nothing_is_not_written_down(tmp_path: Path) -> None:
    """It says nothing about a price, and recorded it would make every product of the
    next run read as new."""
    journal(tmp_path).against([priced("Sage Bambino", 349.0)])
    journal(tmp_path).against([])

    changes = journal(tmp_path).against([priced("Sage Bambino", 349.0)])

    assert [change.movement for change in changes] == ["steady"]


def test_only_a_name_a_price_and_a_currency_are_written_down(tmp_path: Path) -> None:
    """A shopping history on disk is a different object from a page cache, so what is
    kept is what a comparison needs and nothing else."""
    journal(tmp_path).against(
        [
            Product(
                name="Sage Bambino",
                price=349.0,
                currency="USD",
                url="https://shop.example/bambino",
                notes="Fast to heat.",
            )
        ]
    )

    stored = json.loads(next(iter(tmp_path.glob("*.json"))).read_text(encoding="utf-8"))

    assert stored["runs"][0]["products"] == [
        {"name": "Sage Bambino", "price": 349.0, "currency": "USD"}
    ]


def test_a_search_keeps_at_most_its_last_few_runs(tmp_path: Path) -> None:
    """Retention is a count and not an age: a record that expired is no use for the one
    question it exists to answer."""
    for run in range(MAX_RUNS + 3):
        journal(tmp_path).against([priced("Sage Bambino", 300.0 + run)])

    stored = json.loads(next(iter(tmp_path.glob("*.json"))).read_text(encoding="utf-8"))

    assert len(stored["runs"]) == MAX_RUNS
    assert stored["runs"][-1]["products"][0]["price"] == 300.0 + MAX_RUNS + 2


def test_the_least_recently_run_search_is_the_one_forgotten(tmp_path: Path) -> None:
    """Pruning oldest-*entry*-first would delete exactly the entry a comparison wants,
    so what goes is a whole search nobody has run lately."""
    for name in ("kettle", "laptop", "headphones"):
        open_journal(
            name, asked={}, keeping=True, directory=tmp_path,
        ).against([priced(name, 10.0)])
    # The one run longest ago, whatever order the filesystem lists them in.
    oldest = min(tmp_path.glob("*.json"), key=lambda path: path.stat().st_mtime)

    Journal(tmp_path, "kettle", searches=2)._append(Entry(at=time.time(), products=[]))

    assert not oldest.exists()
    assert len(list(tmp_path.glob("*.json"))) == 2


# -- what it says moved --------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "now", "movement", "said"),
    [
        (349.0, 329.0, "cheaper", "20.00 USD cheaper"),
        (329.0, 349.0, "dearer", "20.00 USD dearer"),
        (329.0, 329.0, "steady", "unchanged since"),
    ],
)
def test_a_price_that_moved_is_reported_with_how_far(
    tmp_path: Path, before: float, now: float, movement: str, said: str
) -> None:
    journal(tmp_path).against([priced("Sage Bambino", before)])

    change = journal(tmp_path).against([priced("Sage Bambino", now)])[0]

    assert change.movement == movement
    assert said in change.detail
    assert (change.price_label, change.was_label) == (
        f"{now:,.2f} USD",
        f"{before:,.2f} USD",
    )


def test_a_product_the_last_run_did_not_have_is_new(tmp_path: Path) -> None:
    journal(tmp_path).against([priced("Sage Bambino", 349.0)])

    changes = journal(tmp_path).against(
        [priced("Sage Bambino", 349.0), priced("Gaggia Classic", 449.0)]
    )

    assert [change.movement for change in changes] == ["steady", "new"]
    assert "not in the run of" in changes[1].detail


def test_a_product_that_has_left_the_report_is_listed_last(tmp_path: Path) -> None:
    """The half a shopper notices, since a product that has gone leaves nothing behind
    on the page to read."""
    journal(tmp_path).against([priced("Sage Bambino", 349.0), priced("Gaggia Classic", 449.0)])

    changes = journal(tmp_path).against([priced("Sage Bambino", 349.0)])

    assert [(change.name, change.movement) for change in changes] == [
        ("Sage Bambino", "steady"),
        ("Gaggia Classic", "gone"),
    ]
    assert changes[1].price_label is None
    assert changes[1].was_label == "449.00 USD"


@pytest.mark.parametrize(
    ("before", "now", "converted"),
    [
        # Two prices in two currencies have nothing between them (ADR-0043).
        ((329.0, "USD"), (329.0, "EUR"), True),
        # And a figure no page printed is not a movement either -- for a reason of
        # its own, which "price unknown" already says: there was nothing to convert.
        ((329.0, "USD"), (None, None), False),
        ((None, None), (329.0, "USD"), False),
        ((None, None), (None, None), False),
    ],
)
def test_two_prices_that_cannot_be_held_against_each_other_report_no_movement(
    tmp_path: Path,
    before: tuple[float | None, str | None],
    now: tuple[float | None, str | None],
    converted: bool,
) -> None:
    journal(tmp_path).against([priced("Sage Bambino", *before)])

    change = journal(tmp_path).against([priced("Sage Bambino", *now)])[0]

    assert change.movement == "unplaced"
    assert change.detail.endswith("so there is no movement to report.")
    assert ("nothing is converted" in change.detail) is converted


def test_two_runs_match_a_product_by_the_identity_a_run_already_uses(
    tmp_path: Path,
) -> None:
    """``Product.dedup_key``'s own reading, so there are never two spellings of "the
    same product"."""
    journal(tmp_path).against([priced("Sage Bambino!", 349.0)])

    change = journal(tmp_path).against([priced("sage  bambino", 329.0)])[0]

    assert change.movement == "cheaper"


def test_a_recorded_product_with_no_price_reads_as_one(tmp_path: Path) -> None:
    assert Recorded(name="Thing").label() == "price unknown"


# -- what makes two searches two questions -------------------------------------


@pytest.mark.parametrize(
    "asked",
    [
        {"region": "uk-en"},
        {"max_price": 500.0},
        {"sources": [source.spec for source in parse_sources("rtings.com")]},
    ],
)
def test_a_search_asked_differently_has_a_history_of_its_own(
    tmp_path: Path, asked: dict[str, object]
) -> None:
    """A comparison across two different budgets is a comparison of two questions."""
    journal(tmp_path, region="us-en", max_price=None, sources=[]).against(
        [priced("Sage Bambino", 349.0)]
    )

    kept = journal(tmp_path, **{"region": "us-en", "max_price": None, "sources": [], **asked})

    assert kept.compared_with() is None


def test_the_same_request_typed_differently_is_the_same_search(tmp_path: Path) -> None:
    journal(tmp_path, request="Espresso Machine").against([priced("Sage Bambino", 349.0)])

    assert journal(tmp_path, request=" espresso machine ").compared_with() is not None


def test_the_model_is_not_part_of_what_was_asked() -> None:
    """Which model read the pages decides how well the question was answered rather than
    what it was; keying on it would leave every change of model a search with no history
    at all."""
    first = journal_for("espresso machine", AgentConfig(model="gemma4:12b"))
    second = journal_for("espresso machine", AgentConfig(model="lfm2.5"))

    assert first.key == second.key


def test_the_budget_is_part_of_what_was_asked() -> None:
    assert journal_for("espresso machine", AgentConfig()).key != journal_for(
        "espresso machine", AgentConfig(max_price=500.0)
    ).key


def test_a_run_writes_its_journal_under_the_cache_directory(monkeypatch, tmp_path) -> None:
    """One root, so deleting it throws the page cache and the history away together."""
    monkeypatch.setenv("BUY_AGENT_CACHE_DIR", str(tmp_path))
    kept = journal_for("espresso machine", AgentConfig())

    kept.against([priced("Sage Bambino", 349.0)])

    assert kept.directory == default_dir(RUNS) == tmp_path / RUNS
    assert list((tmp_path / RUNS).glob("*.json"))


# -- a journal never fails a run -----------------------------------------------


def test_an_unwritable_directory_costs_the_history_and_not_the_run(tmp_path: Path) -> None:
    """A shopping history is worth less than the run it would have interrupted."""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("", encoding="utf-8")

    kept = open_journal("espresso", asked={}, keeping=True, directory=blocked / "runs")

    assert kept.against([priced("Sage Bambino", 349.0)]) == []


def test_a_file_that_is_not_an_entry_is_read_as_no_history(tmp_path: Path) -> None:
    kept = journal(tmp_path)
    kept.against([priced("Sage Bambino", 349.0)])
    written = next(iter(tmp_path.glob("*.json")))

    written.write_text("not json", encoding="utf-8")
    assert journal(tmp_path).compared_with() is None

    written.write_text(json.dumps(["not an object"]), encoding="utf-8")
    assert journal(tmp_path).compared_with() is None

    written.write_text(json.dumps({"key": "another search", "runs": []}), encoding="utf-8")
    assert journal(tmp_path).compared_with() is None


def test_an_entry_that_is_not_a_run_is_read_as_no_history(tmp_path: Path) -> None:
    """It should not happen -- nothing else writes these -- and it costs a comparison
    rather than a run."""
    kept = journal(tmp_path)
    kept.against([priced("Sage Bambino", 349.0)])
    written = next(iter(tmp_path.glob("*.json")))
    written.write_text(
        json.dumps({"key": kept.key, "runs": [{"at": "yesterday"}]}), encoding="utf-8"
    )

    assert journal(tmp_path).compared_with() is None


def test_a_half_written_journal_is_not_left_to_be_read_as_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Patched where the move happens, which is ``cache.write_atomically``: a journal is
    # written the way a cache entry is, and what this asserts is that it is *this*
    # module that never fails a run over one.
    monkeypatch.setattr(
        "buy_agent.cache.os.replace", _raising(OSError("no space left on device"))
    )

    journal(tmp_path).against([priced("Sage Bambino", 349.0)])  # no raise

    assert list(tmp_path.iterdir()) == []


def test_a_temporary_file_that_cannot_be_removed_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both halves of the failure path fail, and the run still has its answer."""
    monkeypatch.setattr("buy_agent.cache.os.replace", _raising(OSError("nope")))
    monkeypatch.setattr(Path, "unlink", _raising(OSError("nor that")))

    journal(tmp_path).against([priced("Sage Bambino", 349.0)])  # no raise

    assert journal(tmp_path).compared_with() is None


def test_a_search_another_run_is_replacing_right_now_is_stepped_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs tidying one directory at once, which is the only way either of these
    happens -- and neither is worth failing a run over."""
    for name in ("kettle", "laptop", "headphones"):
        open_journal(name, asked={}, keeping=True, directory=tmp_path).against(
            [priced(name, 10.0)]
        )
    # Only the entries, so the directory itself is still listable -- which is the
    # state this steps over: the file was there when it was listed and gone when it
    # was asked about.
    told = Path.stat
    monkeypatch.setattr(
        Path,
        "stat",
        lambda self, *a, **k: _raise_gone() if self.suffix == ".json" else told(self, *a, **k),
    )

    assert _forget_the_least_recent(tmp_path, 1) == 0

    monkeypatch.undo()
    monkeypatch.setattr(Path, "unlink", _raising(OSError("gone already")))

    assert _forget_the_least_recent(tmp_path, 1) == 0
    assert len(list(tmp_path.glob("*.json"))) == 3


def test_a_directory_that_cannot_be_listed_is_an_empty_history(tmp_path: Path) -> None:
    kept = open_journal("espresso", asked={}, keeping=True, directory=tmp_path / "absent")

    assert kept.compared_with() is None


# -- the comparison on its own -------------------------------------------------


def test_compare_reads_the_day_off_the_run_it_is_comparing_with() -> None:
    """The date is inside every sentence, because the page shows the sentences and
    composes none of its own (ADR-0012)."""
    before = Entry(at=1_757_548_800.0, products=[Recorded(name="Thing", price=10.0)])

    changes = compare(before, [Recorded(name="Thing", price=10.0)])

    assert before.when() == "11 Sep"
    assert changes[0].detail.endswith("unchanged since 11 Sep.")


def _raise_gone() -> float:
    """What a file another run has already replaced answers with."""
    raise OSError("gone already")


def _raising(exc: Exception):
    """A stand-in that fails however it is called."""

    def fail(*_args: object, **_kwargs: object):
        raise exc

    return fail
