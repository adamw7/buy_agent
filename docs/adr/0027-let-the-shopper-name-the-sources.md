# ADR-0027: Let the shopper name the sources, and search nothing else

- **Status:** Accepted
- **Date:** 2026-08-27

## Context

Everything the report says comes from the ten pages a DuckDuckGo search
happened to return. ADR-0006 makes those pages the arbiter of what is true --
a figure they do not print is blanked, a product they do not name is dropped --
and ADR-0025 makes them the arbiter of what may be quoted. That is a strong
guarantee about *invention*: nothing in the report was made up.

It is no guarantee at all about *quality*. A page is a source because it ranked,
and the ten that rank for "wireless headphones under $200 price review" are
mostly affiliate roundups: a price, a five-star rating and a paragraph of
marketing copy, all grounded, all true of the page, none of it worth reading.
Meanwhile the shopper often already knows where the good information is. They
read one review site whose measurements they trust; they watch one channel whose
verdicts have been right before. The agent had no way to be told, and the search
had no way to hear it.

Three shapes of answer were considered.

**Ask the model to prefer good sources.** Rejected under ADR-0002 and the rule
it rests on: whatever decides the answer belongs in Python, where it is
testable. A small local model cannot be trusted to know which sites are
reputable, and "prefer" is not a property a test can assert.

**Rank a trusted source's products higher.** This keeps the whole web in the
pool and adds a term to the score. It is the weaker guarantee -- the report
still mixes facts from pages the shopper never asked for, and the affiliate
roundup still supplies a price when the trusted page did not -- and it needs a
fourth weight in `RankingWeights` to answer "how much higher", which is a number
nobody can defend.

**Narrow the pages.** The pages are already the arbiter of every fact, so
narrowing the pages narrows the facts, with no new machinery in the ranking, the
grounding or the prompts. That is the one that was taken.

## Decision

`AgentConfig.sources` holds the sources the shopper named -- none by default,
which is the whole web and the behaviour every existing run keeps. Given any,
`BuyAgent._search` searches those and nothing else, and everything downstream is
unchanged: the pool it produces is what gets fetched, extracted from, grounded
against and linked to.

A source is written the way people say one, and `buy_agent.sources` reads it
down to two parts:

- the **domain** (`rtings.com`, `youtube.com`), which is *enforced*: every
  result is put through `Source.covers()` and one from another host is dropped
  before the model sees it;
- the **term** -- a channel handle (`@mkbhd`), a section (`/headphones`) --
  which is added to the query as a quoted phrase and is *not* enforced.

The term is not enforced because a URL cannot carry it. `site:` narrows to a
domain, and a video's address says which video it is and nothing about who
published it, so a handle checked against the URL would discard every video the
channel ever posted and keep only the channel page. Enforcing the domain and
searching for the term is the strongest rule the addresses actually support.

Each source is searched for separately, since `site:` takes one domain, and the
configured search width is shared out between them rather than multiplied by
them. Results are pooled in the order the sources were given, deduplicated by
URL, and cut back to that width.

Naming sources that between them return nothing ends the run the way an empty
search always has. There is deliberately no fall back to the wider web: a
shopper who said where the facts come from would otherwise be shown facts from
pages they had refused, and told nothing about it.

## Consequences

**The guarantee gets stronger, and stays a Python one.** "Every figure and every
quote was printed by a page you named" holds by construction, because the named
pages are the only ones grounding ever sees. Nothing in `verification.py`,
`extraction.py` or `ranking.py` knows this feature exists.

**A source is a domain, and that is a real limit.** Naming `@mkbhd` searches
YouTube for that handle and keeps whatever YouTube pages come back -- which is
not the same as "only videos by that channel", and a video by someone else that
mentions the handle can get through. The report links to the page, so the
shopper can see whose it is. Tightening this needs something that knows a page's
author, which the URL is not; a future record can add it, but it must not be
done by filtering the URL, which silently empties the pool instead.

**A narrow source is a short report.** One site with three matching pages is
three pages, and the top 3 may come back as a top 1 -- or as nothing, which is
now a possible outcome of a run whose search backend worked perfectly. That is
the honest answer to "what do these sources say about this", and the alternative
is a report the shopper cannot trust the provenance of.

**Several sources cost several searches.** Five sources are five round trips to
DuckDuckGo for one run, which is five chances to be rate-limited into
`SearchError`. The width is shared out so the *fetching* does not multiply, but
the searching does.

**Four places have to agree, as every option does.** `AgentConfig.sources`,
`--source` in `__main__.build_parser`, `sources` in `api.parse_options` and
`api.defaults_payload`, and the field in `agent.types.ts` and `search-form`.
Sources are the one option that is a list, so they are also the one that does
not go through `api._read`: that renders every value with `str` first, which
turns a JSON array into its Python repr. `_read_sources` takes an array or a
separated string, and a query string can only ever send the latter.

**An unusable source fails the run rather than being dropped.** Naming three
sites of which one is a typo searches two and reports on two, which reads
exactly like a working run. `parse_sources` refuses the lot instead, with a
message naming the shapes that work -- on the CLI through
`argparse.ArgumentTypeError`, because argparse throws a type function's
`ValueError` away and prints "invalid value" in its place.
