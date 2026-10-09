# ADR-0080: Tell the shopper whether a price alert was met

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

The journal already answers "what moved since the last run of this search"
(ADR-0060), which makes running one search on a schedule worthwhile. What a scheduled
run could not do was say whether the price somebody is waiting for has arrived.
`--max-price` with exit code 3 came close and was wrong: a bound *keeps* a product it
cannot judge (ADR-0039), so an unpriced product passed it, the run "found something"
and exited 0, and a script watching for a price drop was told one had happened.

## Decision

**`alert_below` is a setting that is told and never applied.** It is one row of
`api.OPTIONS` (`--alert-below`, the form's Price alert box) with `max_price`'s range.
`alerts.price_alert` names the products priced at or under it in the run's own
currency (the one `api._counted_in` reads, never a second vote, ADR-0056), cheapest
first, leaving out any whose page says it is out of stock (ADR-0079). Unlike a bound,
an unknown or unplaceable price never meets it. Its sentence names the cheapest
product when nothing does, and any under the line that are out of stock.

- The CLI writes `PRICE ALERT MET` or `NOT MET` and the sentence to the report on
  stdout, and exits `ABOVE_ALERT` (5) when it was given and nothing met it. Finding
  nothing is still 3, and a run with `--pay` still answers with what paying came to.
- `POST /api/search` carries `alert` (`null` unset); a re-sort answers `null` and the
  page keeps the run's, as it keeps `dropped` and `changes`.
- The alert removes nothing and reorders nothing, is not part of the journal's key
  (it changes what is said about a search, not the search), and, like a bound, is not
  remembered by the form (ADR-0077).

## Consequences

`python -m buy_agent "espresso machine" --alert-below 400 && notify` is a price watch
with no new store: the journal records each run, and the exit code says whether to
look.

The obligations.

- **An alert is never applied.** Nothing in `BuyAgent.run` reads `alert_below`; it is
  asked of a finished ranking by `api.alert_for`, which both doors call.
- **Only a price this run can place meets it.** It reads `comparable_price`, as
  ranking and the bounds do, and is the opposite of a bound on unknowns, as
  `payment._check` is.
- **The exit code is part of the CLI's interface.** `--help` lists it, and
  `tests/test_cli.py` holds it apart from every other code.
