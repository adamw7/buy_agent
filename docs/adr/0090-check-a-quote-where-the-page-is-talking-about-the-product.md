# ADR-0090: Check a quote where the page is talking about the product

- **Status:** Accepted
- **Date:** 2026-10-23

## Context

[ADR-0025](0025-check-a-quote-against-the-page-it-came-from.md) checked a quote against
one page at a time, a page that mentions the product, and said what that left open: "a
review page covering eight headphones mentions all eight, so a verdict can still move
between products *within* one page."
[ADR-0073](0073-hold-the-scorer-to-the-model-number-and-the-verdicts-on-each-product.md)
gave the benchmark a key of whose verdict each line is, and its `faithful` metric has
counted that move ever since. The README lists it as a limitation.

The nightly of 23 October (run 39897228658) fell under `faithful`'s floor: `qwen3:0.6b`
quoted five times, and three of the quotes were no verdict on the product they were
filed under (0.400 against 0.50). Three runs of the same pipeline on the same Ollama,
dispatched the same morning, quoted the Sony twice, faithfully, and passed. The nightly
had been on a knife edge, and the log could not say which three quotes tipped it.

How wide the gap was is measurable without a model. Offering every judgement the three
cases' pages pass, each case's verdicts and its lines about nobody, as a quote for every
product in its key, `verify_opinions` kept all 86 that are a product's own verdict, and
241 that are not: a roundup's verdict on the Sennheiser for the Bose, a sale for the
JLab. The Bose could carry "The Sony WH-1000XM5 remains our overall pick at $328.",
which names the product it is about.

Other narrowings were measured on the same offer and rejected. Dropping a quote that
names another product of the run kept 149 of the 241, and every unnamed verdict stayed
free to move. Requiring the opinion vocabulary of `fetch._OPINION` kept 76 of the 86
and 201 of the 241.

## Decision

**A quote counts for a product only where the page is talking about it.**
`verification.said_of` places the quote on the line holding most of its five-word runs,
then walks up from that line to the nearest line naming any of the run's products. The
quote counts for the products that line names. A line naming two starts a part about
both. A quote above every named line is about none of them. A page whose title names
its product is that product's from the top.

**Naming is `mentions_name`**, the 0.6 bar that already decides whether a product is
real, which page it links to and which pages may be quoted for it (ADR-0025). The names
are the run's grounded products, the same list `verify_opinions` checks.

**It narrows ADR-0025 and replaces nothing.** A quote still has to be running text on
one page that mentions the product. `said_of` is one more condition on that page, so it
can only drop a quote the old check kept, never keep one it dropped.

## Consequences

On the same offer, 83 of the 86 own verdicts are kept and 12 of the 241 others. The
scripted answers score as they did: `PERFECT` 1.000 on every case, `SLOPPY` 0.605.

What it costs:

- **A verdict that names its product short is lost** where the line above it names
  another: "The Sony is worth the money if you fly monthly." under the Anker's line on
  FlightGear, and its like in the other two cases. The same verdict can still move to
  the product named above it, so 12 borrowings remain. Counting a short name as a
  mention was tried and is worse both ways, 79 of the 86 and 16 of the 241: the
  Bose's "merely mediocre next to the Sony" hands the lines after it to the Sony.
- **A product the run did not report is invisible.** The walk up stops at a run
  product's name, so a verdict under a product the model never reported counts for the
  run product named before it, as it did before.
- **A verdict above the first name is lost**, on a page whose title names nothing.
- **A price line passed off as a quote still counts**, in its own product's part of the
  page. Refusing lines that print a figure and no judgement word would have lost five
  of the 86, "remains our overall pick at $328" among them, and caught no borrowing.

What it obliges:

- **`mentions_name` now decides a fourth thing**: where on a page a product is being
  talked about. A word added to `GENERIC_WORDS` moves where a part of a page starts.
- **A page's lines must survive to `verify_opinions`.** `fetch.condense` keeps its
  lines in the page's order, and `build_haystack` keeps the content's line breaks. A
  change that joins a page's text into one line makes the whole page one part, about
  every product it names, and the check no narrower than ADR-0025's.
  `test_a_verdict_counts_for_the_product_the_page_is_talking_about` fails if it does.
- **The README's limitation shrinks to what is left**: a short name and a product the
  run did not report. `faithful` still measures it, and the benchmark's per-quote lines
  name each quote it did not count and whose line it is.
