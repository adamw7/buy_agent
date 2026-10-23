# ADR-0090: Keep a quote only where a page judges the product it is filed under

- **Status:** Accepted
- **Date:** 2026-10-23

## Context

[ADR-0025](0025-check-a-quote-against-the-page-it-came-from.md) checked a quote against
one page at a time, a page that mentions the product, and said what that left open: "a
review page covering eight headphones mentions all eight, so a verdict can still move
between products *within* one page."
[ADR-0073](0073-hold-the-scorer-to-the-model-number-and-the-verdicts-on-each-product.md)
gave the benchmark a key of whose verdict each line is. Its `faithful` metric counts
two mistakes grounding let through: a verdict on another product, and "a price line
passed off as a verdict".

The nightly of 23 October (run 39897228658) fell under `faithful`'s floor: `qwen3:0.6b`
quoted five times, and three of the quotes were no verdict on the product they were
filed under (0.400 against 0.50). The log gave counts only. Three runs of the same
pipeline dispatched the same morning quoted the Sony twice, faithfully, and passed.
Once the benchmark printed each quote, a fourth (run 39904028995), on the nightly's path
through the model, scored exactly 0.500 and showed what the other two were: the JLab's
and the Bose's own lines, "The JLab JBuds Lux ANC is rated 4.1 out of 5 from 8,900
reviews." Each is printed on a page about its product and judges nothing.

How wide both gaps were is measurable without a model. Offering every judgement the
three cases' pages pass (each case's verdicts and its lines about nobody) as a quote
for every product in its key, `verify_opinions` kept all 86 that are a product's own
verdict, and 241 that are not: a roundup's verdict on the Sennheiser for the Bose, a
sale for the JLab, and for the Bose "The Sony WH-1000XM5 remains our overall pick at
$328.", which names the product it is about. Offered every other line, it kept every
rating line on a page about the product.

Other narrowings were measured on the same offer and rejected. Dropping a quote that
names another product of the run kept 149 of the 241, and every unnamed verdict stayed
free to move. Requiring the judgement vocabulary of every quote kept 76 of the 86 and
201 of the 241. Requiring it, on top of the first rule below, of every quote that prints
any figure lost five verdicts that judge in other words ("remains our overall pick at
$328", "best under $100") and caught no borrowing the first rule had not.

## Decision

**A quote counts for a product only where the page is talking about it.**
`verification.said_of` places the quote on the line holding most of its five-word runs,
then walks up from that line to the nearest line naming any of the run's products. The
quote counts for the products that line names. A line naming two starts a part about
both. A quote above every named line is about none of them. A page whose title names
its product is that product's from the top. Naming is `mentions_name`, the 0.6 bar that
already decides whether a product is real, which page it links to and which pages may
be quoted for it (ADR-0025), and the names are the run's grounded products.

**A quote that restates a rating or a count of reviews must also judge.**
`verification.judges` refuses a quote printing a rating or a count, read as
`mentions_rating` and `mentions_review_count` read one, unless it is also in the
judgement vocabulary. A rating and a count are grounded as figures (ADR-0006); a line
stating them is the card's figures again. A price is not refused, since a price is how
an editor's pick is said.

**One vocabulary of judgement.** It moves from `fetch._OPINION` to `models.JUDGEMENT`,
as `STANDING_PHRASES` lives there: `fetch` keeps lines in it, and `verification` reads
it without importing a step it may not chain to.

**Both narrow ADR-0025 and replace nothing.** A quote still has to be running text on
one page that mentions the product. Each rule is one more condition, so together they
only drop a quote the old check kept, never keep one it dropped.

## Consequences

On the same offer, 83 of the 86 own verdicts are kept and 12 of the 241 others, and
every rating or count line without a judgement is refused. The scripted answers score as
they did: `PERFECT` 1.000 on every case, `SLOPPY` 0.605. Run 39904028995's two figure
lines would have been dropped, and its two Sony quotes kept.

What it costs:

- **A verdict that names its product short is lost** where the line above it names
  another: "The Sony is worth the money if you fly monthly." under the Anker's line on
  FlightGear, and its like in the other two cases. The same verdict can still move to
  the product named above it, so 12 borrowings remain. Counting a short name as a
  mention was tried and is worse both ways, 79 of the 86 and 15 of the 241: the
  Bose's "merely mediocre next to the Sony" hands the lines after it to the Sony.
- **A product the run did not report is invisible.** The walk up stops at a run
  product's name, so a verdict under a product the model never reported counts for the
  run product named before it, as it did before.
- **A verdict above the first name is lost**, on a page whose title names nothing.
- **A bare price line passed off as a quote still counts** ("The Anker Q45 is $99."),
  in its own product's part of the page.
- **A judgement stating a rating in other words is lost** ("Loved by 12,000
  shoppers"), as the vocabulary does not catch it.

What it obliges:

- **`mentions_name` now decides a fourth thing**: where on a page a product is being
  talked about. A word added to `GENERIC_WORDS` moves where a part of a page starts.
- **A page's lines must survive to `verify_opinions`.** `fetch.condense` keeps its
  lines in the page's order, and `build_haystack` keeps the content's line breaks. A
  change that joins a page's text into one line makes the whole page one part, about
  every product it names, and the check no narrower than ADR-0025's.
  `test_a_verdict_counts_for_the_product_the_page_is_talking_about` fails if it does.
- **A word added to `models.JUDGEMENT` moves both**: what `fetch` keeps (and so what
  ADR-0073's keys must list) and which figure-stating quotes count. Its patterns for a
  rating and a count are `verification`'s own, so a new way of writing a rating that
  grounding learns is refused as a quote too.
- **The README's limitation shrinks to what is left.** `faithful` still measures it,
  and the benchmark's per-quote lines name each quote it did not count and whose line
  it is.
