# Architecture (C4)

The [C4 model](https://c4model.com) at three zoom levels, in Mermaid so GitHub
renders it. Why it is this way is in [adr/](adr/README.md). The idea through all
three levels: **the LLM is not in charge.** It refines the query and reads
products out of pages; filtering, grounding, ranking and ordering are Python.

Every diagram below draws with one key:

```mermaid
---
config:
  flowchart:
    wrappingWidth: 400
    rankSpacing: 16
---
graph LR
    keyPerson(["<b>Person</b>"]) ~~~ keySystem("<b>Software system</b>") ~~~ keyContainer("<b>Container</b>") ~~~ keyComponent("<b>Component</b>") ~~~ keyStore[("<b>Data store</b>")] ~~~ keyExternal("<b>External system</b>")

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef internal fill:#1168bd,stroke:#0b4884,color:#fff
    classDef container fill:#2e74c0,stroke:#1d5390,color:#fff
    classDef component fill:#85bbf0,stroke:#4f86c0,color:#000
    classDef external fill:#6b6b6b,stroke:#4d4d4d,color:#fff
    class keyPerson person
    class keySystem internal
    class keyContainer,keyStore container
    class keyComponent component
    class keyExternal external
```

Thick arrows are the steps of a run, numbered in order. Solid ones are calls and
requests, with the technology in brackets where one leaves the process. Dashed
ones are dependencies: what a part reads, builds or relies on. A dashed outline
is a boundary: the system, or the container whose parts are drawn.

## Level 1 -- System context

```mermaid
---
config:
  flowchart:
    wrappingWidth: 400
    nodeSpacing: 28
    rankSpacing: 56
---
graph TB
    shopper(["<b>Shopper</b><br/><i>[Person]</i><br/>Wants to buy something and<br/>would rather not read ten<br/>listicles first"])
    operator(["<b>Operator</b><br/><i>[Person]</i><br/>Runs the model server, and<br/>chooses which of its models<br/>the agent should ask"])

    system("<b>buy_agent</b><br/><i>[Software System]</i><br/>Turns a plain-language request<br/>into a ranked shortlist of real<br/>products, each figure backed by<br/>a source page, and scores which<br/>local model does that best")

    ollama("<b>Model server</b><br/><i>[External System]</i><br/>A local Ollama, a vLLM, or<br/>a LiteLLM proxy routing to<br/>whatever its owner chose --<br/>the last two behind an<br/>OpenAI-compatible API.<br/>Refines the query and<br/>extracts products, under a<br/>JSON schema that constrains<br/>decoding")
    ddg("<b>Search backend</b><br/><i>[External System]</i><br/>DuckDuckGo with no key, a<br/>SearXNG the shopper runs, or<br/>Brave on a key they hold --<br/>one row each")
    shops("<b>Shop and review pages</b><br/><i>[External System]</i><br/>The pages the search returns:<br/>the only source of prices,<br/>ratings and review counts")
    counterparty("<b>AP2 endpoint</b><br/><i>[External System]</i><br/>Whatever merchant or credential<br/>provider the operator names,<br/>reached only by a run that was<br/>asked to buy. None is named<br/>in this project")

    shopper -->|"asks for a product in<br/>their own words, and<br/>watches it become a<br/>ranked shortlist<br/>[CLI or web browser]"| system
    operator -->|"compares the models a<br/>server holds over three<br/>fixed cases, and reads<br/>how they ranked<br/>[CLI or web browser]"| system
    system -->|"prompts with a<br/>JSON schema<br/>[HTTP: :11434,<br/>:8000/v1 or :4000/v1]"| ollama
    system -->|"searches<br/>[HTTPS, or HTTP<br/>to a SearXNG]"| ddg
    system -->|"fetches and condenses<br/>[HTTPS]"| shops
    system -->|"photographs, for<br/>the card that links<br/>to it -- optional<br/>[headless Chromium]"| shops
    system -->|"presents a signed<br/>mandate, only when<br/>asked to buy<br/>[HTTPS]"| counterparty

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef internal fill:#1168bd,stroke:#0b4884,color:#fff
    classDef external fill:#6b6b6b,stroke:#4d4d4d,color:#fff
    class shopper,operator person
    class system internal
    class ollama,ddg,shops,counterparty external
```

Everything runs on machines the shopper controls. Only the search and the page
fetches leave, plus a second visit by the camera where a loopback server takes
pictures ([ADR-0065](adr/0065-photograph-each-products-page-from-a-server-bound-to-this-machine.md)),
and a signed AP2 mandate where a run was asked to buy; the default rail charges
nobody ([ADR-0046](adr/0046-pay-on-the-shoppers-behalf-with-ap2.md)). Which
model server answers is `AgentConfig.provider`, and only `buy_agent/providers.py`
knows the difference
([ADR-0028](adr/0028-serve-the-model-from-ollama-or-vllm.md),
[ADR-0029](adr/0029-one-table-per-model-server.md),
[ADR-0068](adr/0068-reach-a-litellm-proxy-as-a-third-model-server.md)).

The operator's way in is the benchmark, which asks the model server and nothing
else: every model is served the same fixed pages, so a comparison never reaches
the web
([ADR-0070](adr/0070-compare-local-models-and-give-the-comparison-a-page.md)).

## Level 2 -- Containers

One system, drawn once per person: the shop the shopper uses, and the benchmark
the operator uses. Both drive the same agent pipeline and keep what they keep in
the same directory.

### The shop

```mermaid
---
config:
  flowchart:
    wrappingWidth: 400
    nodeSpacing: 24
    rankSpacing: 50
---
graph TB
    shopper(["<b>Shopper</b><br/><i>[Person]</i>"])

    subgraph system["buy_agent [Software System]"]
        spa("<b>Web UI</b><br/><i>[Container: Angular 22]</i><br/>A form, a live progress log<br/>and the ranked cards.<br/>Decides nothing: it renders<br/>what the API sends")
        cli("<b>CLI</b><br/><i>[Container: Python]</i><br/>A flag per setting the API<br/>takes, into one AgentConfig;<br/>runs one search, logs the<br/>top N")
        server("<b>HTTP server</b><br/><i>[Container: Python, stdlib]</i><br/>Serves the built UI and the<br/>JSON API, and relays a run's<br/>log lines as Server-Sent<br/>Events")
        paying("<b>Paying</b><br/><i>[Container: Python library,<br/>optional]</i><br/>A cart out of one grounded<br/>product, two signed AP2<br/>mandates, and the rail they<br/>are presented to. After the<br/>pipeline, never inside it")
        pipeline("<b>Agent pipeline</b><br/><i>[Container: Python library]</i><br/>BuyAgent.run(): search,<br/>extract, ground, deduplicate,<br/>rank. The one implementation<br/>every front end drives")
        camera("<b>Camera</b><br/><i>[Container: Playwright,<br/>optional]</i><br/>One headless Chromium on<br/>one thread, photographing<br/>the page a card links to.<br/>Only on a server bound to<br/>this machine")
        store[("<b>Cache directory</b><br/><i>[Container: JSON files]</i><br/>$BUY_AGENT_CACHE_DIR: the<br/>pages and answers a run may<br/>reuse, the journal of past<br/>runs, the benchmark's board")]
    end

    ollama("<b>Model server</b><br/><i>[External System]</i><br/>Ollama, vLLM or<br/>a LiteLLM proxy")
    ddg("<b>Search backend</b><br/><i>[External System]</i><br/>DuckDuckGo, SearXNG<br/>or Brave")
    shops("<b>Shop and review pages</b><br/><i>[External System]</i>")
    counterparty("<b>AP2 endpoint</b><br/><i>[External System]</i><br/>Whatever the operator<br/>names; none is named here")

    shopper -->|"visits localhost:8000<br/>[HTTP]"| spa
    shopper -->|"types a request<br/>[terminal]"| cli
    spa -->|"the JSON API, a run's<br/>event stream, card<br/>screenshots [HTTP, SSE]"| server
    cli -->|"calls run()"| pipeline
    server -->|"runs a search in a<br/>worker thread, relays<br/>its log lines"| pipeline
    cli -->|"pays, once a person<br/>approved this cart"| paying
    server -->|"POST /api/pay, with<br/>the approval the<br/>page witnessed"| paying
    server -->|"asks for a picture,<br/>waits for it"| camera

    pipeline -->|"[HTTP]"| ollama
    pipeline -->|"[HTTPS, or HTTP<br/>to a SearXNG]"| ddg
    pipeline -->|"[HTTPS]"| shops
    pipeline -->|"pages and answers to<br/>reuse, and the journal"| store
    paying -->|"[HTTPS]"| counterparty
    camera -->|"[HTTPS]"| shops
    store ~~~ ollama & ddg & shops & counterparty

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef container fill:#2e74c0,stroke:#1d5390,color:#fff
    classDef component fill:#85bbf0,stroke:#4f86c0,color:#000
    classDef external fill:#6b6b6b,stroke:#4d4d4d,color:#fff
    class shopper person
    class cli,spa,server,pipeline,paying,camera,store container
    class ollama,ddg,shops,counterparty external
    style system fill:none,stroke:#8c8c8c,stroke-width:1px,stroke-dasharray:6 4
```

The CLI and the server are two front ends onto one `BuyAgent.run()`, and onto
one table of settings: each row of `api.OPTIONS` is a flag at one door and a
request key at the other, refused in the same words at both
([ADR-0071](adr/0071-build-the-cli-flags-off-the-options-table.md)). The server
is stdlib-only and admits every request before routing it, so the API answers
its own page and not other tabs
([ADR-0018](adr/0018-guard-the-loopback-server-against-other-pages.md)).

Paying is its own container: both front ends reach it, it reaches nothing back,
and it settles afterwards from a product the run already grounded
([ADR-0046](adr/0046-pay-on-the-shoppers-behalf-with-ap2.md)). The camera is
further out still: the pipeline never knows it exists, and it is the one part
that starts a process, so only a loopback server has one
([ADR-0065](adr/0065-photograph-each-products-page-from-a-server-bound-to-this-machine.md)).

What is kept between runs is JSON under `$BUY_AGENT_CACHE_DIR`: the page text
and the answers a later run may reuse, which expire and are capped by size
([ADR-0040](adr/0040-cache-the-page-text-on-disk.md),
[ADR-0044](adr/0044-remember-a-deterministic-model-answer.md),
[ADR-0052](adr/0052-cap-the-cache-by-size-as-well-as-age.md)); the journal of
what past runs of a search reported
([ADR-0060](adr/0060-keep-a-run-journal-beside-the-cache-not-in-it.md)); and the
benchmark's board, written the journal's way. Neither of the last two expires.

The `Dockerfile` ships the shop's containers as one image, without the AP2 SDK,
the camera or the benchmark. The model server stays outside it
([ADR-0015](adr/0015-package-the-web-tier-as-a-container.md)).

### The benchmark

```mermaid
---
config:
  flowchart:
    wrappingWidth: 400
    nodeSpacing: 24
    rankSpacing: 50
---
graph TB
    operator(["<b>Operator</b><br/><i>[Person]</i>"])

    subgraph system["buy_agent [Software System]"]
        subgraph benchtier["benchmark/ -- in the repository, not in the image"]
            benchpage("<b>Benchmark page</b><br/><i>[Container: HTML and<br/>JavaScript, no build]</i><br/>Pick a server, its models<br/>and the cases; read the<br/>standings as runs finish.<br/>Decides nothing either")
            bench("<b>Benchmark</b><br/><i>[Container: Python]</i><br/>python -m benchmark, or<br/>python -m benchmark.server.<br/>Runs models over three fixed<br/>cases, scores and times each<br/>run, and keeps it")
        end
        pipeline("<b>Agent pipeline</b><br/><i>[Container: Python library]</i>")
        store[("<b>Cache directory</b><br/><i>[Container: JSON files]</i>")]
    end

    ollama("<b>Model server</b><br/><i>[External System]</i>")

    operator -->|"visits localhost:8100<br/>[HTTP]"| benchpage
    operator -->|"names models<br/>and cases<br/>[terminal]"| bench
    benchpage -->|"its JSON API, polled<br/>while a comparison runs<br/>[HTTP]"| bench
    bench -->|"calls run() over a case's<br/>own pages, search and<br/>fetch swapped out"| pipeline
    bench -->|"each contender's latest<br/>run of each case"| store
    pipeline -->|"[HTTP]"| ollama
    store ~~~ ollama

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef container fill:#2e74c0,stroke:#1d5390,color:#fff
    classDef component fill:#85bbf0,stroke:#4f86c0,color:#000
    classDef external fill:#6b6b6b,stroke:#4d4d4d,color:#fff
    class operator person
    class benchpage,bench,pipeline,store container
    class ollama external
    style system fill:none,stroke:#8c8c8c,stroke-width:1px,stroke-dasharray:6 4
    style benchtier fill:none,stroke:#8c8c8c,stroke-width:1px,stroke-dasharray:3 3
```

The benchmark is a third way into the same `BuyAgent.run()`, for the operator
rather than the shopper
([ADR-0070](adr/0070-compare-local-models-and-give-the-comparison-a-page.md)).
Search and fetch are swapped out on `agent` for a case's own pages, so every
model reads the same text and none of it comes off the web. Its page is three
static files with no build step, served by a subclass of the shop's handler, so
a request is admitted, refused and answered exactly as the shop's are. Nothing
in the package imports it.

## Level 3 -- Components of the agent pipeline

There are too many parts here for one legible picture, so the pipeline is drawn
four ways: the run step by step, what a run is built from, the types its steps
share, and paying, which comes after it.

### The run, step by step

`BuyAgent.run()`, called by either front end, calls each step in the order
numbered.

```mermaid
---
config:
  flowchart:
    wrappingWidth: 400
    nodeSpacing: 24
    rankSpacing: 40
---
graph LR
    agent("<b>BuyAgent</b><br/><i>[Component: agent.py]</i><br/>Orchestrates the fixed<br/>pipeline, hands each step the<br/>checkpoint, the wait and the<br/>recorder, and translates<br/>transport failures into an<br/>actionable message")
    extraction("<b>Extraction</b><br/><i>[Component: extraction.py]</i><br/>Both prompts and both<br/>chains, plus name cleaning<br/>and merging of variant names")
    search("<b>Search</b><br/><i>[Component: search.py]</i><br/>Which backend a search is<br/>asked through, one row each:<br/>where it listens, its key, how<br/>it is asked and what it answers.<br/>Asks once more where the<br/>backend failed, and raises<br/>SearchError when that one<br/>does too")
    sources("<b>Sources</b><br/><i>[Component: sources.py]</i><br/>Reads a trusted source down<br/>to a domain and a term,<br/>narrows the query to it, and<br/>says whether a result came<br/>from it")
    fetch("<b>Fetch</b><br/><i>[Component: fetch.py]</i><br/>Fetches result pages in<br/>parallel and keeps the lines<br/>quoting a figure and the lines<br/>passing judgement, each on a<br/>budget of its own; asks a page<br/>that said to come back once<br/>more; tallies how the rest<br/>failed")
    verification("<b>Verification</b><br/><i>[Component: verification.py]</i><br/>Drops products the sources<br/>never named, blanks any figure<br/>and any quote the page text<br/>does not contain, and links<br/>each product -- and each quote<br/>-- to the page it came off")
    constraints("<b>Constraints</b><br/><i>[Component: constraints.py]</i><br/>The shopper's bounds -- max<br/>price, min rating, min reviews<br/>-- applied after merging and<br/>before ranking. An unknown<br/>figure is not a violation")
    ranking("<b>Ranking</b><br/><i>[Component: ranking.py]</i><br/>Weighted score over rating,<br/>popularity and price -- prices<br/>compared inside one currency<br/>-- and the shares it was<br/>blended from. No LLM")
    logsetup("<b>Report and logging</b><br/><i>[Component: logging_setup.py]</i><br/>Log format, the top-N report<br/>the browser also reads as<br/>events, and what moved since<br/>the last run, for --compare")

    ollama("<b>Model server</b><br/><i>[External System]</i>")
    ddg("<b>Search backend</b><br/><i>[External System]</i>")
    shops("<b>Shop and review pages</b><br/><i>[External System]</i>")

    agent ==>|"1. refine the query<br/>4. extract products<br/>6. deduplicate"| extraction
    agent ==>|"2. search -- once, or<br/>once per named source"| search
    agent -.->|"narrows the query,<br/>holds the results to it"| sources
    agent ==>|"3. enrich the results"| fetch
    agent ==>|"5. clean, then ground<br/>against the same text"| verification
    agent ==>|"7. hold to the<br/>shopper's bounds"| constraints
    agent ==>|"8. rank"| ranking
    agent ==>|"9. log the top N"| logsetup

    extraction -->|"its chains, through<br/>chat.py<br/>[JSON schema, HTTP]"| ollama
    search -->|"[HTTPS, or HTTP<br/>to a SearXNG]"| ddg
    fetch -->|"[HTTPS]"| shops

    classDef component fill:#85bbf0,stroke:#4f86c0,color:#000
    classDef external fill:#6b6b6b,stroke:#4d4d4d,color:#fff
    class agent,extraction,search,sources,fetch,verification,constraints,ranking,logsetup component
    class ollama,ddg,shops external
```

Three joints in that order are load-bearing:

- `clean_products` runs **before** `ground`, so a name wearing its publisher
  suffix ("... Review | AudioSite") does not fail the name check.
- `ground` runs **before** `deduplicate`, so merging only combines figures and
  links the sources back.
- The bounds run **after** `deduplicate`, which may supply the price, and
  **before** `rank_products`, since price scores relative to the set (ADR-0039).

A merge also *pairs* figures: `models.QUALIFIERS` ties currency to price and
review count to rating, and `_fill_gaps` moves whole groups (ADR-0022).
Extraction and verification must see the same text, which is why
`fetch.enrich()` puts it on `SearchResult`.

- **Step 2** is one search, or one per named source, pooled, deduplicated by URL
  and cut back to width. `sources.py` decides what a source is; nothing
  downstream knows the feature exists (ADR-0021, ADR-0027, ADR-0057).
- **Step 3** tallies how fetching failed ("7 refused (403), 2 timed out") at
  INFO, since a run that read nothing otherwise looks like a bad model.
- **Step 5** holds quotes to a stricter bar than figures: most overlapping
  five-word runs must be found, on one page that mentions the product
  (ADR-0024, ADR-0025), and that page is kept on the quote (ADR-0042). It also
  links each product to the first searched page mentioning it (ADR-0017).
- **Step 7** enforces the shopper's bounds. Unknown figures pass, and so do
  prices outside the run's one currency, which is never converted (ADR-0039,
  ADR-0043, ADR-0056).
