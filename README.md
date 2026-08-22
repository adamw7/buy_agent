# buy_agent

A shopping agent built on LangChain and a local [Ollama](https://ollama.com) model.
Tell it what you want to buy; it searches the web, pulls out up to 10 products,
ranks them, and logs the best 3.

```
$ python -m buy_agent "wireless noise cancelling headphones under $200"

18:12:17 INFO  buy_agent.agent  | Refined search query: wireless noise cancelling headphones under $200 price review
18:12:19 INFO  buy_agent.search | Search returned 10 results
18:12:19 INFO  buy_agent.fetch  | Fetching 10 result page(s)
18:12:20 INFO  buy_agent.fetch  | Got usable page text from 10 of 10 result(s)
18:13:24 INFO  buy_agent.agent  | Extracted 9 candidate(s)
18:13:24 INFO  buy_agent.verif. | Dropped unsupported figures on 4 product(s)
18:13:24 INFO  buy_agent        | ==============================================================
18:13:24 INFO  buy_agent        | TOP 3 OF 9 PRODUCTS
18:13:24 INFO  buy_agent        | ==============================================================
18:13:24 INFO  buy_agent        | #1  Bose ANC
18:13:24 INFO  buy_agent        |      score  : 0.967
18:13:24 INFO  buy_agent        |      price  : 152.00
18:13:24 INFO  buy_agent        |      rating : 4.7/5 (5,874 reviews)
18:13:24 INFO  buy_agent        |      url    : https://...
```

## Setup

Everything runs locally; no API keys, no accounts.

```powershell
# 1. Ollama, with a model pulled
ollama serve
ollama pull llama3.2

# 2. Python environment
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

## Usage

```powershell
python -m buy_agent "gaming laptop under 5000 PLN" --region pl-pl
python -m buy_agent "espresso machine" --model qwen2.5 --results 15 --top 5
python -m buy_agent "running shoes" --sort-by price --json results.json
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--model` | `llama3.2` (or `$OLLAMA_MODEL`) | Ollama model tag |
| `--base-url` | `http://localhost:11434` (or `$OLLAMA_HOST`) | Ollama server |
| `--results` | `10` | How many products to find |
| `--top` | `3` | How many to log |
| `--sort-by` | `score` | `score`, `price` or `rating` |
| `--region` | `us-en` | Search region, e.g. `uk-en`, `pl-pl` |
| `--num-ctx` | Ollama's own (usually 4096) | Context window in tokens |
| `--think` / `--no-think` | leave the model alone | Force thinking mode on or off |
| `--no-fetch` | off | Use search snippets only, without opening the result pages |
| `--json` | -- | Also write every result to a JSON file |
| `-v` | off | Debug logging |

### Thinking models

A thinking model (`qwen3.5`, `gemma4`, `lfm2.5` -- anything listing the `thinking`
capability) needs `--no-think`, and is worth giving `--num-ctx 8192` as well:

```powershell
python -m buy_agent "wireless headphones under $200" --model qwen3.5:9b --no-think --num-ctx 8192
```

Left to itself it fails. The extraction prompt runs to roughly 3.3k tokens, so
inside Ollama's default 4096-token window the model spends what is left thinking,
gets cut off before it writes any JSON, and the run ends with `Invalid json
output:` and nothing after the colon. The wider window is what gets you the full
ten products rather than five.

As a library:

```python
from buy_agent import AgentConfig, BuyAgent

agent = BuyAgent(AgentConfig(model="llama3.2", top_n=3))
ranked = agent.run("noise cancelling headphones under $200")   # logs the top 3
print(ranked[0].product.name, ranked[0].score)                 # returns all of them
```

## How it works

```
request ──▶ [LLM] refine into a search query
                      │
                      ▼
            DuckDuckGo text search (10 results)
                      │
                      ▼
        fetch each page, keep the lines quoting a price or rating
                      │
                      ▼
        [LLM] extract structured products from that text
                      │
                      ▼
   clean names ▶ ground against sources ▶ merge duplicates ▶ rank ▶ log top 3
```

The control flow is fixed rather than left to the model to drive with tools. The
LLM does the two things it is good at -- rewording a request and reading facts out
of prose -- and ordinary Python does the rest. Small local models are unreliable
at running a tool loop, but perfectly capable of these two steps.

Four details make it work with a small model:

- **Structured output.** Both LLM calls use Ollama's `json_schema` mode, so the
  model's decoding is constrained to the schema and cannot drift into prose.
- **Sentinels instead of nulls.** The extraction schema asks for `-1` rather than
  `null` for an unknown price (`buy_agent/models.py`). A required `number` makes
  it structurally impossible to answer `"N/A"` and fail validation for the whole
  batch. `ExtractedProduct.to_product()` turns the sentinels back into `None`.
- **Reading the pages, not the snippets.** A DuckDuckGo snippet for "headphones
  under $200" contains exactly one number: the $200 from the query. Extracting
  from snippets alone produced ten products with no prices at all. So each result
  page is fetched and condensed to the lines that quote a price or a rating
  (`buy_agent/fetch.py`), which keeps the prompt small and gives the model
  something real to read. `--no-fetch` reverts to snippets only.
- **Grounding.** Models fill gaps -- inventing a price, or lifting a product
  straight out of the prompt's own example. `buy_agent/verification.py` drops any
  product whose name is absent from the sources, and blanks any price, rating or
  review count that does not appear in the text the model was shown. A blanked
  figure scores neutral instead of winning.

### Ranking

`rank_products` scores each product in `[0, 1]`:

| Criterion | Weight | Notes |
| --- | --- | --- |
| Rating | 0.5 | `rating / 5` |
| Popularity | 0.2 | `log10(reviews)`, saturating at 1,000 reviews |
| Price | 0.3 | Relative to the other candidates: cheapest 1.0, dearest 0.0 |

A missing criterion scores 0.5 rather than 0, so a listing that simply did not
publish a rating is not buried beneath one that published a bad one. Adjust the
mix through `AgentConfig(weights=RankingWeights(rating=0.7, price=0.3, ...))`.

## Tests

```powershell
python -m pytest              # whole suite
python -m pytest tests/test_ranking.py::test_cheaper_wins_when_rating_is_equal
```

117 tests, about 0.2s. Nothing in the suite touches the network or Ollama: the
model is faked through the `llm=` argument of `BuyAgent`, and both the search
backend and the page fetcher are monkeypatched.

## Limitations

- **A figure can be real but attached to the wrong product.** Grounding checks
  that a number appears in the sources, not that it belongs to the product it
  was filed under. Small models sometimes give two products the same review
  count. Reading the top 3 as candidates worth clicking, rather than as a price
  quote, is the right level of trust.
- **Names are only as specific as the model makes them.** `lfm2.5` reported
  "Bose ANC" for a product the page named in full.
- Some shops answer with JavaScript-rendered pages or a 403; those results fall
  back to their snippet rather than failing the run.
- DuckDuckGo rate-limits heavy use; the agent reports this as a `SearchError`.
- Only `lfm2.5` (1.2B) has been measured: it works, takes ~75s end to end, and
  most of that is extraction. The failure modes above are the ones a small model
  shows, so a larger model should improve on them, but that is an expectation
  rather than something benchmarked here.
