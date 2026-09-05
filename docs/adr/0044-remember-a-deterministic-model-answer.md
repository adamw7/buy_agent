# ADR-0044: Remember a deterministic model answer on disk, beside the pages

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

ADR-0040 put the fetched pages on disk and said so in its own consequences: "a
repeated search costs the extraction and nothing else". That was the whole of the
remaining cost, and on a small local model it is most of the minute. The pages
come back in a second or two; the extraction prompt runs to ~4.3k tokens and the
model reads ten pages of it.

Which matters because of *what people do with this project*. The loop for
anything being worked on here -- a bound, a weight, the sort order, the
constraints, a payload's shape -- is: run it, look, change something after the
extraction, run it again. Every one of those iterations asks the model the
identical question and gets the identical answer, because the pages came off
disk and are byte for byte the pages it was shown last time. The three stages the
shopper actually tunes (`Constraints`, `rank_products`, `sort_by`) all run
*after* extraction, so changing any of them re-pays for an answer that cannot
have changed. Re-sorting a finished run was worth its own record for exactly this
reason (ADR-0035); this is the same observation one stage earlier.

There is one thing a cache like this must not do, and it is sharper here than it
was for the pages. A stale page is a stale price -- the sources did print it,
yesterday -- and the shopper can reason about that. A replayed *answer* would be
the cache deciding what the model said, and if the model was not being asked for
a repeatable answer then the replay invents a determinism the run never had.

## Decision

`buy_agent.cache` keeps what a model server answered, beside the pages and under
the same time to live. `AgentConfig.cache_ttl` governs both -- one setting, so a
run cannot be half live -- and `--cache-ttl 0` reads every page off the web and
asks the model every question, as it always did.

**The store is generalised, not duplicated.** `DiskCache` is the page cache's own
mechanics -- one JSON file per key, named by the SHA-256 of the key, the key
stored inside the entry and checked on the way out, written to a temporary file
and moved into place, every operation best-effort and nothing raising -- over an
arbitrary key and value. The two kinds live in separate directories under
`$BUY_AGENT_CACHE_DIR` (`pages/` and `answers/`), because they are pruned and
counted separately and "how much of this run came off disk" is two questions.
Storing the key rather than trusting the hash matters more here than it did for a
URL: an answer's key is a whole rendered request, and long is exactly where "the
name is a hash of it" stops being an argument on its own.

**`RememberedAnswers` is a `ChatModel` wrapping a `ChatModel`**, so everything
above it asks its one question and cannot tell -- the pipeline sees a model that
is sometimes very fast. That is the only way this is allowed to work, and it is
why the key has to hold everything that decides an answer: the rendered messages,
the schema (as the JSON schema itself, so a field added to `ExtractedProduct`
changes the decoding grammar *and* the key), and the run's fingerprint -- the
provider, the model, the address, the thinking switch, and `num_ctx` only where
the provider actually sends it. A reworded prompt, a widened page budget, a
different model or a different server all miss.

**Only a deterministic run is remembered.** `temperature == 0` -- the shipped
default, because extraction is copying rather than creation -- is what makes
"the answer" a thing that exists. A run that samples has no answer to remember,
and replaying one sample would be this cache changing a run's result. So a
sampled run gets the plain model back, not a wrapper that quietly never hits, and
`temperature` is consequently *not* in the key: it is a constant there, and a
constant in a key is noise.

**`api_key` is not in the key either.** The key is written to a file. Nothing
that would be a mistake to leave on disk goes into one.

**Only an answer is stored.** A transport failure and an unreadable answer are
states of the world rather than facts about this question, and storing either
would answer the next run with a stopped server.

**The wrapping happens in `agent.py`, not in `providers.py`.** It has nothing to
do with which server is answering: the same wrapper goes round either, and
neither row has to declare it (ADR-0029). And it goes round the *provider's*
model only -- `BuyAgent(config, llm=...)` hands back exactly what it was given,
because a stand-in answers what a test told it to and a remembered answer over
the top would be this module deciding what the test meant.

## Consequences

The iteration loop this project is actually developed in goes from about a minute
to about a second, and the demo, the scripted runs and anybody comparing two
sortings of one search stop paying for a model call they have already made.

Three places had to be told not to remember, and each says why in its own words.
`benchmark.corpus.settings` sets `cache_ttl=0`: the answers are the thing being
measured, and a scored run that replayed yesterday's would report yesterday's
model -- which also keeps `integration/`, whose whole point is a real model,
asking one. `tests/conftest.py` points `$BUY_AGENT_CACHE_DIR` at a scratch
directory per test, so no unit test reads the developer's disk and no test
answers another test's question.

Four obligations, three of them inherited from ADR-0040 and one new.

- **The cache must never change an answer.** For the pages that meant storing the
  text and not the excerpt. Here it means the key holds *everything*: a setting
  that reaches the model and not the key is a run silently answered from another
  run's question. The determinism gate is the same rule in the other direction.
- **Nothing in `cache.py` may raise.** Unchanged and now load-bearing twice over:
  a cache that can fail is a fourth failure mode ADR-0009 does not have.
- **A failure is not an entry.** As a 403 is not a page.
- **`$BUY_AGENT_CACHE_DIR` is now a root holding two directories**, where it used
  to be the page directory itself. An existing cache is cold once. It still has
  no flag and no form field, for the reason `$VLLM_API_KEY` has none: a path on
  the server's disk is not a browser's to choose.

The cost is a second kind of staleness. A page entry going stale is a day-old
price; an answer entry going stale is a day-old *reading* of that price, which is
the same figure one step further from the source. The two expire together and on
the same clock, and because the pages are in the key an answer can only ever be
reused for pages that are themselves still fresh -- so the answer cache cannot
outlive the page cache, which is the property that keeps the second staleness
from being a new one.