- **Step 8** keeps the shares a score was blended from, so an assumed 0.5 is
  told from a measured one (ADR-0041).

### What a run is built from

```mermaid
---
config:
  flowchart:
    wrappingWidth: 400
    nodeSpacing: 24
    rankSpacing: 50
---
graph TB
    doors("<b>CLI and HTTP server</b><br/><i>[Containers]</i>")

    subgraph pipeline["Agent pipeline [Container: Python library]"]
        config("<b>AgentConfig</b><br/><i>[Component: config.py]</i><br/>Every setting of a run and<br/>its default, the range each<br/>number is held to, and what<br/>a region and a currency must<br/>look like -- the rules both<br/>doors' options are read by")
        boundsc("<b>Noticed bounds</b><br/><i>[Component: bounds.py]</i><br/>Reads &quot;under $200&quot; out of<br/>the request in ordinary<br/>Python, and answers the<br/>figure and the words it read.<br/>Offered at both doors and<br/>applied at neither")
        agent("<b>BuyAgent</b><br/><i>[Component: agent.py]</i>")
        search("<b>Search</b><br/><i>[Component: search.py]</i>")
        fetch("<b>Fetch</b><br/><i>[Component: fetch.py]</i>")
        chat("<b>Chat</b><br/><i>[Component: chat.py]</i><br/>A prompt with the run's<br/>values in it, a chain binding<br/>one to a schema, and the<br/>answer read back as that<br/>schema -- or refused")
        journal("<b>Journal</b><br/><i>[Component: journal.py]</i><br/>What past runs of this same<br/>search reported -- a name, a<br/>price and a currency each --<br/>so this one can say what is<br/>cheaper, dearer, new or gone.<br/>Bounded by a count and never<br/>by an age, and one setting off")
        providers("<b>Providers</b><br/><i>[Component: providers.py]</i><br/>Everything that differs<br/>between Ollama, vLLM and a<br/>LiteLLM proxy, one row each:<br/>the model, address and key it<br/>defaults to, the client and<br/>how it declares a schema, the<br/>listing, which of the two<br/>settings it takes per request,<br/>the errors that mean &quot;not<br/>there&quot;, and what to say")
        cache("<b>Cache</b><br/><i>[Component: cache.py]</i><br/>What a run can reuse: the<br/>text of a fetched page, and<br/>the answer a model gave about<br/>it. Kept for a day, and<br/>bounded by size as well as<br/>age. Best-effort: every<br/>failure is a miss, never a<br/>failed run")
    end

    ollama("<b>Model server</b><br/><i>[External System]</i>")
    store[("<b>Cache directory</b><br/><i>[Container]</i>")]

    doors -->|"run(request, sort_by)"| agent
    doors -.->|"build it: a flag<br/>or a request key per<br/>api.OPTIONS row"| config
    doors -.->|"offer what the request<br/>asks for in words"| boundsc
    doors -.->|"write this run down and<br/>say what moved, keyed by<br/>what was asked, not how"| journal
    config -.->|"configures"| agent
    config -.->|"search_backend"| search
    config -.->|"model_server: the<br/>model, address and<br/>key per provider"| providers
    agent ==>|"3. enrich"| fetch
    agent -->|"invokes the chains<br/>Extraction built"| chat
    agent -.->|"builds the chat model,<br/>names the failure"| providers
    agent -.->|"reuses an answer the<br/>model gave before"| cache
    chat -->|"a prompt, a schema, one<br/>answer, from the server<br/>the provider built"| providers
    fetch -.->|"reads what it<br/>read last time"| cache
    providers -->|"prompts under a<br/>JSON schema [HTTP]"| ollama
    cache -->|"pages/ and answers/"| store
    journal -->|"runs/"| store

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef container fill:#2e74c0,stroke:#1d5390,color:#fff
    classDef component fill:#85bbf0,stroke:#4f86c0,color:#000
    classDef external fill:#6b6b6b,stroke:#4d4d4d,color:#fff
    class doors,store container
    class config,boundsc,agent,search,fetch,chat,journal,providers,cache component
    class ollama external
    style pipeline fill:none,stroke:#8c8c8c,stroke-width:1px,stroke-dasharray:6 4
```

