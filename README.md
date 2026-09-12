# buy_agent

A shopping agent built on a local model -- served by
[Ollama](https://ollama.com), or by a [vLLM](https://docs.vllm.ai) you already
run. Tell it what you want to buy; it searches the web, pulls out up to 10
products along with what the pages say about them, ranks them, and logs the
best 3.

![The search form, with its settings open](docs/ui.png)

Three runs of that page are recorded in `demo/`:
[`wwii-books-1944-45.mpg`](demo/wwii-books-1944-45.mpg), fifteen seconds ending
on the top 3 with the rest folded away;
[`wwii-books-1944-45-with-sound.mpg`](demo/wwii-books-1944-45-with-sound.mpg),
fourteen seconds of the same run with a soundtrack, in which the six log lines
that are the pipeline catching the model out each get a note of their own; and
[`laptops-under-1000.mpg`](demo/laptops-under-1000.mpg), twenty-two seconds
ending on the shop page behind the top product's link. All three are MPEG in a
program stream, which no browser plays inline, so every link downloads.
[The web UI](#the-web-ui) below is what they show, written down.

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

# 3. Optional: only if you want it to pay for things (see "Letting it buy")
pip install -r requirements-ap2-deps.txt
pip install --no-deps -r requirements-ap2.txt
```

`--no-deps` there is deliberate: the AP2 SDK's published metadata pins versions
this project does not use, so its real requirements are pinned in that file
instead. Everything except `--pay` works without it.

Already running a vLLM? Skip step 1 and see
[Running against vLLM](#running-against-vllm) -- `--provider vllm` is the whole
difference.

To run the web UI without either toolchain, build the image instead: see
[Running in Docker](docs/docker.md). The model server still runs on the host. A
published release needs no build at all -- an archive with the UI already built
(unpack, `pip install -r requirements.txt`, run the server) and the same pair as
a container image on `ghcr.io`, both explained in that page and in
[ADR-0030](docs/adr/0030-publish-a-release-as-an-archive-and-an-image.md).

A pulled tag follows the registry, and `python -m scripts.update_ollama`
re-pulls the models Ollama has and reports which builds actually moved -- see
[Keeping the models current](docs/models.md).

## Usage

```powershell
python -m buy_agent "gaming laptop under 5000 PLN" --region pl-pl
python -m buy_agent "espresso machine" --model qwen2.5 --results 15 --top 5
python -m buy_agent "running shoes" --sort-by price --json results.json
python -m buy_agent "wireless earbuds" --source rtings.com --source @mkbhd
python -m buy_agent "headphones" --max-price 200 --min-rating 4.5 --min-reviews 500
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
| `--max-price` | no limit | Report nothing dearer, in the currency the run's prices are counted in |
| `--min-rating` | no limit | Report nothing rated below this, out of 5 |
| `--min-reviews` | no limit | Report nothing whose rating averages fewer reviews |
| `--cache-ttl` | `86400` | Seconds a page, and the model's answer about it, stay usable on disk; `0` is off |
| `--temperature` | `0.0` | Model temperature, 0-2; extraction is a copying task |
| `--num-ctx` | `16384` | Context window in tokens (Ollama only) |
| `--model-timeout` | `600` | Seconds to wait for one answer; asked once, so this is the whole wait |
| `--think` / `--no-think` | `--no-think` | Force thinking mode on or off |
| `--no-fetch` | off | Use search snippets only, without opening the result pages |
| `--json` | -- | Also write every result to a JSON file |
| `-v` | off | Debug logging |

The report goes to **stdout** and the progress to **stderr**, so a redirect
keeps the answer and leaves the narration on screen:

```powershell
python -m buy_agent "gaming laptop under $1500" > top.txt
```

The exit code says which kind of ending it was -- `0` found products, `1` failed
(the reason is the last line on stderr), `2` is a usage error, `3` is a run that
worked and found nothing, `4` is a run that was asked to pay and did not, and
`130` is Ctrl-C. Only the first is an answer, and
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

- **A vLLM serves one model, chosen when it started.** The Model dropdown has
  one entry, and asking for a name it does not have is answered with what it
  *is* serving and how to restart it -- there is nothing to pull.
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
[ADR-0003](docs/adr/0003-local-ollama-no-api-keys.md): a vLLM on your own
machine or network is inside that decision, not an exception to it.

### Thinking models

The default is one, so the two settings a thinking model needs are the defaults
too: thinking off, and a 16384-token window. Left to itself such a model fails
-- the extraction prompt runs to roughly 4.3k tokens, so inside Ollama's own
4096 the model spends what is left thinking, is cut off before it writes any
JSON, and the run ends with `Invalid json output:` and nothing after the colon.
The wider window is also what gets you the full ten products rather than five:
the prompt is only half of what has to fit, the JSON describing ten products
with what was said about each being the other half (ADR-0050).

So `--no-think` and `--num-ctx 16384` are no longer worth typing: `qwen3.5`,
`gemma4`, `lfm2.5`, anything listing the `thinking` capability, is already
covered, and a model that cannot think ignores both. Only a model you
specifically want to hear reasoning from wants the flags back:

```powershell
python -m buy_agent "wireless headphones under $200" --model qwen3.5:9b --think
```

### Sources you trust

By default the facts come from whatever ten pages the search returned, which for
most shopping queries means affiliate roundups. `--source` says where they
should come from instead -- a review site, a section of one, or a YouTube
channel by its handle -- and the search then goes to those and nowhere else:

```powershell
python -m buy_agent "wireless earbuds under $150" --source rtings.com
python -m buy_agent "gaming laptop" --source @mkbhd --source notebookcheck.net
python -m buy_agent "espresso machine" --source https://www.seriouseats.com/coffee
```

Because the pages a run reads are the pages every figure and quote is checked
against, narrowing them narrows the report: everything in it was printed by a
page you named. Nothing falls back to the wider web, so naming sources with
nothing to say about the request is a run that finds nothing -- which is the
answer, and the report says so rather than quietly going elsewhere.

Each source is searched separately (`site:` takes one domain at a time), and the
number of pages read stays what `--results` asked for rather than multiplying by
the sources. What is enforced is the **domain**; a handle or a section narrows
the search but cannot be, a video's address saying which video it is and not who
published it -- see
[ADR-0027](docs/adr/0027-let-the-shopper-name-the-sources.md) for why that is
the strongest rule the URLs support.

The web UI has the same setting, as **Trusted sources** under Settings: one
field, separated by spaces or commas.

### Saying what you will actually buy

"under $200" in the request only ever shaped the *search query* -- a page comes
back for matching the words, not for obeying them -- so a run could top its
report with a $328 pair and look right doing it, price being scored relative to
whatever else came back. Three flags say it as a number instead, and Python
enforces them:

```powershell
python -m buy_agent "wireless headphones" --max-price 200
python -m buy_agent "espresso machine" --min-rating 4.5 --min-reviews 500
```

They are applied after the pages have been read and the duplicates merged, and
before the ranking -- so the "cheapest" in the report is the cheapest of what
you could actually buy, not of a list you were shown none of. A run that set any
of them says what they did, whether or not they did anything:

```
1 of 10 product(s) are within the limits (at most 200.00, rated at least 4.5)
```

**A product whose figure the run never learned is kept.** Grounding blanks every
figure the source pages did not back, so a blank price is as often a page that
did not print one as a product that costs too much. Dropping blanks would throw
away real products for the extractor's misses, which is the same reason a
missing figure scores neutral rather than zero
([ADR-0039](docs/adr/0039-enforce-the-shoppers-bounds-in-python.md)).

**Prices are compared inside one currency, and nothing is converted.** A run's
prices are counted in the commonest currency its pages printed, and a price in
any other is a figure the run cannot place: it passes the budget, it scores
neutral rather than being read as the number it happens to be, and the card
marks it "assumed". A price a page printed with no currency at all is taken as
the run's own. A rate table would be the first figure here that no source page
printed, and a stale rate is a wrong ranking wearing a right one's clothes
([ADR-0043](docs/adr/0043-compare-prices-only-within-one-currency.md)).

The bounds are not read out of the request by the model, deliberately: a model
that saw "under $200" in "headphones with 200 hours of battery" would drop every
product in the run, and the report would say only that nothing was found. The
number goes in the flag, or in the box under Settings in the browser.

### Letting it buy

Off by default, and off again unless you install one more thing. When it is on,
the agent can complete the purchase itself -- authorised by signed
[AP2](https://ap2-protocol.org) mandates rather than by a card number it holds.

```powershell
pip install -r requirements-ap2-deps.txt
pip install --no-deps -r requirements-ap2.txt
python -m buy_agent "wireless headphones under $200" --pay
```

That asks first:

```
  Pay 329.99 USD for Sony WH-1000XM5
    merchant  AudioSite
    page      https://audiosite.example/xm5
    rail      Dry run -- you will NOT be charged
  Type yes to authorise:
```

Two things about that prompt are the point. It restates the **cart** -- what the
mandates will actually carry -- rather than the request that found it. And a run
with no terminal to ask at is *refused* rather than assumed: a script that piped
in nothing would otherwise have bought something.

**The default rail charges nobody.** `--pay` on its own signs a real, verifiable
AP2 authorisation and stops there, which is what makes the switch safe to try.
Paying for real means naming somewhere to pay:

```powershell
python -m buy_agent "headphones" --pay --rail http --merchant-url https://pay.example
```

No merchant, wallet or processor is named anywhere in this project. The rail is
a row in a table (`buy_agent/rails.py`), the address is the whole of the
integration, and the endpoint is asked for a signed checkout at `{url}/checkout`
and presented the mandates at `{url}/payment`.

**Only a product the sources actually priced can be bought.** This is the
grounding rule turned around: a price no page printed is blanked before ranking,
so there is nothing to authorise; a price in a currency this run cannot place is
a number and not an amount. That is deliberately the opposite of what the bounds
above do with the same blank -- a filter that cannot judge a product keeps it,
because dropping it would punish the extractor's miss, and money has no such
luxury.

**`--spend-limit` is the ceiling**, read in the currency the run counts in:

```powershell
python -m buy_agent "headphones" --pay --spend-limit 250
```

None of those three do anything on their own, so a run given one without `--pay`
says which word is missing rather than spending its minute and then buying
nothing.

Signing needs a key. `$BUY_AGENT_AP2_KEY` points at an EC P-256 private key --
`openssl ecparam -genkey -name prime256v1 -noout -out agent-key.pem` -- and the
dry run will generate a throwaway one and say so rather than refusing, since it
has no counterparty to have trusted anything.

**Buying while you are not there** is AP2's other mode, and it needs a mandate
you signed in advance: `$BUY_AGENT_AP2_MANDATE` names a JSON file holding an
*open* mandate and the key it was issued under. Its constraints -- an amount
range, the merchants allowed, an expiry -- are checked before anything is sent,
by the same evaluator a credential provider would run, so a cart outside them is
refused here. There is no flag for this mode: the signed mandate *is* the
authorisation
([ADR-0046](docs/adr/0046-pay-on-the-shoppers-behalf-with-ap2.md)).

In the browser it is the same feature: tick **Pay for the top product** under
Settings before the run, and each card that can be bought grows a Pay button
that asks a second time before anything is signed.

### Running the same search twice is nearly free

Most of a repeated run is opening the ten pages it opened last time and then
asking the model the identical question about them. Both are kept on disk for a
day, so a second run of the same search costs almost nothing -- which is the
difference between a minute and a second or two when what you are actually
changing is a bound, a weight or the sort order, all of which happen *after* the
model has spoken. It also stops ten more requests going to shops that
rate-limit, and stops two runs of one search disagreeing because a shop answered
403 the second time.

```powershell
python -m buy_agent "headphones" --cache-ttl 0      # every page and answer fresh
$env:BUY_AGENT_CACHE_DIR = "D:\scratch\buy-agent" # somewhere else
```

What is stored is the page *text* rather than the condensed excerpt, so changing
`page_chars` re-condenses instead of replaying a stale excerpt, and a cached run
extracts from exactly what a fresh one would have -- which is what keeps the
cache invisible to grounding. Only pages that were actually read are stored: a
403 stays live, so a shop that has stopped refusing is noticed on the next run.
Every failure -- an unwritable directory, a corrupt entry, a full disk -- is a
cache miss and never a failed run
([ADR-0040](docs/adr/0040-cache-the-page-text-on-disk.md)).

It is bounded twice over. `--cache-ttl` is how long an entry stays usable, and
`cache.MAX_BYTES` is how much one kind of them may take up -- 256 MB per
directory, oldest first out, enforced when a run opens the cache. Age alone was no
bound on size: the TTL ceiling is thirty days, what is stored is the whole text of
a page rather than the excerpt, and nothing ever deleted an entry that had not
expired, so a month of shopping was a month of pages on a disk nobody was watching
([ADR-0052](docs/adr/0052-cap-the-cache-by-size-as-well-as-age.md)).

What the model answered is kept the same way, under a key holding the whole
question: the prompt with those pages in it, the schema, the model, the server
and the settings the request carries. So a reworded prompt, a widened page
budget or another model all ask again, and the only thing that comes off disk is
the same question put to the same server twice. A run at a `temperature` above 0
is never remembered at all -- a model asked to sample has no one answer, and
replaying one sample would be the cache deciding the result
([ADR-0044](docs/adr/0044-remember-a-deterministic-model-answer.md)).

The cost is honest and worth knowing: a day-old entry is a day-old price,
reported as current, and a day-old answer is that same figure one step further
from the source. `--cache-ttl 0` is the answer when the figures have to be live.

### What the pages say

Every product in the report carries up to three quotes: the `says` lines under
the top 3 on the CLI, the quoted lines under each card in the browser, the
`opinions` array in `--json` and in what the API answers with. They are the
source pages' own words, and they are there because a price says what a thing
costs and only these lines say whether to want it.

Having any means reading the pages, so each one is swept twice -- once for the
lines quoting a price or a rating, once for the lines passing judgement -- and
each sweep has a budget of its own, so a shop page listing forty prices still
contributes a verdict and a page of prose still contributes its price. What
counts as judgement is a vocabulary of who is speaking and what they concluded
("reviewers found", "the downside is", "disappointing"), never one of subject
matter: "wireless" or "battery" would take every line on a headphone page
([ADR-0024](docs/adr/0024-read-and-quote-what-the-sources-say.md)).

Each quote is then checked before it is shown, and against the pages that name
the product rather than against the run's pages pooled -- a verdict on the
kettle three results down is no evidence about these headphones. What is checked
is the quote as running text: overlapping runs of five consecutive words, most
of which have to be found. A small model paraphrasing what it read fails that,
and the quote is dropped
([ADR-0025](docs/adr/0025-check-a-quote-against-the-page-it-came-from.md)); a
product whose quotes all fail is still reported, with none. An invented price is
a number nobody wrote, but an invented quote is words in a reviewer's mouth.

**Each quote names the page that printed it** -- a `source` link beside it on
the card, the URL after the `says` line on the CLI where it is not the product's
own link, and a `url` beside the `text` in the JSON. The check above already has
to find that page to keep the quote, so it is kept rather than thrown away: a
figure can be checked by following the product's link, and without this a quote
could be checked by nobody. The tolerance for a word of the model's own at
either end is deliberate and stays; what changes is that a paraphrase which
slips through is now one click from being visible as one
([ADR-0042](docs/adr/0042-keep-the-page-a-quote-came-from.md)).

None of it is scored -- the ranking is the figures alone -- so the quotes are
what to read when two candidates come out within a hair of each other.
`--no-fetch` leaves nothing to quote, a search snippet passing judgement about
as rarely as it quotes a price, and `AgentConfig(opinion_chars=0)` reads the
pages but skips the second sweep, which like the budgets themselves is reachable
from Python and neither front end.

## The web UI

The same agent, with a page in front of it. `buy_agent.server` serves a small
JSON API and the built Angular app in `ui/`. Three ways to run it: the script
below, the same three steps by hand, or [the container](docs/docker.md), which
needs neither toolchain.

### Starting it on localhost

Two things run and one gets built: Ollama with a model pulled, the Angular
build, and the server that serves that build alongside the API.
`scripts/start.ps1` does all three and takes no arguments:

```powershell
.\scripts\start.ps1
```

It creates `.venv`, installs `requirements.txt`, starts Ollama, pulls the
default model and builds `ui/` where each is not already done, then runs the
server in the foreground and opens the page -- so a second run is a few seconds.
Ctrl+C stops the server, and the Ollama too if the script started it. It has no
options on purpose: the provider, model and address are
`$env:BUY_AGENT_PROVIDER`, `$env:OLLAMA_MODEL`/`$env:OLLAMA_HOST` (or
`$env:VLLM_MODEL`/`$env:VLLM_HOST`) as everywhere else, and anything past that
is a flag on the server itself.

Ollama is the only model server it starts for you: with
`$env:BUY_AGENT_PROVIDER` set to `vllm` it waits for one to answer and says
where instead of launching it, a vLLM wanting a GPU, a served model and flags
this script has no business choosing. Node is the one thing it will not install
-- without `npm` on PATH it says so and serves the API anyway, so the page is
the 503 until a build exists.

[Paying](#letting-it-buy) needs one more install, and the script does that one
only when asked -- where `$env:BUY_AGENT_RAIL`, `$env:BUY_AGENT_MERCHANT_URL`,
`$env:BUY_AGENT_AP2_KEY` or `$env:BUY_AGENT_AP2_MANDATE` is set, which is the
environment saying a payment is meant. Set none and it says so and carries on,
and the page offers no Buy button; the smallest way to ask for one is:

```powershell
$env:BUY_AGENT_RAIL = 'dry-run'      # the default rail, which charges nobody
.\scripts\start.ps1
```

A PowerShell that refuses unsigned scripts takes the same file the long way
round:

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
build kept elsewhere, and `--host` / `--port` move the binding, which is
loopback on port 8000 by default. Port 8000 is also where a vLLM started with no
arguments listens, so on a machine serving one give the UI another port (`--port
8001`) -- otherwise the bind fails, and the server says so and names the clash.
The model server need not be local either: `$OLLAMA_HOST` and `$VLLM_HOST`, or
the address field under Settings, point the run at another machine. To work on
the UI itself, run the Angular dev server rather than rebuilding for every
change -- see [The dev server](#the-dev-server).

The three recordings at the top of this page are of this page. Everything in
them between the search and the ranking is the real pipeline -- only DuckDuckGo,
the page fetches and the model are scripted stand-ins -- so the progress panel
is showing grounding actually throwing figures, quotes and links away. The one
with sound has none captured either: Chromium records no audio, so the track is
synthesised from a cue per thing that happened, which is how a dropped figure
can be heard as well as read. [demo/README.md](demo/README.md) says what is real
in them, what is not, and how to record them -- and the picture -- again.

The page takes the same settings the CLI takes as flags, shows the agent's log
lines as the run happens, and lists the ranked products with a link to the page
each was found on. It binds loopback by default: it drives a model on your own
machine, and is not meant to be exposed.

Loopback keeps it off the network but not out of the browser, where any page you
have open can send requests to `127.0.0.1`. So the server answers its own page
and nothing else: a request a page on another site made is refused, as is one
addressed to a name that merely resolves here -- a rebinding attack, from this
end. Clients that are not browsers send none of the headers that decide this, so
the `curl` below still works, and reaching the server by another name -- a
container published on a LAN -- means naming it with `--allowed-host buy.lan`.
See [ADR-0018](docs/adr/0018-guard-the-loopback-server-against-other-pages.md).

Under Settings, **Model server** picks between Ollama and vLLM and brings that
one's model and address with it, and **Model** is a dropdown of what that server
is actually serving -- what `ollama list` prints, or the one entry a vLLM
reports at `/v1/models` -- refetched when the address field is pointed
elsewhere. Three cases are marked rather than hidden. A model configured but not
served stays in the list as `not served`, so a stale setting is visible rather
than silently swapped. One that *is* pulled but cannot answer a prompt is
`embedding only` rather than a choice that costs a whole run to find out about:
`nomic-embed-text` and the other embedding models, which `ollama list` prints
exactly like a chat model
([ADR-0032](docs/adr/0032-say-which-models-can-answer-a-prompt.md)). And a
server that answered with nothing turns the field back into a text box. The pill
in the header names whichever server the list came from, so a page pointed at a
vLLM never reports an Ollama being down.

A setting the server would refuse is refused on the page instead, before a run
starts. The ranges come down with the defaults, so **Products to find** of 51
marks the field and greys out the button rather than opening a stream to be told
a minute later. A Trusted sources field is read by the same `parse_sources` the
CLI uses. What the page cannot judge for itself it still shows in the right
place -- a run refused for one value comes back naming the field, and that box
is marked along with the banner
([ADR-0033](docs/adr/0033-let-the-form-refuse-what-the-server-would.md)).

When a run ends badly -- or when you stop one yourself -- the Progress panel
offers **Download log**: the lines it was showing plus the error that ended the
run. The panel scrolls and the next search clears it, so without this a failure
worth reporting is gone as soon as it is retried, and the reason to stop a run
is usually that it had gone quiet for four minutes. A finished run has two
controls of its own: **Re-order these** posts the products back to `POST
/api/rank`, which calls the same `rank_products` a run ends with and nothing
else, so the ordering is still Python's and only the minute is skipped;
**Download results** saves the answer the server sent, which is the same
document `--json` writes
([ADR-0035](docs/adr/0035-re-sort-a-finished-run-without-running-it-again.md)).
It is deliberately not called *Rank by*, which is what the settings call the
criterion the **next** run is ranked by: the two are different questions, and
one label over both read as a single setting perpetually out of step with
itself.

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
| `POST /api/pay` | One of those products bought, given the approval the page witnessed |
| `GET /api/search/stream` | One run, as an event stream |

### The dev server

Working on the UI itself is nicer through the Angular dev server, which rebuilds
on save and proxies `/api` to the Python one:

```powershell
python -m buy_agent.server         # in one terminal
cd ui; npm start                   # in another -- http://localhost:4200
```

See `ui/README.md` for how the app is put together, and [the web tier's
components](docs/architecture.md#level-3----components-of-the-web-tier) for how
it sits behind the API.

## How it works

The C4 diagrams in [docs/architecture.md](docs/architecture.md) draw the same
thing a zoom level at a time -- Mermaid, so GitHub renders them in place:
[system context](docs/architecture.md#level-1----system-context),
[containers](docs/architecture.md#level-2----containers), [the pipeline's
components](docs/architecture.md#level-3----components-of-the-agent-pipeline),
[the web tier's](docs/architecture.md#level-3----components-of-the-web-tier),
and [a streamed run end to end](docs/architecture.md#a-streamed-run-end-to-end).

```
request ──▶ [LLM] refine into a search query
                      │
                      ▼
            DuckDuckGo text search (10 results)
              -- or one search per trusted source, pooled
                      │
                      ▼
     fetch each page (or read yesterday's), keep the lines
     quoting a figure or an opinion
                      │
                      ▼
        [LLM] extract structured products from that text
                      │
                      ▼
   clean names ▶ ground against sources ▶ merge duplicates
                      │
                      ▼
   hold to the bounds you set ▶ rank ▶ log top 3
```

The control flow is fixed rather than left to the model to drive with tools. The
LLM does the two things it is good at -- rewording a request and reading facts
out of prose -- and ordinary Python does the rest. Small local models are
unreliable at running a tool loop, but perfectly capable of these two steps.

Nine details make it work with a small model:

- **Structured output.** Both LLM calls use `json_schema` mode -- Ollama's, or
  vLLM's on the OpenAI-compatible side -- so decoding is constrained to the
  schema and cannot drift into prose.
- **Sentinels instead of nulls.** The extraction schema asks for `-1` rather
  than `null` for an unknown price (`buy_agent/models.py`): a required `number`
  makes it structurally impossible to answer `"N/A"` and fail validation for the
  whole batch. `ExtractedProduct.to_product()` turns the sentinels back into
  `None`.
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
  straight out of the prompt's own example. `buy_agent/verification.py` drops
  any product whose name is absent from the sources, and blanks any price,
  rating or review count that does not appear in the text the model was shown. A
  blanked figure scores neutral instead of winning.
- **Quotes, checked as quotes, and cited.** The opinions in the report are the
  source pages' words, not the model's summary of them, and each is looked for
  in the sources as running text -- overlapping runs of five consecutive words,
  most of which have to be found. A paraphrase fails that and is dropped: an
  invented price is a number nobody wrote, but an invented quote is words in a
  reviewer's mouth. The page that cleared the check is kept on the quote and
  shown beside it, so a shopper can go and read the sentence.

- **Bounds that are enforced, not searched for.** A budget in the request text
  only shapes the query. `--max-price`, `--min-rating` and `--min-reviews` are
  numbers checked in Python after the pages are read and the duplicates merged,
  so the report answers the question that was asked -- and a product whose
  figure no page printed is kept rather than dropped for the extractor's miss.
- **A score that says what it is made of.** Every product carries the three
  shares its score was blended from and the weight each went in at, with the
  ones nothing was published for marked "assumed" -- because a missing rating
  and a middling one both score 0.5, and only one of them is a measurement.

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

That rule has a catch, and the report answers it rather than hiding it: 0.5 is
what a product with **no** rating scores and also what a thoroughly average one
scores, so the total alone cannot tell a measurement from an assumption. Every
score therefore comes with the shares it was blended from, and the assumed ones
say so -- on the CLI:

```
#1  Anker Soundcore Q30
     score  : 0.650  (rating 0.50 x0.50 assumed, popularity 0.50 x0.20 assumed, price 1.00 x0.30)
```

and under the bar on each card in the browser, with the same word. A report
whose every product is assumed three times over is a run that read nothing
useful, which is worth seeing next to the answer rather than only in the log
above it ([ADR-0041](docs/adr/0041-report-what-a-score-is-made-of.md)). Which
shares were assumed is decided where the scoring happens, never inferred from a
share being 0.5 -- a product priced exactly mid-way through the set scores that
having been read off a page.

The `x0.50` beside each share is the weight it went in at, and it is there
because the shares are each scored out of 1 on their own. Three of them under a
total they do not add up to read as arithmetic that has gone wrong, and nothing
else says whether a product placed first on its rating or on a price no page
printed. A run reports what it ranked with, so the card draws the weights rather
than working them out
([ADR-0045](docs/adr/0045-report-the-weights-a-score-was-blended-by.md)).

## Tests

```powershell
python -m pytest              # the Python suite
python -m pylint buy_agent    # ...and the linter over the package it covers
cd ui; npm test               # the UI's own tests, in jsdom
python -m pytest integration  # ...and against a real model, if one is pulled

python -m benchmark --scripted perfect   # score the pipeline, no model needed
python -m benchmark                      # ...and score whatever is serving
```

Neither of the first two touches the network or a model server, both run on
Windows and on Linux, and both are measured against a coverage floor CI
enforces. The third, in `integration/`, is the deliberate exception: it runs the
pipeline against a real Ollama on a model small enough for a CPU, which is the
only place the claims about JSON-schema decoding and Ollama's transport errors
are actually put to Ollama. It lives outside `testpaths`, so `python -m pytest`
cannot reach it, and a nightly job capped at five minutes is what runs it
(ADR-0026). vLLM is not in that job -- it needs a GPU, and a CPU runner cannot
host one honestly -- so its half is asserted in `tests/test_providers.py` and
named as a gap in ADR-0028.

Those tests ask whether the pipeline's promises held, which they do however
badly the model read the pages -- so none of them can say whether a change made
things better. `benchmark/` is the other half: ten fixed pages, an answer key
recording what each prints for each product, and a scorer turning a run into
eight shares in `[0, 1]` -- products found, products real, figures right,
figures misattributed, links, quotes, quotes faithful, ranking order (ADR-0036).
Only the model varies, so two scores a month apart are comparable; the nightly
run is scored as well as checked, and `--scripted perfect` puts a hand-written
answer through the whole real pipeline with no model at all and must come out at
1.000.

### The shape, read off the imports

Which module may know about which is the thing this project says most often and
the thing least able to break loudly: an import in the wrong direction runs
perfectly. It passes that module's own tests, keeps the coverage floor, survives
the mutation run, and shows up years later as the reason two things cannot be
moved apart. `tests/test_architecture.py` is where those sentences are
executable -- twenty-one rules over the import graph, parsed out of the package
with [ArchUnitPython](https://github.com/LukasNiessen/ArchUnitPython), costing
no model, no network and no run
([ADR-0047](docs/adr/0047-check-the-import-graph-with-archunit.md)).

- **The package is a line, and the line runs one way.** No import cycles, and
  none of the five trees that import it -- `tests/`, `integration/`,
  `benchmark/`, `demo/`, `scripts/` -- is imported back.
  `buy_agent/__init__.py` imports the four modules it re-exports from and no
  others, since importing any submodule runs it first: a `from` line there
  naming `payment` would put the optional AP2 stack behind `import buy_agent`.
- **Every module sits in a layer that reaches only downward** -- entry points,
  web, orchestration, pipeline, paying, model access, settings, domain. Four of
  those edges are decisions rather than tiers. The pipeline never reads the
  config, which is what lets `rank_products`, `ground` and `Constraints` be
  tested with three arguments and no environment. The pipeline never pays, so no
  step of a run can spend money it was not asked to (ADR-0046). Paying never
  asks the model, which is "never pay on an unverified number" as an import.
  And the model seam carries a prompt, a schema and an answer without ever
  knowing what an answer means (ADR-0038).
- **One seam, one module.** `mandates.py` alone imports the AP2 SDK (ADR-0046),
  `providers.py` alone a model client (ADR-0029), `search.py` alone the search
  backend (ADR-0021), `fetch.py` alone the HTML parser. The three modules that
  speak HTTP are exactly the three the suite patches, so a fourth would be a
  request no fake answers; `argparse` belongs to the two modules handed an
  `argv`, a parser below them being a third set of defaults.
- **The socket is the server's alone.** `server.py` is a standard-library HTTP
  server on purpose and only ever listens on it (ADR-0010), so a `socket`, an
  `ssl` or a `urllib.request` anywhere else is a module reaching out on its own.
  It also imports nothing outside the standard library -- read off the graph
  rather than off `requirements.txt`, so a dependency added tomorrow is covered
  without anybody writing the rule down again -- while `api.py` reaches no
  socket, thread or queue, which is what leaves the payloads testable by calling
  a function.
- **The package starts no process.** `subprocess`, `multiprocessing` and
  `webbrowser` are nobody's here: installing Ollama, pulling a model, building
  the UI and opening the page are `scripts/start.ps1`'s (ADR-0023), and the
  container starts neither model server either (ADR-0015). It is the one way out
  of the package that no fake in the suite could answer -- a child process would
  see neither the fake model nor the fake search nor the scratch cache
  directory, and a server that opened a browser would open it where nobody is
  sitting.
- **The steps take values and answer values.** Nothing in the pipeline or the
  domain reads an environment variable, a file, a clock or a random number,
  which is the half of "the pipeline never reads the config" no layer can state
  and what says a remembered answer (ADR-0044) is the same answer. It is why a
  page asked to come back later waits by a clock `BuyAgent` hands down rather than
  one `fetch` imports (ADR-0053): waiting is not deciding, and the rule is an
  import away from either. The steps do not chain
  themselves either: the order of the pipeline is `BuyAgent.run`'s to know, so a
  joint argued in one place stays a joint that can be moved. And nothing that
  decides the answer -- the ranking, the bounds, the grounding, the types --
  may reach the model, the fetcher or the search (ADR-0002).

Two things about how they are written. An import under `if TYPE_CHECKING:` does
not count, because it never runs: it is how this package already spells "I name
this type and do not use this module", and it is what lets `providers.py` take
an `AgentConfig` while importing nothing from `config`. And a negated rule whose
subject matches nothing *passes*, which is the one way a file like this can be
worse than no file, so every helper that names modules checks they exist and a
twenty-second test counts the layer placings -- a module renamed out of a rule,
left out of the layer table or named in two of its rows fails a test instead of
quietly becoming an exemption.

What the counts are, what `tests/test_conventions.py` checks that coverage
cannot, each of those twenty-one import rules written out beside the sentence it
came from, what pylint is configured to say and what it is deliberately not
(ADR-0048), what the benchmark measures, and the mutation run that grades the
suite every Saturday are in [Tests](docs/testing.md).

## Limitations

- **A figure can be real but attached to the wrong product.** Grounding checks
  that a number appears in the sources, not that it belongs to the product it
  was filed under, and small models sometimes give two products the same review
  count. Read the top 3 as candidates worth clicking rather than as a price
  quote. This is the one limitation here that is measured rather than only
  described: it is the benchmark's `attribution` metric.
- **A quote is tied to a page, not to a product on it.** A quoted opinion has to
  appear on a page that names the product (ADR-0025), so a verdict cannot move
  between pages about unrelated things -- but a review page covering eight
  headphones names all eight, and nothing stops a verdict moving between them.
  The `source` link beside each quote is that page, which is what makes this one
  checkable by eye rather than only describable (ADR-0042).
- **A bound has to be typed, not implied.** "under $200" in the request shapes
  the search query and nothing else; `--max-price 200` is what enforces it. And
  a product whose price no page printed is inside every budget, deliberately --
  a blank is the extractor having missed something more often than it is a $900
  tag.
- **A cached page is as current as its age, and so is a cached answer.** Both
  are kept for a day by default, so a figure can be up to that stale while
  reading as current, and the reading of it is a day old too. The two expire on
  one clock and the pages are part of an answer's key, so an answer is never
  reused for pages that have themselves gone stale; `--cache-ttl 0` is the run
  that reads and asks everything fresh.
- **A named source is a domain, not an author.** `--source @mkbhd` searches
  YouTube for that handle and keeps the YouTube pages that come back; a video by
  somebody else that mentions the handle can get through. The report links to
  the page, so whose it is can be seen.
- **Names are only as specific as the model makes them.** `lfm2.5` reported
  "Bose ANC" for a product the page named in full.
- **A mixed-currency search scores most of itself on nothing.** Prices are
  compared inside one currency and converted never (ADR-0043), so a search
  returning five currencies gets a price criterion that is "assumed" for four of
  them. That is the true state of what the run knows, and the cards say so --
  but the ranking is then carried by rating and popularity alone.
- **Paying is only as good as the page it read.** The mandates are signed
  correctly and bind to a price a merchant signed, but *which* merchant is the
  site the product page came from, and the product is whatever the extraction
  filed under that name -- both of which the limitation at the top of this list
  applies to. The approval prompt shows all three so the mistake is visible
  before it is signed; nothing downstream can catch it.
- **No real money has moved through the `http` rail.** It has been driven end to
  end against a purpose-built local counterparty -- one that signs the checkout,
  receives both mandates and checks that the Payment Mandate binds to the
  checkout *it* signed -- and against no payment processor. AP2 deliberately
  says nothing about the commerce protocol around it, so the two request shapes
  (`{url}/checkout`, `{url}/payment`) are this project's choice and are the part
  to expect to adjust for whatever you integrate with. The mandates inside them
  are the standard's.
- A shop or a search that says "come back later" is asked once more, and only
  that: a 429 or a 503 waits the `Retry-After` it named, capped at five seconds,
  and a search whose every engine failed waits two (ADR-0053). A 403, a 404 and a
  timeout are answers, not invitations. The model is the other way about -- one
  question, bounded by `--model-timeout`, never repeated (ADR-0051) -- so a model
  server that went quiet holding the prompt now ends the run with the remedy
  rather than hanging it.
- Some shops answer with JavaScript-rendered pages or a 403; those results fall
  back to their snippet rather than failing the run. Which is why the run says
  how the fetching went, on the CLI and in the browser alike: "Got usable page
  text from 0 of 10 result(s): 7 refused (403), 2 timed out". Grounding blanks
  every figure the pages did not back, so a report of "price unknown" throughout
  is either a bad model or nothing having been read, and that line is which.
- DuckDuckGo rate-limits heavy use; the agent asks a second time and then reports
  it as a `SearchError`.
- Only `lfm2.5` (1.2B) has been measured end to end for *speed*: it works, takes
  ~75s, and most of that is extraction. The failure modes above are the ones a
  small model shows, so a larger model should improve on them -- `python -m
  benchmark --model <tag>` is how to find out rather than assume, but no model
  larger than the nightly's `qwen3:0.6b` has been scored yet.
