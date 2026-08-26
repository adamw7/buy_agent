# ADR-0024: Read what the sources say about a product, and quote it word for word

- **Status:** Accepted
- **Date:** 2026-08-26

## Context

The pipeline read pages for figures and nothing else. `fetch.condense()` kept
the lines quoting a price or a rating (ADR-0005) and threw the rest away, so the
sentence a shopper actually searches for -- "the noise cancelling is uncanny for
the money, but the case is too bulky" -- was discarded before the model saw it.
What came out the other end was a shortlist of prices and star ratings, which
answers "how much" and never answers "is it any good".

`ExtractedProduct.notes` was the only opinion-shaped field, and it is the wrong
shape twice over. It asks the model for *its own* sentence about the product,
which makes it the one field in the report that is the model's judgement rather
than a source's -- against the rule that model output is never trusted as
judgement (ADR-0002) -- and nothing checks it, so it survives grounding by not
being a number.

Two things therefore had to change together: the pages had to be read for
opinions, and an opinion had to be checked as strictly as a figure. A quote is
in one way worse than a figure: an invented price is a number nobody wrote,
while an invented quote is words put in a named reviewer's mouth.

## Decision

**Opinions are read, grounded and quoted, never summarised.**

- `fetch.condense()` sweeps each page twice. The first sweep keeps the lines
  quoting a figure, as before; the second keeps the lines that read like a
  judgement -- `_OPINION` is a vocabulary of *judgement* ("reviewers found", "the
  downside is", "disappointing"), not of subject matter, which every line on a
  headphone page shares with every other. Each sweep spends a budget of its own
  (`AgentConfig.page_chars` and `opinion_chars`), so neither kind can crowd the
  other out; `opinion_chars=0` leaves the opinions unread.
- `ExtractedProduct.opinions` is a list of short quotes, copied word for word
  out of the results. The empty list is its "unknown", keeping the sentinel rule
  of ADR-0004: no nullable field in the decoding grammar.
- `verification.verify_opinions()` drops every quote the sources do not contain,
  inside `ground()` with the rest (ADR-0006). A quote is checked as overlapping
  runs of five consecutive words, most of which must appear in the sources as a
  phrase. Not word by word: "great sound, very comfortable" is vocabulary every
  headphone page contains and no page need have printed in that order, which is
  the same substring trap `mentions_name` avoids one level up.
- Merging takes opinions from **both** listings rather than from the fuller one
  (`extraction._merge_opinions`), which is why they are not in
  `_MERGEABLE_FIELDS` (ADR-0022). Two pages quoting two prices are in conflict
  and one has to lose; two reviewers are not.
- The report, the JSON and the product card show the quotes as quotes. Nothing
  scores or summarises them: ranking stays the blend of rating, popularity and
  price that ADR-0007 defines.

## Consequences

The report now answers the second half of the shopper's question, in the words
of the pages that were searched rather than the model's. A quote that survives
is one a source printed; a paraphrase does not survive, and a product simply
shows fewer quotes or none. That is the same direction of failure as every other
grounded field -- recall, not precision.

The obligations:

- **The run bar is deliberately strict, and strict in the middle.** Topping or
  tailing a real quote with a word of the model's own breaks only the runs at
  that end and still passes; changing a word in the *middle* breaks every run
  spanning it and fails. Loosening this to accept paraphrase would put words in
  a reviewer's mouth, which is the failure the whole record exists to prevent.
- **The prompt grew.** Two budgets per page instead of one takes the extraction
  prompt from ~3.3k tokens to ~4.3k, which still leaves room to answer inside
  the 8192-token window ADR-0019 defaults to. A smaller window needs
  `opinion_chars=0` rather than a smaller `num_ctx`.
- **Attribution is still unchecked**, as in ADR-0006: that a quote appears in
  the sources does not prove the reviewer was talking about the product it was
  filed under. The prompt asks the model not to move a verdict between products,
  and nothing enforces it. This is the same known limitation the README records
  for figures, now with a second field under it.
- **`notes` is left alone**, unchecked and the model's own -- deliberately, since
  removing it is a separate decision about the report's shape rather than part of
  reading the sources. The quotes sit next to it, and a reader can tell them
  apart: the quotes are in quotation marks because somebody else said them.