Each table is reached one way: `AgentConfig` names the row (`model_server`,
`search_backend`, and `rail_used` for paying), and nothing above it branches on
a name (ADR-0029, ADR-0046, ADR-0057). The cache wraps the chat model only for a
deterministic run, while the journal is keyed by what was asked and never by the
model (ADR-0044, ADR-0060).

### The types every step speaks

```mermaid
---
config:
  flowchart:
    wrappingWidth: 400
    nodeSpacing: 24
    rankSpacing: 50
---
graph TB
    subgraph pipeline["Agent pipeline [Container: Python library]"]
        extraction("<b>Extraction</b>")
        verification("<b>Verification</b>")
        constraints("<b>Constraints</b>")
        ranking("<b>Ranking</b>")
        fetch("<b>Fetch</b>")
        models("<b>Models</b><br/><i>[Component: models.py]</i><br/>ExtractedProduct (sentinels, for<br/>the LLM's schema) vs Product<br/>(None), the Offer each page<br/>priced it at, and the Removal a<br/>step hands over when it takes<br/>a candidate out")
        moneyc("<b>Money</b><br/><i>[Component: money.py]</i><br/>Every currency table: which<br/>spellings are one currency,<br/>which ones a page is scanned<br/>for, how an amount is written<br/>and how many minor units it<br/>comes to")
    end
    payment("<b>Payment</b><br/><i>[Component of Paying]</i>")

    extraction -.->|"ExtractedProduct →<br/>Product; a Removal per<br/>headline, fold and<br/>nameless entry"| models
    verification -.->|"a Removal per product<br/>no page named"| models
    constraints -.->|"a Removal per product<br/>the bounds would not have"| models
    ranking -.-> models
    payment -.->|"only a grounded price<br/>may be paid"| models
    models -.->|"places the spelling<br/>a listing named"| moneyc
    fetch -.->|"scans for the spellings<br/>it can place"| moneyc
    payment -.->|"counts the cart in<br/>minor units"| moneyc

    classDef component fill:#85bbf0,stroke:#4f86c0,color:#000
    class extraction,verification,constraints,ranking,fetch,models,moneyc,payment component
    style pipeline fill:none,stroke:#8c8c8c,stroke-width:1px,stroke-dasharray:6 4
```

