# ADR-0057: Make a search backend a row in a table, beside a provider and a rail

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

DuckDuckGo was the only way this agent could search, and its rate-limiting is a
named limitation in the README: heavy use is asked a second time (ADR-0053) and
then reported as a `SearchError` -- one of the three failures the whole project
is built around (ADR-0009). There was nothing a shopper could do about it but
wait, on a run that had already spent a minute of somebody's afternoon.

The project already answers this shape of question twice. `providers.PROVIDERS`
carries everything that differs between one model server and another (ADR-0029);
`rails.RAILS` carries everything that differs between one counterparty and
another (ADR-0046). Both hold the same rule: the name becomes behaviour in
exactly one place, and there is no `if provider == ...` above the table.

`ddgs` itself fronts several engines and raises only when every one of them
failed, which is worth knowing and is not the same lever: it chooses between
engines behind one library, one set of rate limits and one address. What a
shopper who is being rate-limited needs is a different *library* or a different
*server* -- their own, or one they hold a key for.

## Decision

**A search backend is one row in `search.BACKENDS`, and a row nowhere else.**
Each row carries where it listens (from its own environment variable), its key
(from its own), whether it needs one, how it is asked, how its answer becomes
`SearchResult`s, the transport errors meaning "not there", and the sentence one
of those carries. `AgentConfig.search_backend` is the only place a name becomes
behaviour.

Three rows: `ddg`, what exists today and the default; `searxng`, self-hosted,
no key and no account, which is ADR-0003's argument about the model applied to
the search; and `brave`, a key read off `$BRAVE_API_KEY` with no flag and no form
field, exactly as `$VLLM_API_KEY` is handled.

**The table lives in `search.py`, not beside it.** The alternative was a fourth
module with `search.py` left wrapping it, and it does not survive the import
graph: `config.py` has to read the rows, so the table must sit below the settings
-- and `SearchResult` and `SearchError` are what the rows answer with and what
five other modules already import from `search`. One module keeps ADR-0021
intact, keeps `buy_agent.search.DDGS` the one name the suite patches for the
library, and keeps the module knowing about no other module of this package.

**A row is handed itself, not a config.** `Provider.chat_model` and
`Rail.checkout` are handed the `AgentConfig` they were resolved from, because
what they need is spread across it. A search takes a query and answers results;
what a row needs is its own address and its own key, which are on the row. So
`find` and `hint` take the `Backend` and nothing that knows what a config is --
which is also what lets `search.py` stay the one table with no `config` import
of any kind, not even a deferred one.

**A missing key is the row's failure, not the config's.** `AgentConfig` refuses a
name nothing can search, the way it refuses a provider and a rail. It does not
refuse a backend whose key is unset: that is a property of the machine the server
is running on, not of the request, and the browser has no box to put one in. The
row raises a `SearchError` naming the variable instead, and `Backend.configured`
is what the form's picker marks the row with -- Python's answer, read out by the
page rather than worked out there (ADR-0012).

**The retry stays above the rows.** ADR-0053 decided what is worth asking twice,
and that judgement is about a search and not about a backend. What each backend
calls "nothing matched" *is* its own -- DuckDuckGo raises where the others answer
an empty list -- so that reading is on the row.

## Consequences

`search.py` is the fourth module in the package that speaks HTTP, beside
`fetch.py`, `providers.py` and `rails.py`, and the fourth the suite patches. That
rule was written as "three", and the count was the whole of its content; it is
four now, and a fifth is still a request from a module nobody thought made any.

`search.py` moves out of the pipeline layer and in beside the other seams, for a
reason a test already stated: a step is given its settings and does not go and
look them up, and a table reads its rows' addresses off the environment. The
layer that held `chat.py`, `providers.py` and `cache.py` was never "model access"
anyway -- the disk is not a model server either -- so it is named for what it
holds.

The obligations.

- **No `if backend == ...` above `search.py`.** A fourth backend is a row there
  and a row nowhere else; the flag's `choices`, the API's check and the form's
  picker are all read off the table.
- **A key is read on the row and nowhere else.** `backend_options()` goes to a
  browser, so it carries `configured` and never the key itself.
- **`integration/` is DuckDuckGo's alone, and stays fake.** The live suite fakes
  the web on purpose (ADR-0026) so a nightly failure says something about this
  code. A self-hosted SearXNG and a keyed Brave are both gaps named here rather
  than jobs to fake, exactly as ADR-0028 names vLLM's.
- **A hint names the setting and never the flag.** These sentences are read on a
  terminal and under a labelled box; "the search backend" is what both doors call
  it.
