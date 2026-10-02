# ADR-0070: Compare local models over several cases, and give the comparison a page of its own

- **Status:** Accepted
- **Date:** 2026-10-02

## Context

ADR-0036 wrote down the right answer for one corpus and scored one run against it.
That answered the maintainer's question -- did a change to a prompt or a threshold
make things better? -- and left the operator's unanswered: of the models this server
holds, which one should the agent use? The README said as much: "No model larger
than the nightly's `qwen3:0.6b` has been scored; `python -m benchmark --model <tag>`
is how to find out." Finding out meant one command per model, reading scorecards
side by side, and keeping notes, since a run printed its numbers and forgot them.

Three things made one corpus the wrong instrument for that question:

- **It measures one kind of page.** Ten American review pages priced in dollars. A
  model that misreads "1.299,00 €", takes a monthly payment or a cashback for the
  price, or calls a Canadian listing dollars scores the same as one that does not,
  because none of those is on the page.
- **Half of what the agent asks a model is not scored.** The query step (ADR-0002)
  never reaches the scorer: the corpus is served whatever the query said. A model
  that answers "Sure! Here is a query: ..." or adds a brand the shopper never named
  passes, and searches the real web badly.
- **Nothing says how long a model took**, which on a CPU is the difference between
  a usable model and one that times out (ADR-0051).

And one constraint shaped where a page could live: the package may not import
`benchmark/` (ADR-0047), which is neither shipped in the image nor mutated
(ADR-0064). The shop's server and its Angular app are the package's.

## Decision

Score several contenders over several cases, keep what they scored, and serve the
comparison from a page of the benchmark's own.

- **A case is one use of the agent**: a request, the pages its search returns, the
  key to what those pages print, the query constraints the request states, and a
  perfect and a sloppy script (`benchmark/cases.py`). There are three: `headphones`
  (ADR-0036's corpus, unchanged, still the nightly's), `laptops` (thousands
  separators, spec sheets, a monthly payment, a student price, a Canadian listing)
  and `espresso` (decimal commas, dotted thousands, an American page in dollars,
  cashback and accessories). Every key is held to ADR-0036's rules and one more:
  every page that mentions a product is one its entry lists.
- **The query is scored apart** (`benchmark/query.py`), as checks with sentences:
  each constraint kept (a case lists the spellings that keep it), no brand the
  shopper did not name, no figure the shopper did not give, and short enough to be
  one query. A query the model garbled is one failed check, since the agent then
  searches with the request. It is not blended into the score: it moves the search,
  not what the pages say.
- **A contender is a model on a server or a scripted answer**, reached the way a run
  reaches one: `config.model_server.chat_model(config)` on the case's settings, the
  shipped defaults (ADR-0019, ADR-0029, ADR-0050). A stopwatch around the model times
  each question.
- **An unreadable answer is the case's result; a model that cannot be asked is no
  result.** The first scores 0 and is kept. The second -- nothing listening, nothing
  pulled, no answer within the timeout -- is reported in the server's own words
  (ADR-0009), kept nowhere, and its remaining cases are skipped.
- **Standings are each contender's latest run of each case**, ranked by how many
  cases it has run, then the mean score (a failed run counting 0), then the query,
  then the time its model took. A mean over the easiest case is no comparison with
  a mean over all three.
- **The board keeps the runs** at `$BUY_AGENT_CACHE_DIR/benchmark/board.json`, beside
  the journal (ADR-0060) and written the same way. Both doors read it afresh and add
  to it. Each run carries its case's fingerprint -- request, pages, key, metrics --
  and a run whose fingerprint no longer matches is left out: it scored another case.
- **The page is `python -m benchmark.server`**, on `127.0.0.1:8100`: static files in
  `benchmark/web/` with no build step, served by a subclass of the shop's
  `BuyAgentHandler`, so a request is admitted, refused and answered exactly as the
  shop's are (ADR-0018). Python orders and words everything the page shows
  (ADR-0012). One comparison runs at a time, on a thread of its own and not on the
  page's connection: closing the page does not stop it, unlike a search (ADR-0034),
  because a comparison is minutes of work somebody comes back to. **Stop** ends it at
  the next step.

## Consequences

A model is chosen from evidence: pull three, tick them, read one table. The
weaknesses a single dollar corpus hid each have a case, the query step has a score,
and a slow model says so in seconds rather than by timing out a shopper's run.

What it obliges:

- **A case is a module and a row.** `REQUEST`, `QUERY`, `REFINED_QUERY`, `PAGES`,
  `PAGE_TEXT`, `ANSWER_KEY`, `PERFECT` and `SLOPPY` in `benchmark/`, and a `Case` in
  `cases.CASES`. `tests/test_benchmark_cases.py` reads every key back off its
  condensed pages, runs `PERFECT` to exactly 1.000 and pins `SLOPPY`'s counts.
  **Editing any case's pages means re-running it**, as ADR-0036 says of the first.
- **A new case moves every contender down until it has run it**, since the
  standings rank by cases run. That is the rule working, not a bug.
- **The nightly is unchanged**: it scores the headphones case alone, inside its five
  minutes (ADR-0026). The other cases are run on demand.
- **The page's script has no test harness of its own.** What holds it is
  `tests/test_benchmark_server.py`: the markup loads only its own files and carries
  no inline script or handler (the CSP refuses both, and only a browser would show
  it, ADR-0066), the script writes text and never markup -- model output reaches the
  page -- every element it looks up exists, and every field it reads is one a
  payload carries, which is `agent.types.ts`'s rule for a page with no types.
- **The handler subclass leans on the shop's protected methods** (`_refused`,
  `_read_json`, `_send_json`, `_serve_static`, `_models`, `_answered_here`). A
  rename there breaks the page, and the page's server tests are what notice.
- **`--json` writes the standings payload**, the page's own shape, not one
  scorecard. The exit code keeps its meaning: 0 only when every run finished and
  cleared every floor.
- **A model's first question includes loading it.** Contenders run one after
  another, each over every case, so each pays that once, on its first case.
- **The board is not a cache.** Nothing expires it; **Clear the board**, or deleting
  the file, is how a run is forgotten.

The first espresso sloppy run showed what the key is for: a list's "3." after
"3,412 reviews." grounds a review count of 3 against the pooled pages, which the
invariants cannot see and the scorer counts as a misattributed figure.