The five heuristics that take a whole candidate out (`clean_products`,
`drop_ungrounded`, `merge_variants`, `deduplicate`'s nameless drop and
`Constraints.apply`) also call `record` with a `models.Removal`, whose sentence
nothing downstream rewords. It defaults to `models.nothing_recorded`. Steps that
only blank something record nothing (ADR-0055).

### Paying

```mermaid
---
config:
  flowchart:
    wrappingWidth: 400
    nodeSpacing: 32
    rankSpacing: 50
---
graph TB
    cli("<b>CLI</b><br/><i>[Container]</i>")
    server("<b>HTTP server</b><br/><i>[Container]</i>")
    config("<b>AgentConfig</b><br/><i>[Component of the pipeline]</i>")

    subgraph paying["Paying [Container: Python library, optional]"]
        payment("<b>Payment</b><br/><i>[Component: payment.py]</i><br/>What may be bought and for<br/>how much: a cart out of a<br/>grounded product, the spend<br/>limit, and the receipt. One<br/>failure, PaymentError")
        railsc("<b>Rails</b><br/><i>[Component: rails.py]</i><br/>Who the payment goes through,<br/>one row each: where it listens,<br/>whether it needs an address and<br/>an enrolled key, whether it<br/>moves money, and what to say<br/>when it cannot be reached")
        mandatesc("<b>Mandates</b><br/><i>[Component: mandates.py]</i><br/>The only module that imports<br/>the AP2 SDK. Signs the Checkout<br/>and Payment Mandates, bound to<br/>the merchant's signed checkout<br/>by hash, and checks an open<br/>mandate's constraints")
    end

    counterparty("<b>AP2 endpoint</b><br/><i>[External System]</i><br/>Whatever merchant or credential<br/>provider the operator names.<br/>None is named in this project")

    cli -->|"10. pay, once a person<br/>approved this cart"| payment
    server -->|"POST /api/pay, with<br/>the approval the<br/>page witnessed"| payment
    payment -->|"signs the two mandates"| mandatesc
    payment -->|"config.rail_used: the<br/>checkout, then the<br/>settlement"| railsc
    config -.->|"rail_used: where a payment<br/>goes and what it needs"| railsc
    railsc -->|"signs its own checkout<br/>on the dry run"| mandatesc
    railsc -->|"[HTTPS]"| counterparty

    classDef container fill:#2e74c0,stroke:#1d5390,color:#fff
    classDef component fill:#85bbf0,stroke:#4f86c0,color:#000
    classDef external fill:#6b6b6b,stroke:#4d4d4d,color:#fff
    class cli,server container
    class config,payment,railsc,mandatesc component
    class counterparty external
    style paying fill:none,stroke:#8c8c8c,stroke-width:1px,stroke-dasharray:6 4
```

