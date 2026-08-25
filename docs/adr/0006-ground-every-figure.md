# ADR-0006: Ground every product and every figure against the sources before ranking

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

Small models fill gaps. Two failures showed up repeatedly and neither is a
syntax problem, so ADR-0004's schema cannot see them:

- A figure is carried over from the prompt's own example, or simply invented,
  for a product whose page never quoted one.
- A product is reported that appears nowhere in the results at all -- a search
  for headphones returning the electric kettle used to illustrate the schema.

Ranking makes this worse rather than exposing it. An invented `$99.00` is not a
harmless blank: because price is scored relative to the other candidates, the
cheapest wins, so the fabricated figure takes the top spot. The one number
nobody wrote down decides the answer.

## Decision

`verification.ground()` runs between extraction and ranking, over the same text
the model was shown (`build_haystack()` of the `SearchResult` contents -- see
ADR-0005), and nothing reaches the ranking unsupported:

- A product whose name is absent from the sources is **dropped**. "Absent" means
  fewer than 60% of the name's distinctive tokens appear (`_NAME_COVERAGE`),
  ignoring the generic words shared with `extraction.GENERIC_WORDS`.
- A price, rating or review count absent from the text is **blanked** to `None`,
  and a blank scores neutral (ADR-0007) rather than winning.
- A rating counts as supported only where it is written like a rating --
  "4.3/5", "4.3 out of 5", "4.3 stars", "rated 4.3" -- because a bare `4.3`
  matches for a hundred other reasons, and the "5" in "out of 5" would vouch for
  any product claimed to be rated 5.
- Only the 0-5 scale is accepted. Confirming a claimed 4.5/5 with a "4.5 out of
  10" found on the page would vouch for a figure that actually means 2.25/5.
- Numbers are normalised before matching, so a page's "90,000" backs a
  reported `90000`.

## Consequences

Every figure in the report is one a source page actually printed. A model that
hallucinates costs recall -- the product disappears, or loses its price and drops
to mid-field -- rather than precision, which is the direction to fail in for a
tool whose output is a shortlist to click.

Two obligations follow. Extraction and verification must be given the *same*
text: hand grounding anything narrower and it rejects everything, which is why
the condensed content lives on `SearchResult`. And the coverage bar is a
heuristic in both directions -- a real product whose name the model wrote more
fully than the page did can be dropped, which is the cost of never ranking a
number nobody wrote down.

What this does *not* check is attribution: that a figure appears in the sources
does not prove it belongs to the product it was filed under. Two products
sharing a review count is a known limitation, recorded in the README rather than
fixed here.
