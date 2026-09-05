# ADR-0040: Cache the page text on disk, and cache the text rather than the excerpt

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

A run takes about a minute on `lfm2.5`, and it spends that minute twice over on
work it has already done. `fetch.enrich` opens ten pages every time; the ten
pages a search returns are the same ten pages an hour later, and their prices are
the same prices. Only the extraction genuinely has to happen again, and only
because the prompt may have changed.

Three things made this worth fixing rather than tolerating.

The obvious one is the wait. Anyone changing a prompt, a weight, a bound or the
condensing rules pays the full fetch to see the effect, so the loop for the parts
of this project that are actually being worked on is gated by the part that is
not.

The second is that the fetch is the *flaky* stage. The README's own limitations
list says so: shops answer 403, some answer JavaScript, some rate-limit. Grounding
blanks every figure the pages did not back, so a run whose fetches went badly
reports "price unknown" throughout -- and two consecutive runs of the same search
can differ for no reason but which shops felt like answering. That makes the
benchmark's own numbers noisier than the thing they measure.

The third is politeness. Ten requests per run at the same shops, repeated through
an afternoon of iteration, is the behaviour that gets an IP rate-limited, and the
rate limit then looks like a bug in this code.

Nothing in the pipeline needed to change to fix it: `fetch_page` was one function
doing two separable things, fetching a page and condensing it.

## Decision

`buy_agent.cache` keeps the text of fetched pages on disk, one JSON file per URL,
expiring by age. `AgentConfig.cache_ttl` is how long an entry stays usable --
86,400 seconds, a day -- and `0` is how "read every page off the web" is spelled,
on the command line (`--cache-ttl 0`) and in the form alike. One setting rather
than a number and a switch, which can disagree about whether the cache is on.

`fetch_page` is split. `read_page` fetches and answers the page's **visible
text**; `fetch_page` gets that text from the cache or from `read_page`, condenses
it, and stores what it read. `enrich` opens the cache for the run and prunes it,
for the reason it owns the HTTP client: it is the function that knows when the
fetching starts and when it is over.

**What is stored is the page text, not the condensed excerpt.** `page_chars` and
`opinion_chars` decide which lines survive into the prompt and are per-run
settings, so an excerpt stored under one budget is the wrong excerpt under the
next -- and the budgets are exactly what somebody iterating on this code is
changing. Condensing is cheap and runs every time; fetching is the part that is
skipped.

**What is stored is stored whole, exactly as a live fetch produced it**, so a run
that read a page off disk extracts from the text a fresh run would have. That is
what keeps the cache invisible to everything downstream: extraction and
verification are still given the same text (ADR-0005), and grounding cannot tell
a cached page from a fetched one, which is the only way a cache is allowed to
work here. Nothing in the cache trims -- the ceiling is `fetch._MAX_PAGE_BYTES`,
already applied by the fetch.

**Only a page that was actually read is stored.** A 403, a timeout, markup that
would not parse: none of them go in. A shop that has stopped refusing is noticed
on the next run rather than at the end of a day.

An entry is named by the SHA-256 of its URL, so a URL of any length and any
character becomes a filename; the URL is stored *inside* the entry and checked on
the way out, because a hash is not a promise and one page's text standing in for
another's is the one thing a cache must never do. Writes go to a temporary file
and are moved into place with `os.replace`, so a reader never sees half an entry.

**Every operation is best-effort.** An unwritable directory, a half-written file,
a disk that filled up, JSON that will not parse, an entry naming another URL: all
of them read as a miss. Nothing in this module raises, because a cache that
cannot be used is a slower run and never a failed one.

Where the entries live is `$BUY_AGENT_CACHE_DIR`, or the platform's own cache
directory under `buy-agent/pages`. It is the one setting with no flag and no form
field, for the reason `$VLLM_API_KEY` is one: a path on the server's disk is not
a browser's to choose.

The run says how many pages came off disk, on the line that already says how the
fetching went:

```
Got usable page text from 10 of 10 result(s), 8 from cache
```

## Consequences

A repeated search costs the extraction and nothing else, which is the difference
between a minute and a few seconds for anybody iterating on a prompt, a bound or
the ranking. Two runs an afternoon apart compare the same pages rather than
whichever pages answered, which makes the benchmark's numbers about the model
again.

The cost is a stale price. A day-old entry is a day-old figure, reported as
current; that is what a time to live is, and `--cache-ttl 0` is the answer for a
run that must be live. It is worth being explicit that this is the one place
where the project's own "never report a figure the sources did not print" is
weakened: the sources did print it, yesterday.

Three obligations.

- **The cache must never change an answer.** Storing the condensed excerpt, or a
  truncated page, or a page that failed to load would each break that, and none
  of them would fail a test that was not written for it -- `tests/test_fetch.py`
  holds the two that matter: the same text through the same `condense`, and a
  widened budget widening the excerpt from a cached page exactly as from a live
  one.
- **Nothing in `cache.py` may raise.** Every filesystem call is guarded, and
  `prune` is called from `open_cache` without a guard *because* it cannot raise.
  A new operation that can throw makes the cache able to fail a run, which is a
  fourth failure mode ADR-0009 does not have.
- **A page that could not be read is not an entry.** Caching failures would make
  a transient 403 last a day, and the run's failure tally -- which is how a
  shopper tells a bad model from nothing having been read -- would be reporting
  yesterday's web.

The container gets no volume for this by default, so an image restarted starts
cold; that is correct for a container and the directory is one environment
variable away from a mounted one.
