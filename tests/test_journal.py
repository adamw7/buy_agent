"""The run journal: what it keeps, what it forgets, and what it says moved (ADR-0060)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from buy_agent.journal import (
    Entry,
    Journal,
    Recorded,
    _forget_the_least_recent,
    compare,
    open_journal,
)
from buy_agent.models import Product, price_label


def priced(name: str, price: float | None, currency: str | None = "USD") -> Product:
    return Product(name=name, price=price, currency=currency if price is not None else None)


def journal(tmp_path: Path, request: str = "espresso machine", **asked: object) -> Journal:
    """A journal of one search, kept where this test can see it."""
    return open_journal(request, asked=asked, keeping=True, directory=tmp_path)


# -- what it keeps -------------------------------------------------------------


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
    assert (changes[1].price_label, changes[1].was_label) == ("449.00 USD", None)


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
    again = journal(tmp_path)

    change = again.against([priced("Sage Bambino", *now)])[0]

    label, was = price_label(*now), price_label(*before)
    why = "; nothing is converted" if converted else ""
    assert change.movement == "unplaced"
    assert (change.price_label, change.was_label, change.delta) == (label, was, None)
    assert change.detail == (
        f"{label} now and {was} on {again.compared_with()}{why}, "
        "so there is no movement to report."
    )


@pytest.mark.parametrize(
    ("before", "now"),
    [
        pytest.param(329.0, None, id="gone from this run"),
        pytest.param(None, 329.0, id="absent from the last"),
    ],
)
def test_a_price_missing_on_either_side_is_no_movement_even_in_one_currency(
    before: float | None, now: float | None
) -> None:
    """Grounding blanks a price with its currency, but a file written otherwise can name
    a currency for a price it does not have: one missing figure is still nothing to
    subtract."""
    was = Entry(at=time.time(), products=[Recorded(name="Thing", price=before, currency="USD")])

    change = compare(was, [Recorded(name="Thing", price=now, currency="USD")])[0]

    assert change.movement == "unplaced"
    assert "nothing is converted" not in change.detail


@pytest.mark.parametrize(
    ("before", "now", "movement", "delta", "said"),
    [
        # The cents are kept: rounded to the unit, this read "20.00 USD cheaper".
        pytest.param(349.99, 329.49, "cheaper", -20.5, "20.50 USD cheaper", id="cents"),
        # Under one whole unit is still a rise, not a fall.
        pytest.param(329.49, 329.99, "dearer", 0.5, "0.50 USD dearer", id="under one unit"),
        # Under a cent is no movement at all -- written out, it read "0.00 USD
        # cheaper" -- and its sign does not reach the payload as -0.0.
        pytest.param(349.0, 348.996, "steady", 0.0, "unchanged since", id="under a cent"),
    ],
)
def test_a_movement_is_measured_to_the_cent(
    before: float, now: float, movement: str, delta: float, said: str
) -> None:
    was = Entry(at=time.time(), products=[Recorded(name="Thing", price=before, currency="USD")])

    change = compare(was, [Recorded(name="Thing", price=now, currency="USD")])[0]

    assert (change.movement, change.delta) == (movement, delta)
    assert str(change.delta) == str(delta), "no negative zero"
    assert said in change.detail


# -- what makes two searches two questions -------------------------------------


def test_the_order_the_settings_arrive_in_is_not_part_of_what_was_asked(
    tmp_path: Path,
) -> None:
    journal(tmp_path, region="uk-en", max_price=500.0).against([priced("Sage Bambino", 349.0)])

    assert journal(tmp_path, max_price=500.0, region="uk-en").compared_with() is not None


# -- a journal never fails a run -----------------------------------------------


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

    written.write_text(json.dumps({"key": kept.key}), encoding="utf-8")
    assert journal(tmp_path).compared_with() is None


@pytest.mark.parametrize(
    "run",
    [
        pytest.param({"at": float("nan")}, id="a time that is no number"),
        pytest.param({"at": 1e300}, id="a time past any calendar"),
        pytest.param(
            {"at": time.time(), "products": [{"name": "Sage Bambino", "price": float("nan")}]},
            id="a price that is no number",
        ),
    ],
)
def test_an_entry_that_reads_as_numbers_but_means_none_is_no_history(
    tmp_path: Path, run: dict[str, object]
) -> None:
    """Each passed as a number and failed after the run: dating the comparison raised,
    so every later run of the search ended in a traceback or a 500, and a NaN price
    moved by NaN into a payload no browser can parse. Read as no history instead, the
    next run replaces the file."""
    kept = journal(tmp_path)
    kept.against([priced("Sage Bambino", 349.0)])
    written = next(iter(tmp_path.glob("*.json")))
    written.write_text(json.dumps({"key": kept.key, "runs": [run]}), encoding="utf-8")

    again = journal(tmp_path)

    assert again.compared_with() is None
    assert again.against([priced("Sage Bambino", 329.0)]) == []
    assert journal(tmp_path).compared_with() is not None


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


def test_one_search_being_replaced_does_not_stop_the_rest_being_forgotten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first file asked about, whichever the filesystem lists first, so this holds
    in any order: one stepped over, the other two are still weighed."""
    for name in ("kettle", "laptop", "headphones"):
        open_journal(name, asked={}, keeping=True, directory=tmp_path).against(
            [priced(name, 10.0)]
        )
    told = Path.stat
    stepped_over: list[Path] = []

    def stat(self: Path, *args: object, **kwargs: object):
        if self.suffix == ".json" and not stepped_over:
            stepped_over.append(self)
            _raise_gone()
        return told(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)

    assert _forget_the_least_recent(tmp_path, 1) == 1
    assert stepped_over[0].exists()


# -- the comparison on its own -------------------------------------------------


def _raise_gone() -> float:
    """What a file another run has already replaced answers with."""
    raise OSError("gone already")


def _raising(exc: Exception):
    """A stand-in that fails however it is called."""

    def fail(*_args: object, **_kwargs: object):
        raise exc

    return fail


def test_a_listings_standing_is_not_written_down() -> None:
    """The journal keeps a name, a price and a currency and nothing else (ADR-0060), so
    stock and condition, which change by the hour, are not history (ADR-0079)."""
    product = Product(
        name="Sony WH-1000XM5", price=299.0, currency="USD",
        availability="out of stock", condition="refurbished",
    )

    assert Recorded.of(product).model_dump() == {
        "name": "Sony WH-1000XM5", "price": 299.0, "currency": "USD",
    }
