# ADR-0017: Attribute a product's link to the searched page that mentions it

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

`ExtractedProduct` has asked the model for a `url` since the schema was written,
`Product` carries it, `api.product_payload` sends it, and the product card
renders the name as a link to it with the host beside it. The whole path exists.

It has never carried anything. In a real run against `gemma4:12b` -- "campaign
series books about WWII 1943-1945", ten results, five of them fetched -- all six
products came back with `url: null`, so no card was ever a link. The extraction
prompt is about names and figures; the link is one field among eight in a
constrained decode, and a small model leaves it empty.

The fix is not to ask harder. A link is the field where a wrong answer is worst.
ADR-0006 blanks an unsupported price because an invented figure would win the
ranking, but a blank price still *shows*, as "price unknown", and scores neutral
(ADR-0007). A link is what the shopper actually clicks. An invented one is not a
worse shortlist, it is a page nobody vouched for, offered under the name of a
product the agent recommended.

Nor is asking necessary, because the answer is already in the prompt. Each block
`extraction.format_results` builds carries its own `URL:` line, and the coverage
rule from ADR-0006 already decides which page mentions which product.

## Decision

`verification.attribute_sources()` runs inside `ground()`, after the figures are
verified and before `deduplicate` merges anything (the order ADR-0008 fixes):

- A link the model reported is kept **only if it names one of the pages that were
  searched**. Anything else is discarded, the same way an unsupported figure is.
- Otherwise the link is worked out from the sources: the **first result whose own
  text mentions the product**, by `mentions_name` -- the same 60% coverage bar
  that decided the product was real.
- A product no single page mentions keeps **no link**. `drop_ungrounded` asks
  whether the results *as a whole* mention a name, so a name two pages each half
  cover survives grounding with nowhere to point. It gets `None` rather than
  borrowing the first page's.

The model is still asked for a `url`, and the field stays on the schema. It is
now only ever a vote that agrees with the sources or is thrown away.

## Consequences

Every product in the report links to a page that was actually read, and the UI
became a page of links without a line changing in it -- the card was already
written for a link it never received.

What it costs is precision about *where*: the link is the page the product was
found on, not the product's own listing on it. A review of nine headphones gives
all nine the same URL. That is the honest answer to what the agent knows, and it
is what the host pill on the card says.

Three obligations follow. Attribution must stay inside `ground`, before
`deduplicate`, or `_combine` will merge links from pages that were never
searched. `extraction._completeness` counts `url`, and now that grounding fills
it in for nearly every product the field no longer separates a fuller listing
from a thinner one -- a future tie-break must not lean on it. And this is
attribution for *links only*: ADR-0006's closing note, that a figure appearing in
the sources does not prove it belongs to the product it was filed under, still
stands for prices, ratings and review counts.
