# ADR-0004: Constrain both LLM calls with a JSON schema, and use sentinels rather than nullable fields

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

Both model calls have to come back as data the pipeline can use. Asking a small
model for JSON in the prompt and parsing what comes back fails in the ordinary
ways: prose around the object, a half-closed brace, a trailing apology.

Ollama's structured-output mode (`method="json_schema"`) fixes the syntax
problem outright -- the schema is compiled into a decoding grammar, so tokens
that would break it are never sampled. But that same mechanism creates a second
problem. A nullable field compiles to a union, and a small model handed
`number | null` for an unknown price reaches instead for the string it has seen
in a thousand product tables: `"N/A"`. That is not in the grammar, so decoding
goes wrong for the *whole batch* -- one unknown price loses ten products.

## Decision

Both chains use `with_structured_output(..., method="json_schema")`, over Pydantic
models in `models.py`. Two shapes of product exist, on purpose:

- **`ExtractedProduct` is what the model is asked for.** Every field is
  required and non-nullable, and "unknown" is a sentinel *within* the type:
  `-1` for price and rating, `0` for review count, `""` for the strings. The
  field descriptions say so ("Use -1 if unknown"). There is nothing for the model
  to answer that the grammar forbids.
- **`Product` is what the rest of the code uses**, where unknown is `None`.

`ExtractedProduct.to_product()` is the one conversion point: it turns the
sentinels back into `None`, drops a rating outside `0..5`, and tidies whitespace.

`ProductList` wraps the list, because Ollama's structured output needs a JSON
object at the root rather than an array.

A new extraction field is therefore added as a non-nullable field with a sentinel
and converted in `to_product()` -- never as `float | None`.

## Consequences

A malformed answer stops being a failure mode, and one missing figure can no
longer cost the batch. Everything downstream of `to_product()` gets to use plain
`None` checks, and the sentinels never leak past `models.py`.

The cost is two models for one concept and a conversion that must be kept in
step. It is also a *hint*, not an enforcement: the grammar guarantees a number,
not an honest one. A model that does not know the price can emit `-1`, or it can
emit a plausible-looking `199.99`. That second case is exactly what ADR-0006
exists to catch, and it is why grounding is not optional.
