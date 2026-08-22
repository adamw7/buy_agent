# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A shopping agent: it takes a plain-language request ("wireless headphones under
$200"), searches the web, extracts up to 10 products, ranks them, and logs the
top 3. Built on LangChain with a local Ollama model. See `README.md` for usage.

## Commands

Dependencies live in a `.venv` created with stdlib `venv`; there is no
`pyproject.toml` and no packaging step. Run everything from the repository root.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt          # runtime deps: requirements.txt

python -m pytest                              # whole suite (~0.2s)
python -m pytest tests/test_ranking.py        # one file
python -m pytest tests/test_ranking.py::test_cheaper_wins_when_rating_is_equal
python -m pytest -k verification              # by name

python -m buy_agent "gaming laptop under $1500"          # run the agent
python -m buy_agent "espresso machine" --model lfm2.5 -v
```

There is no linter. CI (`.github/workflows/ci.yml`) installs
`requirements-dev.txt` and runs `python -m pytest` on Python 3.11, 3.12 and 3.13
for pushes to `main` and for every pull request. `pytest.ini` sets
`pythonpath = .`, which is why the package imports without being installed.

## Architecture

The pipeline is deliberately **not** a tool-calling agent loop. The LLM is used
for the two steps it is reliable at, and ordinary Python does everything else,
because Ollama is typically run with small models that drive tool loops badly.

```
request -> refine query (LLM) -> DuckDuckGo -> fetch + condense pages
        -> extract products (LLM) -> clean_products -> ground -> deduplicate
        -> rank -> log top 3
```

That order is load-bearing in both joints. `clean_products` runs before `ground`
so a name still wearing its publisher suffix ("... Review | AudioSite") is not
failed by the coverage check for tokens the page never had to contain; `ground`
runs before `deduplicate` so `_combine` only ever merges figures the sources
back.

| Module | Responsibility |
| --- | --- |
| `agent.py` | `BuyAgent.run()` -- orchestrates the pipeline, translates Ollama errors |
| `extraction.py` | Both prompts, both chains, name cleaning, deduplication |
| `fetch.py` | Fetches result pages, keeps the lines quoting a price or rating |
| `verification.py` | Drops products and figures absent from the sources |
| `ranking.py` | Scoring and sorting; no LLM involved |
| `models.py` | `ExtractedProduct` (LLM-facing) vs `Product` (domain) |
| `search.py` | DuckDuckGo wrapper plus a LangChain `@tool` version |
| `config.py`, `logging_setup.py`, `__main__.py` | Config, the report, the CLI |

Four conventions matter when changing this code:

- **`ExtractedProduct` uses sentinels, `Product` uses `None`.** The LLM-facing
  schema asks for `-1`/`""` rather than nullable fields: Ollama compiles the JSON
  schema into a decoding grammar, and a required `number` makes `"N/A"` -- which
  would fail validation for the entire batch -- structurally impossible. Keep new
  extraction fields non-nullable with a sentinel, and convert in `to_product()`.
- **Never rank on an unverified number.** `verification.ground()` drops products
  whose name is absent from the sources and blanks any price, rating or review
  count that is. Ratings need context ("4.3/5", "rated 4.3") because a bare `5`
  matches the "5" in "out of 5". Extraction and verification must be given the
  same text, or the check rejects everything -- this is why `fetch.enrich()` puts
  page content on `SearchResult` rather than passing it around separately.
- **`GENERIC_WORDS` is shared, and edits to it pull in two directions.**
  `verification.py` imports the set from `extraction.py`. Adding a word makes
  `merge_variants` fold *more* names into one product, and at the same time makes
  `mentions_name` stricter -- ignored words leave fewer distinctive tokens to
  clear the 0.6 coverage bar. Only ever add words that identify nothing
  ("wireless", "black"); a brand or a model number there would let an invented
  product pass grounding.
- **Model output is never trusted as judgement.** The model reports article
  headlines as products; `clean_products` filters them. Anything that decides the
  answer -- filtering, scoring, ordering -- belongs in Python, where it is testable.

`BuyAgent.run()` raises exactly three things -- `ValueError`,
`OllamaUnavailableError`, `SearchError` -- and `__main__.main()` catches exactly
those, logging them and returning 1 (130 on Ctrl-C). A new failure mode needs
handling in both places or it reaches the user as a traceback. Within the agent
only query refinement is recoverable: it falls back to the raw request, but lets
`OllamaUnavailableError` through rather than searching with a model that is not
there.

## Tests

`BuyAgent(config, llm=...)` is the injection seam: `tests/conftest.py` provides a
`FakeLLM` exposing only `with_structured_output`. The network is monkeypatched
in three places: `buy_agent.agent.search_web` and `buy_agent.agent.enrich` for
pipeline tests, and `buy_agent.search.DDGS` / `buy_agent.fetch.httpx.Client` for
the wrappers' own tests. `ollama.Client` is patched too, for the one path that
lists the installed models to name them in an error. Patching `DDGS.text` does
*not* work -- the name `ddgs` exports is a wrapper that constructs a different
class. No test touches the network or Ollama; keep it that way. 268 tests run in
about 0.4s, so a run that suddenly takes seconds means something is reaching out.

## Environment

Development happens on Windows with PowerShell as the default shell; prefer PowerShell syntax for terminal commands, or use the Bash tool explicitly for POSIX scripts.