Step 10 is outside the run's order: `BuyAgent.run` ends at the report, and
paying happens afterwards to one product a person or an open mandate approved. A
price no source printed, or in a currency the run cannot place, is never paid --
the opposite of the bounds, which keep what they cannot judge (ADR-0046).

## Level 3 -- Components of the web tier

```mermaid
---
config:
  flowchart:
    wrappingWidth: 400
    nodeSpacing: 28
    rankSpacing: 50
---
graph TB
    browserUser(["<b>Shopper</b><br/><i>[Person]</i>"])

    subgraph spa["Web UI [Container: Angular]"]
        form("<b>SearchForm</b><br/><i>[Component: search-form]</i><br/>The request and every option,<br/>seeded from /api/config,<br/>and refused here first")
        app("<b>App</b><br/><i>[Component: app.ts]</i><br/>Holds the run's state in<br/>signals; splits the answer<br/>into the top N and the rest,<br/>and folds under it what<br/>moved since the last run<br/>and what the run took out")
        log("<b>ProgressLog</b><br/><i>[Component: progress-log]</i><br/>The agent's own log lines,<br/>as they arrive, and a<br/>transcript to download for<br/>a run that failed or was<br/>stopped")
        card("<b>ProductCard</b><br/><i>[Component: product-card]</i><br/>One ranked product, drawn<br/>from the labels the API<br/>sent: the shares its score<br/>was blended from, each<br/>listing's price, a picture<br/>of its page, and two clicks<br/>to buy it")
        agentsvc("<b>AgentService</b><br/><i>[Component: agent.ts]</i><br/>HttpClient for the JSON<br/>endpoints; wraps<br/>EventSource as an<br/>Observable, so<br/>unsubscribing is the<br/>Stop button")
    end

    subgraph srv["HTTP server [Container: Python]"]
        handler("<b>BuyAgentHandler</b><br/><i>[Component: server.py]</i><br/>Admits the request, then<br/>routes /api to the API and<br/>everything else to the<br/>built app, falling back<br/>to index.html")
        guard("<b>_refused</b><br/><i>[Component: server.py]</i><br/>Refuses a request another<br/>site's page made, and a<br/>Host that merely resolves<br/>here -- loopback is not a<br/>boundary the browser<br/>respects")
        relay("<b>_LogRelay</b><br/><i>[Component: server.py]</i><br/>A handler on the package<br/>logger: fans the pipeline's<br/>records out by the context<br/>a run is watched through,<br/>so two runs never see each<br/>other's progress, and a step<br/>with threads of its own<br/>still reaches its stream")
        api("<b>API</b><br/><i>[Component: api.py]</i><br/>OPTIONS, the settings both<br/>doors read. Coerces a<br/>request into an AgentConfig,<br/>runs the pipeline, collects<br/>what it took out and what<br/>moved, shapes products as<br/>JSON, maps each failure to<br/>a status (400 / 502 / 503);<br/>re-ranks or pays for a<br/>finished run without<br/>running one")
    end

    cli("<b>CLI</b><br/><i>[Container]</i>")
    agentpipeline("<b>Agent pipeline</b><br/><i>[Container]</i>")
    paying("<b>Paying</b><br/><i>[Container]</i>")
    camera("<b>Camera</b><br/><i>[Container]</i>")

    browserUser --> form
    form -->|"submit, and what to check:<br/>the request's bounds, the<br/>sources, the model server"| app
    app --> log
    app --> card
    card -->|"pay, on the<br/>second click"| app
    app -->|"the defaults, search, rank<br/>and pay, and every reading<br/>the form asks for"| agentsvc
    agentsvc -->|"GET /api/search/stream<br/>[SSE: log, result,<br/>failure, ping]"| handler
    agentsvc -->|"GET /api/config,<br/>/api/models, /api/sources,<br/>/api/bounds<br/>POST /api/rank, /api/pay<br/>[JSON]"| handler
    card -->|"GET /api/screenshot,<br/>from an img [JPEG]"| handler

    handler -->|"before any routing"| guard
    handler -->|"parse_options, run_search,<br/>rank_again, pay_now,<br/>screenshot, the readings"| api
    handler -->|"reads the queue<br/>for this run"| relay
    cli -.->|"a flag per OPTIONS row;<br/>--json is results_payload"| api
    api -->|"agent_factory(config)<br/>.run(), then what moved<br/>[worker thread]"| agentpipeline
    api -->|"cart_for,<br/>then pay_for"| paying
    api -->|"camera.shoot(url)"| camera

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef container fill:#2e74c0,stroke:#1d5390,color:#fff
    classDef component fill:#85bbf0,stroke:#4f86c0,color:#000
    class browserUser person
    class cli,agentpipeline,paying,camera container
    class app,form,log,card,agentsvc,handler,guard,relay,api component
    style spa fill:none,stroke:#8c8c8c,stroke-width:1px,stroke-dasharray:6 4
    style srv fill:none,stroke:#8c8c8c,stroke-width:1px,stroke-dasharray:6 4
```

