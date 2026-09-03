# buy_agent

A shopping agent built on LangChain and a local model -- served by
[Ollama](https://ollama.com), or by a [vLLM](https://docs.vllm.ai) you already
run. Tell it what you want to buy; it searches the web, pulls out up to 10
products along with what the pages say about them, ranks them, and logs the
best 3.

![The search form, with its settings open](docs/ui.png)

Two runs of that page are recorded in `demo/`, fifteen seconds and twenty-two:
[`wwii-books-1944-45.mpg`](demo/wwii-books-1944-45.mpg), which ends on the top 3
with the rest folded away, and
[`laptops-under-1000.mpg`](demo/laptops-under-1000.mpg), which ends on the shop
page behind the top product's link. They are MPEG-1 in a program stream, which
no browser plays inline, so both links download. [The web UI](#the-web-ui) below
is what they show, written down.

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
18:13:24 INFO  buy_agent        |      says   : the noise cancelling is uncanny for the money
18:13:24 INFO  buy_agent        |      says   : the case is too bulky for a coat pocket
```

[Architecture](docs/architecture.md) draws the whole of it as C4 diagrams --
context, containers, the components inside the pipeline and inside the web tier,
and one streamed run end to end. [How it works](#how-it-works) below is the same
story in prose, and [docs/adr/](docs/adr/README.md) is why it is this way.

## Setup

Everything runs locally; no API keys, no accounts.

```powershell
# 1. Ollama, with a model pulled
ollama serve
ollama pull gemma4:12b

# 2. Python environment
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

Already running a vLLM? Skip step 1 and see
[Running against vLLM](#running-against-vllm) -- `--provider vllm` is the whole
difference.

To run the web UI without either toolchain, build the image instead: see
[Running in Docker](docs/docker.md). The model server still runs on the host. A
published release needs no build at all -- an archive with the UI already built
(unpack, `pip install -r requirements.txt`, run the server) and the same pair as
a container image on `ghcr.io`, both explained in that page and in
[ADR-0030](docs/adr/0030-publish-a-release-as-an-archive-and-an-image.md).

A pulled tag follows the registry, and `python -m scripts.update_ollama` re-pulls
the models Ollama has and reports which builds actually moved -- see
[Keeping the models current](docs/models.md).

## Usage

```powershell
python -m buy_agent "gaming laptop under 5000 PLN" --region pl-pl
python -m buy_agent "espresso machine" --model qwen2.5 --results 15 --top 5
python -m buy_agent "running shoes" --sort-by price --json results.json
python -m buy_agent "wireless earbuds" --source rtings.com --source @mkbhd
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--provider` | `ollama` (or `$BUY_AGENT_PROVIDER`) | `ollama` or `vllm` |
| `--model` | the provider's own | Ollama tag, or the name a vLLM was started with |
| `--base-url` | the provider's own | Where that server listens |
| `--results` | `10` | How many products to find (1-50) |
| `--top` | `3` | How many to log (1-50) |
| `--sort-by` | `score` | `score`, `price` or `rating` |
| `--region` | `us-en` | Search region: a country, then a language -- `uk-en`, `pl-pl` |
| `--source` | -- | Take the facts from this source only; repeatable |
| `--temperature` | `0.0` | Model temperature, 0-2; extraction is a copying task |
| `--num-ctx` | `8192` | Context window in tokens (Ollama only) |
| `--think` / `--no-think` | `--no-think` | Force thinking mode on or off |
| `--no-fetch` | off | Use search snippets only, without opening the result pages |
| `--json` | -- | Also write every result to a JSON file |
| `-v` | off | Debug logging |

The report goes to **stdout** and the progress to **stderr**, so a redirect keeps
the answer and leaves the narration on screen:

```powershell
python -m buy_agent "gaming laptop under $1500" > top.txt
```

The exit code says which kind of ending it was -- `0` found products, `1` failed
(the reason is the last line on stderr), `2` is a usage error, `3` is a run that
worked and found nothing, and `130` is Ctrl-C. Only the first is an answer, and
only the second is a bug worth chasing. `--json` is written either way, so a
script waiting on that file gets `[]` rather than yesterday's results.

As a library:

```python
from buy_agent import AgentConfig, BuyAgent

agent = BuyAgent(AgentConfig(model="gemma4:12b", top_n=3))
ranked = agent.run("noise cancelling headphones under $200")   # logs the top 3
print(ranked[0].product.name, ranked[0].score)                 # returns all of them
```

### Running against vLLM

Ollama is the default because it is the one you install in a minute on a laptop.
On a machine that already serves a model with [vLLM](https://docs.vllm.ai) -- a
shared GPU box, a lab server -- installing a second model server and pulling a
second copy of the weights is pure waste, so point the agent at the one that is
already running:

```powershell
python -m buy_agent "gaming laptop under $1500" --provider vllm
python -m buy_agent "espresso machine" --provider vllm --base-url http://gpu.lan:8000/v1
$env:BUY_AGENT_PROVIDER = 'vllm'      # ...or once, for every run in this shell
```

`--provider` on its own is a complete choice: `--model` and `--base-url` default
to the pair belonging to whichever provider was named, so nothing has to be
retyped to switch. Those defaults are `$VLLM_MODEL` and `$VLLM_HOST`
(`Qwen/Qwen3-8B`, `http://localhost:8000/v1` -- the port and `/v1` root `vllm
serve` gives you with no arguments), exactly as `$OLLAMA_MODEL` and
`$OLLAMA_HOST` are Ollama's.

Everything else is the same run: both servers constrain decoding to the JSON
schema, so extraction, grounding, quoting and ranking are unchanged, and
`buy_agent/providers.py` is the only module that knows which is answering. Three
differences are real, and none is hidden:

- **A vLLM serves one model, chosen when it started.** The Model dropdown has one
  entry, and asking for a name it does not have is answered with what it *is*
  serving and how to restart it -- there is nothing to pull.
- **`--num-ctx` is Ollama's.** vLLM fixes its window with `--max-model-len` at
  startup, so the flag is not sent there and the form disables the field rather
  than taking a number it would ignore. `--think` / `--no-think` works on both:
  it becomes `enable_thinking`, which is what the chat templates of the thinking
  models vLLM serves read.
- **A key, if there is one.** A vLLM started with `--api-key` wants it back;
  `$env:VLLM_API_KEY` is how, and deliberately the only how -- no flag, so it
  stays out of your shell history and out of what the web API hands the browser.

[ADR-0028](docs/adr/0028-serve-the-model-from-ollama-or-vllm.md) has why this is
one seam rather than two code paths, and why it does not reopen the
no-accounts-no-keys decision in
[ADR-0003](docs/adr/0003-local-ollama-no-api-keys.md): a vLLM on your own machine
or network is inside that decision, not an exception to it.

### Thinking models

The default is one, so the two settings a thinking model needs are the defaults
too: thinking off, and an 8192-token window. Left to itself such a model fails --
the extraction prompt runs to roughly 4.3k tokens, so inside Ollama's own 4096
the model spends what is left thinking, is cut off before it writes any JSON, and
the run ends with `Invalid json output:` and nothing after the colon. The wider
window is also what gets you the full ten products rather than five.

So `--no-think` and `--num-ctx 8192` are no longer worth typing: `qwen3.5`,
`gemma4`, `lfm2.5`, anything listing the `thinking` capability, is already
covered, and a model that cannot think ignores both. Only a model you
specifically want to hear reasoning from wants the flags back:

```powershell
python -m buy_agent "wireless headphones under $200" --model qwen3.5:9b --think
```

### Sources you trust

By default the facts come from whatever ten pages the search returned, which for
most shopping queries means affiliate roundups. `--source` says where they should
come from instead -- a review site, a section of one, or a YouTube channel by its
handle -- and the search then goes to those and nowhere else:

```powershell
python -m buy_agent "wireless earbuds under $150" --source rtings.com
python -m buy_agent "gaming laptop" --source @mkbhd --source notebookcheck.net
python -m buy_agent "espresso machine" --source https://www.seriouseats.com/coffee
```

Because the pages a run reads are the pages every figure and quote is checked
against, narrowing them narrows the report: everything in it was printed by a page
you named. Nothing falls back to the wider web, so naming sources with nothing to
say about the request is a run that finds nothing -- which is the answer, and the
report says so rather than quietly going elsewhere.

Each source is searched separately (`site:` takes one domain at a time), and the
number of pages read stays what `--results` asked for rather than multiplying by
the sources. What is enforced is the **domain**; a handle or a section narrows the
search but cannot be, a video's address saying which video it is and not who
published it -- see
[ADR-0027](docs/adr/0027-let-the-shopper-name-the-sources.md) for why that is the
strongest rule the URLs support.

The web UI has the same setting, as **Trusted sources** under Settings: one
field, separated by spaces or commas.

## The web UI

The same agent, with a page in front of it. `buy_agent.server` serves a small
JSON API and the built Angular app in `ui/`. Three ways to run it: the script
below, the same three steps by hand, or [the container](docs/docker.md), which
needs neither toolchain.

### Starting it on localhost

Two things run and one gets built: Ollama with a model pulled, the Angular build,
and the server that serves that build alongside the API. `scripts/start.ps1` does
all three and takes no arguments:

```powershell
.\scripts\start.ps1
```

It creates `.venv`, installs `requirements.txt`, starts Ollama, pulls the default
model and builds `ui/` where each is not already done, then runs the server in the
foreground and opens the page -- so a second run is a few seconds. Ctrl+C stops
the server, and the Ollama too if the script started it. It has no options on
purpose: the provider, model and address are `$env:BUY_AGENT_PROVIDER`,
`$env:OLLAMA_MODEL`/`$env:OLLAMA_HOST` (or `$env:VLLM_MODEL`/`$env:VLLM_HOST`) as
everywhere else, and anything past that is a flag on the server itself.

Ollama is the only model server it starts for you: with `$env:BUY_AGENT_PROVIDER`
set to `vllm` it waits for one to answer and says where instead of launching it,
a vLLM wanting a GPU, a served model and flags this script has no business
choosing. Node is the one thing it will not install -- without `npm` on PATH it
says so and serves the API anyway, so the page is the 503 until a build exists. A
PowerShell that refuses unsigned scripts takes the same file the long way round:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

By hand, the same three things:

```powershell
# 1. Ollama, in its own terminal -- skip it if Ollama already runs as a service
ollama serve
ollama pull gemma4:12b             # once; pulled tags are what the Model dropdown lists

# 2. The UI, built once -- and again after any change under ui/src
cd ui
npm install
npm run build                      # writes ui/dist/ui/browser
cd ..

# 3. The server, in a second terminal
.venv\Scripts\Activate.ps1
python -m buy_agent.server         # http://127.0.0.1:8000
```

Then open <http://127.0.0.1:8000> and search. Skip step 2 and the API still
answers, but the page is a 503 telling you to build it; `--ui-dir` points at a
build kept elsewhere, and `--host` / `--port` move the binding, which is loopback
on port 8000 by default. Port 8000 is also where a vLLM started with no arguments
listens, so on a machine serving one give the UI another port (`--port 8001`) --
otherwise the bind fails, and the server says so and names the clash. The model
server need not be local either: `$OLLAMA_HOST` and `$VLLM_HOST`, or the address
field under Settings, point the run at another machine. To work on the UI itself,
run the Angular dev server rather than rebuilding for every change -- see
[The dev server](#the-dev-server).

The two recordings at the top of this page are of this page. Everything in them
between the search and the ranking is the real pipeline -- only DuckDuckGo, the
page fetches and the model are scripted stand-ins -- so the progress panel is
showing grounding actually throwing figures, quotes and links away.
[demo/README.md](demo/README.md) says what is real in them, what is not, and how
to record them -- and the picture -- again.

The page takes the same settings the CLI takes as flags, shows the agent's log
lines as the run happens, and lists the ranked products with a link to the page
each was found on. It binds loopback by default: it drives a model on your own
machine, and is not meant to be exposed.

Loopback keeps it off the network but not out of the browser, where any page you
have open can send requests to `127.0.0.1`. So the server answers its own page and
nothing else: a request a page on another site made is refused, as is one
addressed to a name that merely resolves here -- a rebinding attack, from this
end. Clients that are not browsers send none of the headers that decide this, so
the `curl` below still works, and reaching the server by another name -- a
container published on a LAN -- means naming it with `--allowed-host buy.lan`. See
[ADR-0018](docs/adr/0018-guard-the-loopback-server-against-other-pages.md).

Under Settings, **Model server** picks between Ollama and vLLM and brings that
one's model and address with it, and **Model** is a dropdown of what that server
is actually serving -- what `ollama list` prints, or the one entry a vLLM reports
at `/v1/models` -- refetched when the address field is pointed elsewhere. Three
cases are marked rather than hidden: a model configured but not served stays in
the list as `not served`, so a stale setting is visible rather than silently
swapped; one that *is* pulled but cannot answer a prompt (`nomic-embed-text` and
the other embedding models, which `ollama list` prints exactly like a chat model)
is `embedding only` rather than a choice that costs a whole run to find out about
([ADR-0032](docs/adr/0032-say-which-models-can-answer-a-prompt.md)); and a server
that answered with nothing turns the field back into a text box. The pill in the
header names whichever server the list came from, so a page pointed at a vLLM
never reports an Ollama being down.

A setting the server would refuse is refused on the page instead, before a run
starts: the ranges come down with the defaults, so **Products to find** of 51
marks the field and greys out the button rather than opening a stream to be told
a minute later, and a Trusted sources field is read by the same `parse_sources`
the CLI uses. What the page cannot judge for itself it still shows in the right
place -- a run refused for one value comes back naming the field, and that box is
marked along with the banner
([ADR-0033](docs/adr/0033-let-the-form-refuse-what-the-server-would.md)).

When a run ends badly the Progress panel offers **Download log**: the lines it was
showing plus the error that ended the run. The panel scrolls and the next search
clears it, so without this a failure worth reporting is gone as soon as it is
retried. A finished run has two controls of its own: **Rank by** posts the
products back to `POST /api/rank`, which calls the same `rank_products` a run ends
with and nothing else, so the ordering is still Python's and only the minute is
skipped; **Download results** saves the answer the server sent, which is the same
document `--json` writes
([ADR-0035](docs/adr/0035-re-sort-a-finished-run-without-running-it-again.md)).

A search takes tens of seconds, so the browser does not wait on one response.
`GET /api/search/stream` runs the search and relays the agent's own log lines as
Server-Sent Events, finishing on a `result` or a `failure`. `POST /api/search`
does the same run in one JSON response, which is the shape a script wants:

```powershell
curl -X POST http://127.0.0.1:8000/api/search `
  -H "Content-Type: application/json" `
  -d '{"request": "espresso machine", "top": 5, "sort_by": "price"}'
```

| Endpoint | Answers with |
| --- | --- |
| `GET /api/config` | The form's defaults -- the same ones `--help` prints |
| `GET /api/models` | What a named server is serving, or why it could not be asked |
| `GET /api/sources` | Whether a Trusted sources field names sites, and what is wrong if not |
| `POST /api/search` | One run, as JSON |
| `POST /api/rank` | A finished run's products in another order |
| `GET /api/search/stream` | One run, as an event stream |

### The dev server

Working on the UI itself is nicer through the Angular dev server, which rebuilds
on save and proxies `/api` to the Python one:

```powershell
python -m buy_agent.server         # in one terminal
cd ui; npm start                   # in another -- http://localhost:4200
```

See `ui/README.md` for how the app is put together, and
[the web tier's components](docs/architecture.md#level-3----components-of-the-web-tier)
for how it sits behind the API.

## How it works

The C4 diagrams in [docs/architecture.md](docs/architecture.md) draw the same
thing a zoom level at a time -- Mermaid, so GitHub renders them in place:
[system context](docs/architecture.md#level-1----system-context),
[containers](docs/architecture.md#level-2----containers),
[the pipeline's components](docs/architecture.md#level-3----components-of-the-agent-pipeline),
[the web tier's](docs/architecture.md#level-3----components-of-the-web-tier), and
[a streamed run end to end](docs/architecture.md#a-streamed-run-end-to-end).

```
request ──▶ [LLM] refine into a search query
                      │
                      ▼
            DuckDuckGo text search (10 results)
              -- or one search per trusted source, pooled
                      │
                      ▼
     fetch each page, keep the lines quoting a figure or an opinion
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

Seven details make it work with a small model:

- **Structured output.** Both LLM calls use `json_schema` mode -- Ollama's, or
  vLLM's on the OpenAI-compatible side -- so decoding is constrained to the schema
  and cannot drift into prose.
- **Sentinels instead of nulls.** The extraction schema asks for `-1` rather than
  `null` for an unknown price (`buy_agent/models.py`): a required `number` makes
  it structurally impossible to answer `"N/A"` and fail validation for the whole
  batch. `ExtractedProduct.to_product()` turns the sentinels back into `None`.
- **Reading the pages, not the snippets.** A DuckDuckGo snippet for "headphones
  under $200" contains exactly one number: the $200 from the query. So each
  result page is fetched and condensed (`buy_agent/fetch.py`), which keeps the
  prompt small and gives the model something real to read. `--no-fetch` reverts
  to snippets only.
- **Reading the opinions too, not only the figures.** A page is swept twice: for
  the lines quoting a price or a rating, and for the lines passing judgement --
  "reviewers found", "the downside is", "disappointing". Each sweep has its own
  budget, so a shop page listing forty prices still contributes a verdict and a
  review page of prose still contributes its price. A price says what a thing
  costs and only these lines say whether to want it (ADR-0024).
- **Sources you can name.** `--source rtings.com --source @mkbhd` searches those
  instead of the whole web, and since the pages a run reads are the pages every
  fact is checked against, that makes provenance a property of the pipeline
  rather than a promise (ADR-0027).
- **Grounding.** Models fill gaps -- inventing a price, or lifting a product
  straight out of the prompt's own example. `buy_agent/verification.py` drops any
  product whose name is absent from the sources, and blanks any price, rating or
  review count that does not appear in the text the model was shown. A blanked
  figure scores neutral instead of winning.
- **Quotes, checked as quotes.** The opinions in the report are the source pages'
  words, not the model's summary of them, and each is looked for in the sources
  as running text -- overlapping runs of five consecutive words, most of which
  have to be found. A paraphrase fails that and is dropped: an invented price is
  a number nobody wrote, but an invented quote is words in a reviewer's mouth.

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
python -m pytest              # the Python suite
cd ui; npm test               # the UI's own tests, in jsdom
python -m pytest integration  # ...and against a real model, if one is pulled

python -m benchmark --scripted perfect   # score the pipeline, no model needed
python -m benchmark                      # ...and score whatever is serving
```

Neither of the first two touches the network or a model server, both run on
Windows and on Linux, and both are measured against a coverage floor CI enforces.
The third, in `integration/`, is the deliberate exception: it runs the pipeline
against a real Ollama on a model small enough for a CPU, which is the only place
the claims about JSON-schema decoding and Ollama's transport errors are actually
put to Ollama. It lives outside `testpaths`, so `python -m pytest` cannot reach
it, and a nightly job capped at five minutes is what runs it (ADR-0026). vLLM is
not in that job -- it needs a GPU, and a CPU runner cannot host one honestly -- so
its half is asserted in `tests/test_providers.py` and named as a gap in ADR-0028.

Those tests ask whether the pipeline's promises held, which they do however badly
the model read the pages -- so none of them can say whether a change made things
better. `benchmark/` is the other half: ten fixed pages, an answer key recording
what each prints for each product, and a scorer turning a run into eight shares
in `[0, 1]` -- products found, products real, figures right, figures
misattributed, links, quotes, quotes faithful, ranking order (ADR-0036). Only the
model varies, so two scores a month apart are comparable; the nightly run is
scored as well as checked, and `--scripted perfect` puts a hand-written answer
through the whole real pipeline with no model at all and must come out at 1.000.

What the counts are, what `tests/test_conventions.py` checks that coverage
cannot, what the benchmark measures, and the mutation run that grades the suite
every Saturday are in [Tests](docs/testing.md).

## Limitations

- **A figure can be real but attached to the wrong product.** Grounding checks
  that a number appears in the sources, not that it belongs to the product it was
  filed under, and small models sometimes give two products the same review
  count. Read the top 3 as candidates worth clicking rather than as a price
  quote. This is the one limitation here that is measured rather than only
  described: it is the benchmark's `attribution` metric.
- **A quote is tied to a page, not to a product on it.** A quoted opinion has to
  appear on a page that names the product (ADR-0025), so a verdict cannot move
  between pages about unrelated things -- but a review page covering eight
  headphones names all eight, and nothing stops a verdict moving between them.
- **A named source is a domain, not an author.** `--source @mkbhd` searches
  YouTube for that handle and keeps the YouTube pages that come back; a video by
  somebody else that mentions the handle can get through. The report links to the
  page, so whose it is can be seen.
- **Names are only as specific as the model makes them.** `lfm2.5` reported
  "Bose ANC" for a product the page named in full.
- Some shops answer with JavaScript-rendered pages or a 403; those results fall
  back to their snippet rather than failing the run. Which is why the run says
  how the fetching went -- "Got usable page text from 0 of 10 result(s): 7 refused
  (403), 2 timed out" -- on the CLI and in the browser alike: grounding blanks
  every figure the pages did not back, so a report of "price unknown" throughout
  is either a bad model or nothing having been read, and that line is which.
- DuckDuckGo rate-limits heavy use; the agent reports this as a `SearchError`.
- Only `lfm2.5` (1.2B) has been measured end to end for *speed*: it works, takes
  ~75s, and most of that is extraction. The failure modes above are the ones a
  small model shows, so a larger model should improve on them -- `python -m
  benchmark --model <tag>` is how to find out rather than assume, but no model
  larger than the nightly's `qwen3:0.6b` has been scored yet.
