# ADR-0074: Pay the score nothing for silence or for luck

- **Status:** Accepted
- **Date:** 2026-10-05

## Context

ADR-0036 split each "did it copy correctly" question into a completeness half and an
error half, so a scorecard could tell a model that reported nothing from one that
reported nonsense. The score then weighed the eight shares by a weighted arithmetic
mean, and three of them -- `attribution`, `faithful` and `order` -- read 1.0 when they
had nothing to count. That is the right thing for a scorecard to show and a floor to
read: nothing reported is nothing wrong. As a score it paid for silence.

- An empty answer scored 0.308. One product named and linked, with nothing else,
  scored 0.585.
- Five products named and linked, with not one price, rating or quote, scored 0.731,
  above `sloppy`'s 0.701 -- and `sloppy` had copied seven figures of nine. A shopper
  given the first has nothing to rank on.
- The one real model on record, `qwen3:0.6b`, quoted nothing and got `faithful` whole.
- With equal weights an arithmetic mean also makes reporting nothing worth exactly as
  much as reporting everything with half of it wrong.

`order` had two problems of its own. A shuffled ranking puts half its pairs in order on
average, so 0.5 was luck, paid like skill; its 0.25 floor sat below a shuffle, failing
fewer than one shuffle in eight. And the ideal ranking was built from each product's
canonical figures, so a run reporting a price the key accepts -- the Sennheiser's euro
listing -- lost a tenth of `order` for being right.

## Decision

The scorecard keeps showing what it showed. The score stops paying for silence and for
luck.

- **A pair counts by the weighted harmonic mean of its halves** (`scoring.PAIRS`), with
  the weights `METRICS` already gave them. A pair is worth only as much as its weaker
  half allows, so one right figure in fifteen counts for little however right it is.
- **A share with nothing to count counts 0 in the score**, whatever it shows. Its empty
  value in `METRICS` is what the scorecard prints and the floors read.
- **`order` counts only above a shuffle** (`scoring.CHANCE`): its concordance rescaled
  so that 0.5 is nothing and 1.0 is still everything.
- **The ideal order is built from the figures the run was right to report**: each pair
  the run reported where the key accepts it, the entry's own where it does not. A blank
  or a wrong figure still costs order; an accepted one never does.
- **The score says what it is made of.** `Scorecard.parts` holds each part's weight and
  value, and the scorecard's `weighed as` line, the page and `--json` all show it.

## Consequences

An empty answer scores 0, the five bare names 0.462, `sloppy` 0.675 on the headphones
and 0.605 over the three cases, and `perfect` still exactly 1.000. Re-scored from its
counts, the recorded `qwen3:0.6b` comes to 0.725 rather than 0.815. A kept run is
re-scored from its counts on today's rules, as ADR-0070 has it, so the board needs
nothing dropped.

What it obliges:

- **Reporting nothing and reporting nonsense now score alike on a pair: 0.** The
  scorecard still tells them apart half by half, and so do the floors -- nonsense fails
  `attribution`'s, nothing fails `figures'`. That is what the split was for. The score
  answers a different question: what a shopper was given.
- **A new metric is declared twice**: in `METRICS`, with its weight and what it shows on
  nothing, and either in a pair in `PAIRS` or alone. A metric luck can score has a
  `CHANCE`. `tests/test_benchmark.py` holds each rule to a case it changes.
- **The floors keep their numbers and their reading.** None was raised here: each reads
  the share the scorecard shows, as before. `order`'s floor still sits below a shuffle,
  and raising it is a commit of its own quoting runs (ADR-0036) -- which the nightly now
  keeps (ADR-0072). The overall floor reads a score that is lower at the bottom than it
  was, which the one run on record clears by far.
- **Every pinned score moved**, `sloppy`'s three among them, and the README's recorded
  run is quoted at both.