Deliberate details:

- **A run is streamed.** The UI uses `GET /api/search/stream`; `POST
  /api/search` is the same run in one response, for scripts.
- **Re-ordering and paying run nothing.** `POST /api/rank` calls only
  `rank_products` on the products and currency the page holds (ADR-0035,
  ADR-0056). `POST /api/pay` builds the cart on the server, and the page's echo
  of title, price and currency must match it (ADR-0012, ADR-0046).
- **Closing the stream stops the run at its next `checkpoint`.** A chat call in
  flight still finishes (ADR-0034).
- **The failure event is `failure`, not `error`**, because `EventSource`
  reconnects on `error` and would start the search again.
- **`dropped` and `changes` travel with the result** and survive a re-sort,
  which reports neither of its own (ADR-0055, ADR-0060).
- **The browser decides nothing.** Labels such as `price_label` are Python's,
  and `sort_by` is a request even for a finished run.
- **A picture is the card's own request**: an `<img>` pointed at
  `/api/screenshot`, only where `/api/config` says this server has a camera and
  the product links somewhere. It loads lazily, and a failed one drops its frame
  (ADR-0065).
- **One table of settings, two doors.** `parse_options` reads a request key per
  `api.OPTIONS` row and the CLI makes a flag of each, so both refuse the same
  values in the same words (ADR-0033, ADR-0071).
