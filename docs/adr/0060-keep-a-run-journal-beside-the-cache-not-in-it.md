# ADR-0060: Keep a run journal beside the cache, not in it

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

Running the same search again is the normal thing a shopper does, and the reason they do
it is that a price may have moved. The agent had no memory of ever having run it. Both
of the things it does keep on disk are there to make the *next* run cheaper -- the page
text (ADR-0040) and the model's answers about it (ADR-0044) -- and the one thing a person
would actually want back, what the answer was last week, was the one thing not kept.

The tempting move is a third kind of cache entry beside `pages/` and `answers/`.
`cache.py`'s docstring says what it holds -- what a run can reuse, and nothing else -- and
the three rules in it are all wrong for a record kept to be read:

- **Retention.** Both existing kinds expire on one TTL because a stale page is not
  evidence. A journal that expires at `cache_ttl` is useless for the one question it
  exists to answer, and the default TTL is a day.
- **Size.** ADR-0052 prunes oldest-first on the way in. Pruning a journal oldest-first
  deletes exactly the entry a comparison wants.
- **Privacy.** A shopping history is a different object from a page cache, and "delete
  the directory, it costs one slow run" stops being the whole answer once the directory
  holds what somebody was shopping for and what it cost.

## Decision

**A journal is its own module, its own directory and its own rules.**
`buy_agent/journal.py` writes `runs/` beside `pages/` and `answers/` under
`$BUY_AGENT_CACHE_DIR` -- one root, so one delete throws everything away -- and shares
nothing with `cache.py` but the question of where disposable things live.

**It never expires, and it is bounded by a count.** At most `MAX_RUNS` runs of one
search, oldest of that search out first, and at most `MAX_SEARCHES` searches, the least
recently *run* one out first. Which is ADR-0052 turned around on purpose: the file's
mtime is when that search last ran, so the one being dropped is a search nobody is
asking about, never the newest entry of one they are.

**It holds three figures per product.** A name, a price and a currency: what a
comparison needs and nothing else. No links, no quotes, no notes, no scores.

**It is keyed by the request and by what shaped the question.** The region, the
currency, the backend, the sources, the three bounds and how many products were wanted.
A comparison across two different budgets is a comparison of two different questions.
Deliberately *not* by the model, the provider or the context window: those decide how
well the question was answered rather than what was asked, and keying on them would make
every change of model a search with no history at all. That is the opposite reading from
the cache's fingerprint (`agent._asks_the_same_question`), and for a reason -- that one
hands an *answer* back, and this one is read by a person.

**A run that found nothing is not written down.** It says nothing about a price, and
recorded it would make every product of the next run read as new. It is still compared,
against the last run that did find something.

**It is one setting off.** `AgentConfig.journal`, `--journal/--no-journal`, and a
"Remember this run" box in the form. On by default, because a feature nobody discovers
answers nobody's question; `--journal`'s help says where it is kept and that deleting
that directory throws it away.

**The comparison is Python's sentences.** `journal.Change` carries what moved as a word
to group by and the whole of it as a finished sentence, the way `models.Removal` does
(ADR-0055). The CLI prints the block under `--compare`, marked as report so a
`> top.txt` keeps it; the run payload carries `changes` and `compared_with`, and the
page draws a panel under the results. A re-sort answers empty lists and the page carries
the run's own across, exactly as it does for `dropped` (ADR-0035).

## Consequences

`journal.py` is the first module in the seams layer that names the domain types, because
what it writes down is products; the layer rule in `tests/test_architecture.py` says so
now. The three tables keep the stricter rules of their own.

The obligations.

- **Pruning is never oldest-entry-first.** The newest entry of a search is the one a
  comparison is about. What goes is a whole search nobody has run lately.
- **Nothing in the journal may reach the pipeline.** It is opened before a run and
  asked after it, by the two doors. `BuyAgent.run` does not know it exists, which is
  what keeps the answer to a run the ranked products and nothing else, and what lets
  the server's stub agent still be a stub.
- **A field added to what is written down is a shopping history that holds more.**
  Three figures is the whole of it on purpose; anything else needs its own argument.
- **What it costs is said where somebody will read it.** `--journal`'s help, the form's
  box and the README all say what is kept, where, and how to stop.
- **The key is what was asked, never how it was answered.** A setting added to
  `journal_for` splits every existing history in two, silently.
