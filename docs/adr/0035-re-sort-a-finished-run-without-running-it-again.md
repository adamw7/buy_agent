# ADR-0035: Re-sort a finished run without running it again

- **Status:** Accepted
- **Date:** 2026-09-01

## Context

"Rank by" was a search option and nothing else. `sort_by` arrived with the rest
of the request, `BuyAgent.run` passed it to `rank_products` at the end, and the
answer came back in that order -- which is right, and is the rule this project
keeps deliberately: the browser decides nothing, so ordering is not a client-side
re-sort over an array the page happens to hold.

What that cost was the whole pipeline. A shopper looking at ten ranked products
and wanting them cheapest-first paid another minute: a second web search, ten
more pages fetched, another extraction, another two model calls -- to reorder
products already on the screen, whose every figure had already been grounded.
Worse, the second run is a *different* run: the search may return other pages and
the model may read them differently, so what came back was not the same ten
products in another order.

The other half of the same gap: nothing could be kept. The CLI has `--json`. The
page had the log, and only after a failure -- deliberately, since a successful run
is on the screen in front of you. But the screen is thrown away by the next
question, and the run took a minute.

The rule was never that the *pipeline* must re-run. `rank_products` needs no
model, no network and no page: it is arithmetic over products that already exist.

## Decision

`POST /api/rank` takes a finished run's products back and answers the shape a
finished run answers with -- `{"request", "count", "top_n", "sort_by",
"products"}` -- having called `rank_products` and nothing else. `api.rank_again`
is the whole of it; no agent is built, so there is no `agent_factory` to hand one
to.

The products travel in the request rather than being kept server-side under a run
id. A session store is state with a lifetime, an eviction policy and a leak, on a
server whose whole point is that it is stdlib and serves one person; the browser
already holds the products, and the body limit already caps how many can arrive.
It is a POST for the same reason `sources` is the one option `_read` does not
touch: a query string cannot carry a list.

Sending the products back is not the browser deciding. What it sends is what it
was sent, and every one of them is scored again from the set -- a score is a fact
about the candidate set, and this is that set -- so a figure edited on the way out
changes nothing about the order. `Product` validates what arrives, the labels
`product_payload` adds are ignored on the way in and written again on the way
out, and `sort_by` and `top` are read by the same `_as_sort_by` and the same
`config.LIMITS` range the search endpoint reads them by.

For keeping a run: a **Download results** button beside the results, writing what
the server sent. There is one shaping of a run's products -- `api.results_payload`
-- and `--json` writes it too, so the file a script parses and the file a shopper
downloads are the same document. The browser stringifies the answer; it composes
nothing.

## Consequences

Changing the criterion after a run costs one request over data already on the
page, and the products do not change while it happens -- which the old re-run
could not promise. A finished run can be kept, in the shape the CLI already
writes.

The obligations:

- `rank_again` and `run_search` answer one shape. `agent.types.ts` types both as
  `SearchResult` and `tests/test_conventions.py` holds them to it; a field added
  to one is a field going undefined in the view showing the other.
- A field added to `product_payload` is in `--json`, in the API's answer and in
  the downloaded file at once, because all three are `results_payload`. Shaping a
  run anywhere else is how the three come apart.
- A criterion added to `ranking.SortBy` is offered in a fourth place now: the
  results control reads `sort_options` off the defaults the server ships, the way
  the form's own Rank by field does, so nothing lists them again.
- `do_POST` routes two endpoints. A third belongs in the same table, behind the
  same `_admits()` -- a method added without it is unguarded and nothing fails
  (ADR-0018).
- A re-sort that fails is not a run that failed. It is said beside the results,
  which are still there in the order they were in, rather than in the banner --
  which would also start the log panel offering a bug report about a search that
  worked (ADR-0034's Download log is for a failed *run*).
- This does not make the page a place where ordering is decided. `rank_products`
  is still the only thing that orders products, and it still runs in Python.
