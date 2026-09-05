# ADR-0042: Keep the page a quote came from, and link it

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

ADR-0024 gave a product the words the sources used about it, and ADR-0025 made
those words checkable: a quote survives only where *one page that mentions the
product* has it as running text. Both were about whether the pipeline could be
trusted not to invent a verdict. Neither said anything about the shopper, who
gets three sentences in quotation marks and no way to see where any of them came
from.

Every other reported fact can be checked. A price, a rating and a review count
are each grounded against the pages (ADR-0006), and the product carries a link to
the page it was found on, chosen out of the results rather than off the model
(ADR-0017) -- so a shopper who doubts the figure can click through to it. A quote
could not be checked at all. It is also the field a small model is *most* likely
to have got subtly wrong: paraphrasing is what a small model does when asked to
copy, and `verify_opinions` deliberately tolerates a word of the model's own at
either end of a real quote, which is the tolerance ADR-0025 argues for and also
the gap a shopper would want to see through.

The page was already known. `verify_opinions` walks the results one at a time and
keeps a quote on the first one that both mentions the product and prints the
words -- it has the page in hand at the moment it decides -- and then dropped it
on the floor, keeping the string alone. Nothing had to be searched, fetched or
inferred to fix this; the answer was being computed and discarded.

## Decision

A product's `opinions` are `Opinion` objects -- the `text` and the `url` of the
page that printed it -- rather than bare strings.

**The URL is written by `verify_opinions`, out of the results that were
searched.** It is the first page that both mentions the product and has the quote
as running text, which is the same "first result that mentions it" rule
`attribute_sources` picks the product's own link by, so the two answers cannot
disagree about which page a product was found on. `ExtractedProduct.opinions`
stays a list of plain strings: the model is asked for the words and never for the
page, exactly as it is never trusted with a link (ADR-0017). A quote arriving
with a URL on it -- which only a hand-built `Product` can do -- has it replaced.

**`url` is nullable, and `None` is not the same as a dropped quote.** A search
result can carry no URL of its own; the page still printed the words, and a
verdict is not worth throwing away for want of a link to it. The two are told
apart in the loop rather than collapsed, because collapsing them would silently
drop every quote off an unlinked result.

**The pair travels as one object.** Through `_merge_opinions`, through
`distinct_quotes`, into `product_payload` and into the card. That is what keeps it
out of ADR-0022's qualifier scheme: a currency needs a rule saying it may not
outlive its price because they are two fields, while a quote and its page are one
value and neither half can move without the other. Identity for de-duplication is
still the casefolded *text* -- two listings quoting one reviewer differ by
capitalisation and by which page was read, and they are one quote -- and the one
kept is the earlier, so a syndicated review points at the first page that
printed it.

**Both front ends show it.** The card renders the quote and a small `source` link
beside it; the CLI report names the page only where it is not the product's own
link, a quote off the page printed two lines above being the ordinary case and
three repetitions of one URL saying nothing.

## Consequences

A quote is now evidence rather than an assertion. The shopper can follow it, and
a paraphrase that got through the tolerance is one click from being visible as
one -- which is worth more than tightening the check would be, since tightening
it drops real quotes (ADR-0025 is explicit about that trade).

It also gives the run something it could not say before: which of several pages a
verdict came off, when the product is listed on eight of them. `integration/`
asserts it as an invariant -- every quote names a page that was searched, that
mentions the product, and that has the words -- which is the strongest of the
three because it is the one a wrong answer would be actively misleading about.

Four obligations.

- **The URL is never the model's.** Same rule as ADR-0017 and the same reason: a
  blanked figure reads as "unknown" while a wrong link is one somebody clicks.
  A quote's page is chosen out of the searched results or it is `None`.
- **`None` and "dropped" must stay different answers.** The loop that decides
  both is written out rather than folded into a comprehension precisely so the
  two are visibly separate; a refactor that returns `None` for "no page printed
  it" would drop every quote off an unlinked result and no test of the *words*
  would notice.
- **The pair moves together.** A merge, a de-duplication or a payload that takes
  the text without the URL puts one page's link under another page's words,
  which is the class of mistake ADR-0022 exists to prevent on the figures.
- **`agent.types.ts` carries the `Opinion` interface**, and
  `tests/test_conventions.py` holds it against `product_payload`: a field added
  on one side of the language boundary and forgotten on the other is otherwise
  an undefined in an `href`.

The cost is a wider payload -- a URL per quote, up to three per product, mostly
repeating the product's own link -- and a small model still cannot be made to
quote perfectly. This makes its mistakes visible; it does not prevent them.
