# ADR-0078: Read what a page declares as lines it prints

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Most shops and many review sites declare their products to search engines in
schema.org JSON-LD: a `<script type="application/ld+json">` naming the product, its
offers (price, currency, availability, condition, seller) and its aggregate rating.
`fetch.html_to_text` dropped every script before reading a page, so those figures
reached neither the model nor grounding. What was left was the visible text, where a
price may be split across elements, drawn from a script, or missing from the first
`page_chars` that `condense` keeps. A price no page printed visibly was blanked by
grounding, and a blanked price cannot be ranked on or paid for (ADR-0006, ADR-0043).

Two ways of using the declaration were open. It could be parsed into `Product`s and
merged with what the model extracted, a second extractor beside the first. Or it
could be written out as the plain lines a shop would print and put in front of the
page text, where the model reads it and grounding checks it like any other line.

## Decision

**A declaration is turned into lines of page text, not into products.** `fetch.py`
finds the JSON-LD scripts (the `type` matched case-insensitively) and hands their text
to `structured.declared`, which writes one line per offer and one per rating, each
naming the product:

```
Sony WH-1000XM5: 348.00 USD, in stock, condition: new, sold by AudioShop
Sony WH-1000XM5: rated 4.6/5 from 3200 reviews
```

The lines go *first* in the page's text, so `condense`'s budget reaches them before
anything else, and they are cached with the page (ADR-0040). Each is written in the
form grounding already accepts: a price with its code, a rating with its scale, a
count with who was counted, a standing in the words `STANDING_PHRASES` reads
(ADR-0079). A rating declared on another scale is put on five. `structured.py` reads
JSON only -- `fetch.py` stays the one module that parses HTML -- does no I/O, and sits
in the domain layer beside `bounds.py`, a reader of text in plain Python. Microdata
and RDFa are not read.

## Consequences

The model and the checks still see one text (`fetch.enrich` puts it on
`SearchResult`), so nothing in grounding, ranking or paying learns that a figure came
from a declaration rather than from the visible page. A declaration is what the page
says about itself and is held to exactly what visible text is: a product it names
must still be named in the sources, a price it declares is a price the page printed.

The obligations.

- **A line `declared` writes must be one grounding accepts.**
  `tests/test_structured.py` runs the written lines through `mentions_number`,
  `mentions_rating`, `mentions_review_count` and `mentions_standing`; a change to
  either side that breaks one blanks the very figures this was for.
- **The lines stay short and bounded.** At most twenty products per page, names over
  120 characters skipped, so a catalogue page cannot spend `page_chars` and no line
  passes `condense`'s 300-character ceiling.
- **`structured` is in `benchmark.pipeline.MODULES`**, since it decides what a run
  reports (ADR-0075); a change to it marks kept runs as no comparison.
- **Reading another format** (microdata, RDFa) is another writer of the same lines,
  never a second path into `Product`.