- **Loopback is not a boundary the browser respects**, so `_refused` turns away
  cross-site requests and foreign `Host`s before routing (ADR-0018).

## Level 3 -- Components of the benchmark

```mermaid
---
config:
  flowchart:
    wrappingWidth: 400
    nodeSpacing: 28
    rankSpacing: 50
---
graph TB
    operator(["<b>Operator</b><br/><i>[Person]</i>"])
    benchpage("<b>Benchmark page</b><br/><i>[Container: benchmark/web]</i><br/>Draws what /api/state<br/>answers, sends what the<br/>form says, and writes text,<br/>never markup")

    subgraph benchmark["Benchmark [Container: Python, not shipped]"]
        commandline("<b>Command line</b><br/><i>[Component: __main__.py]</i><br/>python -m benchmark: every<br/>contender over every case<br/>named, the standings printed,<br/>--json the page's payload")
        baseline("<b>Baseline</b><br/><i>[Component: baseline.py]</i><br/>--baseline: each run beside<br/>the same contender's run of<br/>the case in an earlier --json,<br/>metric by metric")
        handler("<b>BenchmarkHandler</b><br/><i>[Component: server.py]</i><br/>The shop's handler,<br/>subclassed: it refuses what<br/>the shop's does, then routes<br/>to the benchmark's endpoints<br/>in place of the shop's")
        bench("<b>Bench</b><br/><i>[Component: server.py]</i><br/>One comparison at a time,<br/>on a thread of its own:<br/>closing the page does not<br/>stop it, and Stop ends it<br/>at the next step")
        compare("<b>Comparison</b><br/><i>[Component: compare.py]</i><br/>A contender is a model on<br/>a server or a script. Times<br/>each question, keeps an<br/>unreadable answer as a 0,<br/>raises for a model that<br/>cannot be asked, and ranks<br/>the standings")
        cases("<b>Cases</b><br/><i>[Component: cases.py and<br/>a module per case]</i><br/>headphones, laptops,<br/>espresso: a request, its<br/>pages, the key to what they<br/>print, and a perfect and<br/>a sloppy script")
        runner("<b>Runner</b><br/><i>[Component: runner.py]</i><br/>Runs BuyAgent over a case's<br/>own pages: search_web and<br/>enrich swapped out on agent,<br/>the text condensed by the<br/>real fetch.condense")
        scoring("<b>Scoring</b><br/><i>[Component: scoring.py,<br/>query.py]</i><br/>Eight shares in [0, 1]<br/>against the case's key --<br/>figures and verdicts, product<br/>by product -- weighed pair by<br/>pair; the query judged apart,<br/>as checks with sentences")
        scoredunder("<b>Scored under</b><br/><i>[Component: pipeline.py]</i><br/>A fingerprint of the code<br/>between the pages and the<br/>scorecard, and the settings<br/>that reach the model")
        board("<b>Board</b><br/><i>[Component: board.py]</i><br/>Each contender's latest run<br/>of each case, in board.json<br/>beside the cache, read afresh<br/>on every look; a run scored<br/>against pages since changed<br/>is left out, one scored under<br/>other code is kept and marked")
    end

    pipeline("<b>Agent pipeline</b><br/><i>[Container]</i>")
    ollama("<b>Model server</b><br/><i>[External System]</i>")

    operator -->|"picks a server, its<br/>models and the cases"| benchpage
    operator -->|"--model, --scripted,<br/>--case [terminal]"| commandline
    benchpage -->|"GET /api/config,<br/>/api/models, /api/state<br/>while one runs<br/>POST /api/run, /api/stop,<br/>/api/clear [JSON]"| handler
    handler -->|"start, stop,<br/>clear, state"| bench
    bench -->|"run_case, contender by<br/>contender [its own thread]"| compare
    commandline -->|"run_case, contender<br/>by contender"| compare
    compare -.->|"the request, the key,<br/>the scripts"| cases
    compare -->|"run_benchmark, the<br/>contender's model behind<br/>a stopwatch"| runner
    compare -.->|"builds a served<br/>contender's model off<br/>its provider row, and<br/>asks its listing which<br/>build answered"| pipeline
    compare -->|"records with each<br/>run, and marks a kept<br/>one made under<br/>another"| scoredunder
    commandline -->|"--baseline FILE"| baseline
    runner -->|"BuyAgent.run(),<br/>over the case"| pipeline
    runner -->|"score_run"| scoring
    compare -->|"judge_query"| scoring
    scoring -.->|"matches names by<br/>grounding's own words,<br/>model numbers apart;<br/>orders by rank_products"| pipeline
    bench -->|"adds each run,<br/>reads them all"| board
    commandline -->|"the same, unless<br/>--no-save"| board
    pipeline -->|"[HTTP]"| ollama

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef container fill:#2e74c0,stroke:#1d5390,color:#fff
    classDef component fill:#85bbf0,stroke:#4f86c0,color:#000
    classDef external fill:#6b6b6b,stroke:#4d4d4d,color:#fff
    class operator person
    class benchpage,pipeline container
    class commandline,baseline,handler,bench,compare,runner,cases,scoring,scoredunder,board component
    class ollama external
    style benchmark fill:none,stroke:#8c8c8c,stroke-width:1px,stroke-dasharray:6 4
```

