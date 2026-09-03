# ADR-0036: Score the agent against a fixed answer key, beside the invariants

- **Status:** Accepted
- **Date:** 2026-09-03

## Context

`integration/` runs the whole pipeline against a real model nightly (ADR-0026)
and asserts the promises the pipeline makes: every name and figure in the
sources, every quote printed on a page about that product, every link a page that
was searched, nothing listed twice, the ranking ordered and numbered. Those
assertions are deliberately not about whether the model was *right* -- a 0.6B
model is not held to an answer -- which is the correct bar for a job whose point
is to notice that Ollama's `method="json_schema"` decoding changed.

It leaves two holes.

The first: a green nightly run says nothing about quality. A run that found two
products out of seven and a run that found five pass identically, so the question
a maintainer actually has after changing a prompt, a threshold, `GENERIC_WORDS`
or the model tag -- did it get better or worse? -- has no answer anywhere in the
project, and every judgement about it is somebody reading a log.

The second is sharper: three real failures are invisible to those invariants by
construction rather than by oversight.

- **A figure copied off another product's line.** `verify_numbers` grounds each
  figure against the *pooled* haystack, which is what makes grounding one cheap
  pass over all ten pages. So the `$349` printed for the Bose vouches for a
  `$349` reported for the Sony, the invariant asserting that every figure is
  printed in the sources passes, and the report puts a wrong price in front of a
  shopper.
- **One product reported twice.** `deduplicate` merges names differing by
  *descriptive* words (ADR-0008), and a brand is not one, so "Anker Soundcore
  Space Q45" and "Soundcore Space Q45" both stand. The invariant test checks this
  by re-running `merge_variants`, so it necessarily agrees with what the merge
  missed -- it is the same rule.
- **A ranking in the wrong order.** `test_the_ranking_is_ordered_and_numbered`
  holds whatever the scores are, so misread figures produce a correctly-numbered
  list of the wrong products in the wrong order.

Each needs the one thing the invariants do not have: knowledge of what the right
answer was. Nothing in `integration/` can be given it, every test there being
written to hold for *any* answer.

## Decision

Write the right answer down, and score against it.

`benchmark/` holds four things: the corpus (ten fabricated pages, one request,
the run's widths), the **answer key** -- what those pages print, product by
product -- a **scorer** that is a pure function of a run's products and those
pages, and two **scripted answers** that exercise it with no model at all.

Four rules hold it together.

- **The corpus lives here and `integration/conftest.py` reads it back.** One
  corpus, one model call, two questions asked of the same run. Two corpora would
  be a benchmark score attached to a run nobody asserted anything else about, and
  a second inference inside a job with a five-minute budget.
- **The key records what each page prints for each product, as sets -- not one
  right answer.** `$328`, the refurbished `$269` and EuroTech's `329 EUR` are all
  things the sources say the Sony costs, and a model reporting any of them has
  copied rather than invented. A currency travels with its price and a review
  count with its rating, as pairs (ADR-0022), so "329 USD" -- two figures the
  corpus prints and a pairing it never does -- is one wrong price. The canonical
  value beside each set exists to build the ranking the run should have produced,
  and for nothing else.
- **Every metric is a share in `[0, 1]`, higher-is-better, declared once in
  `scoring.METRICS` with its weight, and each "did it copy correctly" question is
  split into a completeness half and an error half.** A model that reports
  nothing and one that reports confident nonsense are not equally good, and one
  blended number calls them so.
- **The scorer uses the pipeline's own rules where it has one:** `NAME_TOKENS`
  and `GENERIC_WORDS` for names, the *condensed* text the model was shown for
  quotes, `rank_products` for the ideal order. A benchmark with its own idea of a
  name's words would be scoring the pipeline against a rule it does not follow.

Two ways to run it. `python -m pytest integration` scores the nightly run and
fails under `benchmark.scoring.FLOORS`. `python -m benchmark --scripted perfect`
runs a hand-written answer through the whole real pipeline with no model and no
network, and scores 1.000.

## Consequences

The nightly job reports a number, logged whole pass or fail, so a change to a
prompt or a threshold can be argued about from evidence; the three failures above
have somewhere to be caught; and the scorer is checked by the same suite as
everything else rather than by its own output, per this project's rule that
whatever decides an answer belongs where it is testable.

What it costs:

- **The answer key is a transcription, and transcriptions rot.** A line edited in
  `benchmark/corpus.py` can make a figure in `benchmark/answers.py` unreachable,
  putting a silent ceiling under every score the nightly ever reports.
  `tests/test_benchmark.py` reads the key back off the condensed corpus -- every
  name grounded, every figure printed, every listed page really mentioning the
  product -- and `PERFECT` scoring exactly 1.000 end to end is the same check
  through the whole pipeline. **Editing the corpus means re-running both.**
- **The floors are a tripwire, not a target, and they are set low.** A floor at
  the level a 0.6B model happens to reach today fails the job for a reworded
  prompt, which is how a scheduled run gets ignored. Raising one is a deliberate
  commit quoting the runs that justify it.
- **`benchmark/` is one more place a corpus change has to be thought about**, and
  is now named in `setup.cfg`'s `also_copy` (the Saturday run mutates a copy of
  the tree, and a file left out fails it at collection) and in `.dockerignore`.
  `tests/test_conventions.py` reads the first list back.
- **`links` will sit at 1.000 almost always**, `attribute_sources` (ADR-0017)
  making a wrong link nearly impossible. That is a guarantee being shown to hold
  rather than a metric with nothing to say, but the overall score moves less than
  it looks like it should when links break.
- **A benchmark measures what it can count.** Nothing here scores whether the
  products suit a shopper who asked for something comfortable for flights: the
  pipeline does not rank on that either (ADR-0007), and a key claiming to would
  be an opinion dressed as a transcription.
