# ADR-0089: Ask for one product at least, and leave stock to the schema

- **Status:** Accepted
- **Date:** 2026-10-22

## Context

An end-to-end run on 22 October scored `qwen3:0.6b` (Q4_K_M, Ollama 0.40.8, four CPU
cores) at 0.873 on `headphones`, 0.437 on `laptops` and 0.000 on `espresso`. The
nightly scores only the first (ADR-0072), so neither of the other two had been seen.
They failed in two different ways.

**`laptops` lost every figure.** The model named four real laptops and wrote `-1` for
each price and rating, beside `"availability": "in stock"` on each. The pages print
both figures. A bisect over the same model and server named the commit that added
[ADR-0079](0079-ground-a-listings-stock-and-condition-with-its-price.md), and within
it the one line added to `EXTRACTION_PROMPT`: "availability is 'in stock' or 'out of
stock', and condition 'new', 'used' or 'refurbished', only where the results say so".
With that line taken out and nothing else changed, `laptops` scored 0.726 with 6/6
figures right; the code from before ADR-0079 scored 0.664 on the same setup. Taking the
two fields out of the schema instead, or the stock lines `fetch.condense` now keeps,
brought no figure back. A rule about two qualifiers had pulled a small model's
attention off the figures they qualify.

**`espresso` reported nothing**, from the code before ADR-0079 too. The model answered
`{ "products": [ \n  \t] }`. Unconstrained, it lists the machines with their prices as
the pages print them, "279,00 €", and a JSON number cannot hold a decimal comma: under
the grammar ([ADR-0004](0004-json-schema-and-sentinels.md)) the one way forward it had
was to close the list. Taught the convention in the prompt, it recovered `espresso` and
lost six of nine figures on `headphones`. With the schema asking for one product at
least (`minItems`), it wrote two real machines, 5/6 figures right, and the other two
cases kept every figure they had.

## Decision

**The extraction schema asks for at least one product, and only the grammar asks.**
`ProductList.products` declares `"minItems": 1` through `json_schema_extra`, so it
reaches the server's grammar and not the reader: `read_answer` still accepts an empty
list, which is what a server that ignores the keyword can send.

**Whether anything was found is Python's call.** `clean_products`, `ground` and the
shopper's bounds decide it, as they already decided what was real (ADR-0006).

**The extraction prompt does not ask for a listing's stock or condition.** The fields
stay in `ExtractedProduct` with their sentinels, `to_product` folds what a model writes
(ADR-0079), and `verify_standing` keeps only what a page about the product prints. A
small model fills them or leaves them `""`; either way no figure is lost to them.

## Consequences

`qwen3:0.6b` scores 0.813, 0.741 and 0.662 on the three cases, against 0.873, 0.437
and 0.000 before: every case clears the overall floor, and no case reports nothing
because of how a page writes its prices.

What it costs. `headphones` reports three of its five products where it reported four
(0.873 to 0.813, every figure still right), as the code before ADR-0079 scored it: the
line's removal did that, not `minItems`. On pages holding nothing the shopper asked
for, the model must still name something. A name no page prints is dropped by
grounding, but a product a page does name can reach the report although it misses the
request: the same over-reporting a small model already does, which the benchmark
counts against `genuine`. Without a rule asking for them, stock and condition may be
extracted less often; they are still never shown unless a page prints them.

The obligations.

- **`minItems` is a schema declaration, never `min_length`.** A validator would turn a
  server's empty answer into `UnreadableAnswerError`, which `_extract_products`
  reports as a model out of room (ADR-0019): the wrong sentence, and a run that found
  nothing reported as a failure. `test_the_grammar_is_asked_for_one_product_at_least`
  and `test_an_empty_list_still_reads_as_nothing_found` hold both halves.
- **A new rule in `EXTRACTION_PROMPT` is scored on every case before it lands.** Run
  `python -m benchmark --model qwen3:0.6b` over all three cases before and after, and
  quote both runs, as with a floor. A rule that helps one case can cost another, and
  the nightly gates only one.
- **A field joins the prompt's rules only if a figure needs it.** A qualifier is asked
  for by the schema's field name and grounded in Python. Asking for it in the prompt is
  what cost `laptops` its prices.