What it keeps of the shop, and what it changes
([ADR-0070](adr/0070-compare-local-models-and-give-the-comparison-a-page.md)):

- **The pipeline is the shop's own, run whole.** `runner.serving_the_corpus`
  swaps `search_web` and `enrich` on `agent` for a case's pages, condensed by the
  real `fetch.condense`, so every model reads the same text, cut the way a run
  cuts it. A case runs with `cache_ttl` 0 and its model handed in, so every
  question is really asked (ADR-0044).
- **A contender is reached the way a run reaches one**:
  `config.model_server.chat_model(config)`, on the case's settings (ADR-0029). A
  `Stopwatch` round it times each of the two questions.
- **An unreadable answer is a result; a model that cannot be asked is not.** The
  first scores 0 and is kept. The second is reported in its server's own words,
  and its remaining cases are skipped (ADR-0009).
- **The query is scored apart.** It moves the search, not what the pages say, so
  it is never blended into the score.
- **A comparison outlives the page.** Unlike a search (ADR-0034), closing the
  tab does not stop it, since it is minutes of work somebody comes back to;
  **Stop** ends it at the next `checkpoint`.
- **The page decides nothing.** `/api/state` carries every sentence and every
  order it shows, and its script writes text and never markup, since model
  output reaches it (ADR-0012).
- **The board is not a cache.** Nothing expires it. A run whose case's
  fingerprint -- request, pages, key, metrics -- no longer matches is left out,
  since it scored another case.
- **A kept run says what it was scored under**
  ([ADR-0075](adr/0075-say-what-a-kept-run-was-scored-under-and-compare-it-with-a-baseline.md)):
  the code it went through, its settings and its model's build. One made under
  other code or settings is kept and marked, since its counts are true of what
  made them; `--baseline` sets a run beside an earlier `--json` of it, which the
  board, keeping only the latest, cannot.
- **The scorer is stricter than grounding where the key can say so**
  ([ADR-0073](adr/0073-hold-the-scorer-to-the-model-number-and-the-verdicts-on-each-product.md),
  [ADR-0074](adr/0074-pay-the-score-nothing-for-silence-or-for-luck.md)): a model
  number tells two products apart, a quote counts only as a verdict on its own
  product, and the score pays nothing for silence or a shuffle's luck.

## A streamed run, end to end

```mermaid
---
config:
  sequence:
    mirrorActors: false
    actorMargin: 16
    width: 96
    wrap: true
---
sequenceDiagram
    autonumber
    box Browser
        actor S as Shopper
        participant UI as Web UI
    end
    box HTTP server
        participant H as Handler
        participant W as Worker thread
    end
    box Agent pipeline
        participant J as Journal
        participant A as BuyAgent
    end
    box External systems
        participant O as Model server
        participant D as Search backend
        participant P as Shop pages
    end

    S->>UI: "wireless headphones under $200"
    UI->>H: GET /api/search/stream?... (EventSource)
    H-->>UI: 200 text/event-stream
    H->>W: start the run, attach the log relay
    W->>J: open this search's journal, holding its last run
    W->>A: run(request, sort_by, checkpoint, record)
    A->>O: refine the query
    O-->>A: search query
    A-->>UI: event: log
    A->>D: search (once per named source, else once)
    D-->>A: up to 10 results, held to the named sources if any
    A->>P: fetch pages in parallel
    P-->>A: HTML, condensed to figures and verdicts
    A->>O: extract products (JSON schema)
    O-->>A: candidates
    A->>A: clean → ground → deduplicate → bounds → rank
    A->>W: record each candidate a step took out
    A-->>UI: event: log (top 3 report)
    A-->>W: ranked products
    W->>J: write this run down, compare it with the last
    J-->>W: what is cheaper, dearer, new or gone
    W-->>H: the products, what was taken out and what moved
    H-->>UI: event: result
    UI-->>S: the shortlist, best first
    Note over UI,W: A quiet stretch sends a ping event every 15s. A failed run sends a failure event carrying its HTTP status. A frame that cannot be written stops the run at its next checkpoint.
```

Where a run is deterministic, the page text and the model's answers come off
disk for as long as `cache_ttl` allows, rather than off the web and the model
(ADR-0040, ADR-0044).

Only query refinement is recoverable: it falls back to the raw request but lets
`ModelUnavailableError` through. `BuyAgent.run()` raises exactly `ValueError`,
`ModelUnavailableError` and `SearchError`, which `api._STATUS` maps to 400, 503
and 502; the provider writes the remedy the middle one carries.
