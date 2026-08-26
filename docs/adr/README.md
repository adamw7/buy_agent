# Architecture decisions

Why this codebase is shaped the way it is, one decision per file, in the order
they were written down. `docs/architecture.md` shows *what* the system is;
these say *why*, and what was rejected on the way.

Most of them are reactions to the same constraint: the model is a small one,
running on the shopper's own machine, and it cannot be trusted with judgement.

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions in ADRs | Accepted |
| [0002](0002-fixed-pipeline-not-a-tool-loop.md) | Fix the pipeline instead of letting the model drive a tool loop | Accepted |
| [0003](0003-local-ollama-no-api-keys.md) | Run against a local Ollama model, with no accounts or API keys | Accepted |
| [0004](0004-json-schema-and-sentinels.md) | Constrain both LLM calls with a JSON schema, and use sentinels rather than nullable fields | Accepted |
| [0005](0005-fetch-and-condense-pages.md) | Extract from fetched pages, condensed to their priced lines, not from search snippets | Accepted |
| [0006](0006-ground-every-figure.md) | Ground every product and every figure against the sources before ranking | Accepted |
| [0007](0007-rank-in-python-missing-scores-neutral.md) | Rank in Python, and score missing data neutral rather than zero | Accepted |
| [0008](0008-post-extraction-order-and-shared-vocabulary.md) | Clean, then ground, then deduplicate -- over one shared word list | Accepted |
| [0009](0009-three-failure-modes.md) | Raise exactly three failure modes, and list them in three places | Accepted |
| [0010](0010-stdlib-http-server.md) | Serve the web tier from the standard library | Accepted |
| [0011](0011-stream-a-run-as-sse.md) | Stream a run as Server-Sent Events, and name the terminal failure `failure` | Accepted |
| [0012](0012-the-browser-decides-nothing.md) | The browser decides nothing | Accepted |
| [0013](0013-ui-as-a-separate-angular-workspace.md) | Keep the UI a separate Angular workspace with its own toolchain | Accepted |
| [0014](0014-conventions-tests-over-coverage.md) | Guard cross-module conventions with a test that reads the declarations | Accepted |
| [0015](0015-package-the-web-tier-as-a-container.md) | Package the web tier as a container, with Ollama left outside it | Accepted |
| [0016](0016-mutation-testing-weekly-not-per-push.md) | Check the tests with mutation testing, weekly rather than on every push | Accepted |
| [0017](0017-attribute-links-to-the-page-that-mentions-them.md) | Attribute a product's link to the searched page that mentions it | Accepted |
| [0018](0018-guard-the-loopback-server-against-other-pages.md) | Guard the loopback server against the other pages in the browser | Accepted |
| [0019](0019-default-to-a-thinking-model.md) | Default to a thinking model, and default the settings it needs to answer | Accepted |
| [0020](0020-test-on-windows-as-well-as-linux.md) | Run both suites on Windows as well as Linux | Accepted |
| [0021](0021-carry-no-exports-the-pipeline-does-not-use.md) | Carry no exports the pipeline does not use | Accepted |
| [0022](0022-merge-figures-in-groups.md) | Merge a listing's figures in groups, never field by field | Accepted |
| [0023](0023-one-script-starts-everything.md) | Start the whole stack from one script, and give it nothing to decide | Accepted |
| [0024](0024-read-and-quote-what-the-sources-say.md) | Read what the sources say about a product, and quote it word for word | Accepted |

ADR-0002 onwards are retrospective: they record decisions that were already in
the code when the log was started, so their dates are when they were written
down rather than when they were made.

## Adding one

Copy [`0000-template.md`](0000-template.md) to the next free number, write it,
and add the row above. Three rules, from [ADR-0001](0001-record-architecture-decisions.md):

- **Numbers are never reused**, and an accepted record is never rewritten to say
  something different. A decision that changes gets a new ADR, and the old one is
  marked `Superseded by ADR-NNNN`. Typos and broken links are fine to fix.
- **Write the consequences honestly**, including the ones that hurt. The
  obligations are the useful part -- what a future change must not break, and
  which other file has to be edited in step.
- **Not every change is a decision.** An ADR is for a choice that constrains
  later work. How a function is written is not one.

`tests/test_conventions.py` checks that this index and the directory agree, and
that each record has its `Status` and its sections.
