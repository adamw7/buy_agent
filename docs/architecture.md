# Architecture (C4)

The [C4 model](https://c4model.com) at three zoom levels, in Mermaid so GitHub
renders it. Why it is this way is in [adr/](adr/README.md), and the rules it
implies in `CLAUDE.md`. The idea through all three levels: **the LLM is not in
charge.** It refines the query and reads products out of pages; filtering,
grounding, ranking and ordering are Python.

Every diagram draws with one key:

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

Thick arrows are a run's steps, numbered in order. Solid ones are calls and
requests, with the technology in brackets where one leaves the process; dashed
ones are dependencies, what a part reads, builds or relies on. A dashed outline is
a boundary: the system, or the container whose parts are drawn.

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

    ollama("<b>Model server</b><br/><i>[External System]</i><br/>Ollama, vLLM, TensorRT-LLM<br/>or a LiteLLM proxy. Refines<br/>the query and extracts<br/>products, decoding under<br/>a JSON schema")
    ddg("<b>Search backend</b><br/><i>[External System]</i><br/>DuckDuckGo with no key, a<br/>SearXNG the shopper runs, or<br/>Brave on a key they hold")
    shops("<b>Shop and review pages</b><br/><i>[External System]</i><br/>The pages the search returns:<br/>the only source of prices,<br/>ratings and review counts")
    counterparty("<b>AP2 endpoint</b><br/><i>[External System]</i><br/>The merchant or credential<br/>provider the operator names;<br/>none is named in this project")

    shopper -->|"asks in their own<br/>words, and watches it<br/>become a shortlist<br/>[CLI or web browser]"| system
    operator -->|"compares a server's<br/>models over three<br/>fixed cases<br/>[CLI or web browser]"| system
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
fetches leave, plus the camera's second visit where a loopback server takes
pictures
([ADR-0065](adr/0065-photograph-each-products-page-from-a-server-bound-to-this-machine.md))
and a signed AP2 mandate where a run was asked to buy; the default rail charges
nobody ([ADR-0046](adr/0046-pay-on-the-shoppers-behalf-with-ap2.md)). Which model
server answers is `AgentConfig.provider`, and only `buy_agent/providers.py` knows
the difference ([ADR-0028](adr/0028-serve-the-model-from-ollama-or-vllm.md),
[ADR-0029](adr/0029-one-table-per-model-server.md),
[ADR-0068](adr/0068-reach-a-litellm-proxy-as-a-third-model-server.md),
[ADR-0081](adr/0081-reach-tensorrt-llm-as-a-fourth-model-server.md)). The
operator's way in, the benchmark, asks the model server and nothing else: every
model is served the same fixed pages
([ADR-0070](adr/0070-compare-local-models-and-give-the-comparison-a-page.md)).

## Level 2 -- Containers

One system, drawn once per person: the shop and the benchmark. Both drive the same
agent pipeline and keep what they keep in the same directory.

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
        spa("<b>Web UI</b><br/><i>[Container: Angular 22]</i><br/>A form, a live progress log<br/>and the ranked cards. Decides<br/>nothing: it renders what<br/>the API sends")
        cli("<b>CLI</b><br/><i>[Container: Python]</i><br/>A flag per API setting;<br/>runs one search, logs<br/>the top N")
        server("<b>HTTP server</b><br/><i>[Container: Python, stdlib]</i><br/>The built UI, the JSON API,<br/>and a run's log lines as<br/>Server-Sent Events")
        paying("<b>Paying</b><br/><i>[Container: Python library,<br/>optional]</i><br/>A cart from one grounded<br/>product, two signed AP2<br/>mandates and a rail. After<br/>the pipeline, never in it")
        pipeline("<b>Agent pipeline</b><br/><i>[Container: Python library]</i><br/>BuyAgent.run(): search,<br/>extract, ground, deduplicate,<br/>rank, for every front end")
        camera("<b>Camera</b><br/><i>[Container: Playwright,<br/>optional]</i><br/>One headless Chromium<br/>(screenshots.py)<br/>photographing a card's page,<br/>on a loopback server only")
        store[("<b>Cache directory</b><br/><i>[Container: JSON files]</i><br/>$BUY_AGENT_CACHE_DIR: the<br/>pages and answers a run may<br/>reuse, the journal of past<br/>runs, the benchmark's board")]
    end

    ollama("<b>Model server</b><br/><i>[External System]</i><br/>Ollama, vLLM,<br/>TensorRT-LLM or<br/>a LiteLLM proxy")
    ddg("<b>Search backend</b><br/><i>[External System]</i><br/>DuckDuckGo, SearXNG<br/>or Brave")
    shops("<b>Shop and review pages</b><br/><i>[External System]</i>")
    counterparty("<b>AP2 endpoint</b><br/><i>[External System]</i><br/>Whatever the operator<br/>names; none is named here")

    shopper -->|"visits localhost:8000<br/>[HTTP]"| spa
    shopper -->|"types a request<br/>[terminal]"| cli
    spa -->|"the JSON API, a run's<br/>event stream, card<br/>screenshots [HTTP, SSE]"| server
    cli -->|"calls run()"| pipeline
    server -->|"runs a search on a<br/>worker thread, relays<br/>its log lines"| pipeline
    cli -->|"pays, once a person<br/>approved this cart"| paying
    server -->|"POST /api/pay, with<br/>the page's approval"| paying
    server -->|"asks for a picture"| camera

    pipeline -->|"[HTTP]"| ollama
    pipeline -->|"[HTTPS, or HTTP<br/>to a SearXNG]"| ddg
    pipeline -->|"[HTTPS]"| shops
    pipeline -->|"pages and answers to<br/>reuse, and the journal"| store
    paying -->|"[HTTPS]"| counterparty
    camera -->|"[HTTPS]"| shops
    store ~~~ ollama & ddg & shops & counterparty

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef container fill:#2e74c0,stroke:#1d5390,color:#fff
    classDef external fill:#6b6b6b,stroke:#4d4d4d,color:#fff
    class shopper person
    class cli,spa,server,pipeline,paying,camera,store container
    class ollama,ddg,shops,counterparty external
    style system fill:none,stroke:#8c8c8c,stroke-width:1px,stroke-dasharray:6 4
```

The CLI and the server are two doors onto one `BuyAgent.run()` and one table of
settings: each `api.OPTIONS` row is a flag at one door and a request key at the
other, refused in the same words at both
([ADR-0071](adr/0071-build-the-cli-flags-off-the-options-table.md)). The server is
stdlib-only and admits every request before routing it, so the API answers its own
page and not other tabs
([ADR-0018](adr/0018-guard-the-loopback-server-against-other-pages.md)). Paying is
a container of its own that both doors reach and that reaches nothing back,
settling afterwards from a product the run already grounded
([ADR-0046](adr/0046-pay-on-the-shoppers-behalf-with-ap2.md)). The camera is
further out still: the pipeline never knows it exists, and as the one part that
starts a process, only a loopback server has one
([ADR-0065](adr/0065-photograph-each-products-page-from-a-server-bound-to-this-machine.md)).

What is kept between runs is JSON under `$BUY_AGENT_CACHE_DIR`: the page text and
answers a later run may reuse, which expire and are capped by size
([ADR-0040](adr/0040-cache-the-page-text-on-disk.md),
[ADR-0044](adr/0044-remember-a-deterministic-model-answer.md),
[ADR-0052](adr/0052-cap-the-cache-by-size-as-well-as-age.md)); the journal of what
past runs of a search reported
([ADR-0060](adr/0060-keep-a-run-journal-beside-the-cache-not-in-it.md)); and the
benchmark's board, written the journal's way. Neither of the last two expires.

The `Dockerfile` ships the shop's containers as one image, without the AP2 SDK,
the camera, the benchmark or the model server
([ADR-0015](adr/0015-package-the-web-tier-as-a-container.md)). Image and release
archive are stamped with the version they are, which both doors report
([ADR-0083](adr/0083-say-which-build-is-running.md)).

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
            benchpage("<b>Benchmark page</b><br/><i>[Container: HTML and<br/>JavaScript, no build]</i><br/>Pick a server, its models<br/>and the cases; read the<br/>standings as runs finish")
            bench("<b>Benchmark</b><br/><i>[Container: Python]</i><br/>python -m benchmark, or<br/>benchmark.server: runs models<br/>over three fixed cases, and<br/>scores, times and keeps<br/>each run")
        end
        pipeline("<b>Agent pipeline</b><br/><i>[Container: Python library]</i>")
        store[("<b>Cache directory</b><br/><i>[Container: JSON files]</i>")]
    end

    ollama("<b>Model server</b><br/><i>[External System]</i>")

    operator -->|"visits localhost:8100<br/>[HTTP]"| benchpage
    operator -->|"names models<br/>and cases<br/>[terminal]"| bench
    benchpage -->|"its JSON API, polled<br/>while a comparison runs<br/>[HTTP]"| bench
    bench -->|"calls run() over a<br/>case's own pages"| pipeline
    bench -->|"each contender's latest<br/>run of each case"| store
    pipeline -->|"[HTTP]"| ollama
    store ~~~ ollama

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef container fill:#2e74c0,stroke:#1d5390,color:#fff
    classDef external fill:#6b6b6b,stroke:#4d4d4d,color:#fff
    class operator person
    class benchpage,bench,pipeline,store container
    class ollama external
    style system fill:none,stroke:#8c8c8c,stroke-width:1px,stroke-dasharray:6 4
    style benchtier fill:none,stroke:#8c8c8c,stroke-width:1px,stroke-dasharray:3 3
```

The benchmark is a third way into the same `BuyAgent.run()`, for the operator
([ADR-0070](adr/0070-compare-local-models-and-give-the-comparison-a-page.md)).
Search and fetch are swapped out on `agent` for a case's own pages, so every model
reads the same text and none of it comes off the web. Its page is three static
files with no build step, served by a subclass of the shop's handler, so a request
is admitted, refused and answered exactly as the shop's are. Nothing in the
package imports it.

## Level 3 -- Components of the agent pipeline

Too many parts for one legible picture, so the pipeline is drawn four ways: the
run step by step, what a run is built from, the types its steps share, and paying,
which comes after it.

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
    agent("<b>BuyAgent</b><br/><i>[Component: agent.py]</i><br/>Orchestrates the fixed<br/>pipeline, hands each step its<br/>checkpoint, wait and recorder,<br/>times the steps, and words<br/>transport failures as a remedy")
    extraction("<b>Extraction</b><br/><i>[Component: extraction.py]</i><br/>Both prompts, plus<br/>name cleaning<br/>and merging of variant names")
    search("<b>Search</b><br/><i>[Component: search.py]</i><br/>One row per backend: where<br/>it listens, its key, how it is<br/>asked. Asks once more where<br/>it failed, then raises<br/>SearchError")
    sources("<b>Sources</b><br/><i>[Component: sources.py]</i><br/>A trusted source as a domain<br/>and a term: narrows the query,<br/>and says whether a result<br/>came from it")
    fetch("<b>Fetch</b><br/><i>[Component: fetch.py]</i><br/>Fetches pages in parallel,<br/>writes their JSON-LD as lines<br/>(structured.py), keeps the lines<br/>quoting a figure or stock and<br/>those passing judgement, asks<br/>a page that said to again once,<br/>and tallies the failures")
    verification("<b>Verification</b><br/><i>[Component: verification.py]</i><br/>Drops products no source<br/>named, blanks figures, stock,<br/>condition and quotes the text<br/>lacks, and links each product<br/>and quote to its page")
    constraints("<b>Constraints</b><br/><i>[Component: constraints.py]</i><br/>The shopper's bounds, after<br/>merging and before ranking;<br/>an unknown figure passes")
    ranking("<b>Ranking</b><br/><i>[Component: ranking.py]</i><br/>Weighted score over rating,<br/>popularity and price in one<br/>currency, and the shares it<br/>was blended from. No LLM")
    logsetup("<b>Report and logging</b><br/><i>[Component: logging_setup.py]</i><br/>The log format, the top-N<br/>report, what moved since the<br/>last run, and whether a price<br/>alert was met (alerts.py)")

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

    extraction -->|"its prompts, through<br/>chat.py<br/>[JSON schema, HTTP]"| ollama
    search -->|"[HTTPS, or HTTP<br/>to a SearXNG]"| ddg
    fetch -->|"[HTTPS]"| shops

    classDef component fill:#85bbf0,stroke:#4f86c0,color:#000
    classDef external fill:#6b6b6b,stroke:#4d4d4d,color:#fff
    class agent,extraction,search,sources,fetch,verification,constraints,ranking,logsetup component
    class ollama,ddg,shops external
```

Three joints in that order bear weight. `clean_products` runs **before** `ground`,
so a name wearing its publisher suffix ("... Review | AudioSite") passes the name
check. `ground` runs **before** `deduplicate`, so merging only combines grounded
figures and links, and moves them in pairs: `models.QUALIFIERS` ties currency to
price and review count to rating, and `_fill_gaps` moves whole groups (ADR-0022).
The bounds run **after** `deduplicate`, which may supply the price, and **before**
`rank_products`, since price scores relative to the set (ADR-0039). Extraction and
verification see the same text, which is why `fetch.enrich()` puts it on
`SearchResult`.

- **Step 2** is one search, or one per named source, pooled, deduplicated by URL
  and cut back to width; only `sources.py` knows the feature exists (ADR-0021,
  ADR-0027, ADR-0057).
- **Step 3** tallies how fetching failed ("7 refused (403), 2 timed out") at INFO,
  since a run that read nothing otherwise looks like a bad model.
- **Step 5** holds quotes to a stricter bar than figures: most overlapping
  five-word runs found on one page that mentions the product (ADR-0024, ADR-0025),
  under its own name there, never a rating or count restated without a judgement
  (ADR-0090). The quote keeps that page (ADR-0042), and each product is linked to
  the first searched page mentioning it (ADR-0017).
- **Step 7** passes unknown figures, and prices outside the run's one currency,
  which is never converted (ADR-0039, ADR-0043, ADR-0056).
- **Step 8** keeps the shares a score was blended from, so an assumed 0.5 is told
  from a measured one (ADR-0041).

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
        config("<b>AgentConfig</b><br/><i>[Component: config.py]</i><br/>Every setting of a run, its<br/>default and range, and what a<br/>region and a currency must<br/>look like, for both doors")
        boundsc("<b>Noticed bounds</b><br/><i>[Component: bounds.py]</i><br/>Reads &quot;under $200&quot; out of<br/>the request in plain Python;<br/>offered at both doors and<br/>applied at neither")
        agent("<b>BuyAgent</b><br/><i>[Component: agent.py]</i>")
        search("<b>Search</b><br/><i>[Component: search.py]</i>")
        fetch("<b>Fetch</b><br/><i>[Component: fetch.py]</i>")
        chat("<b>Chat</b><br/><i>[Component: chat.py]</i><br/>A prompt with the run's<br/>values, a chain binding it<br/>to a schema, and the answer<br/>read back as that schema")
        journal("<b>Journal</b><br/><i>[Component: journal.py]</i><br/>What past runs of this search<br/>reported, a name, price and<br/>currency each, so this one can<br/>say what is cheaper, dearer,<br/>new or gone. Bounded by count")
        providers("<b>Providers</b><br/><i>[Component: providers.py]</i><br/>One row per model server: the<br/>model, address and key it<br/>defaults to, its client and<br/>schema declaration, listing,<br/>settings it takes, the errors<br/>meaning &quot;not there&quot;, and<br/>what to say")
        version("<b>Version</b><br/><i>[Component: version.py]</i><br/>Which build is running: a<br/>release's stamp, else dev and<br/>the commit off .git's files,<br/>never by spawning git")
        cache("<b>Cache</b><br/><i>[Component: cache.py]</i><br/>A fetched page's text and the<br/>model's answer about it, kept<br/>for a day and capped by size.<br/>Every failure is a miss,<br/>never a failed run")
    end

    ollama("<b>Model server</b><br/><i>[External System]</i>")
    store[("<b>Cache directory</b><br/><i>[Container]</i>")]

    doors -->|"run(request, sort_by)"| agent
    doors -.->|"build it: a flag<br/>or a request key per<br/>api.OPTIONS row"| config
    doors -.->|"offer what the request<br/>asks for in words"| boundsc
    doors -.->|"write this run down and<br/>say what moved, keyed by<br/>what was asked"| journal
    doors -.->|"--version, /api/config,<br/>the Server header"| version
    config -.->|"configures"| agent
    config -.->|"search_backend"| search
    config -.->|"model_server: the<br/>model, address and<br/>key per provider"| providers
    agent ==>|"3. enrich"| fetch
    agent -->|"binds Extraction's prompts<br/>into chains and invokes them"| chat
    agent -.->|"builds the chat model,<br/>names the failure"| providers
    agent -.->|"reuses an answer the<br/>model gave before"| cache
    chat -->|"a prompt, a schema,<br/>one answer"| providers
    fetch -.->|"reads what it<br/>read last time"| cache
    providers -->|"prompts under a<br/>JSON schema [HTTP]"| ollama
    cache -->|"pages/ and answers/"| store
    journal -->|"runs/"| store

    classDef container fill:#2e74c0,stroke:#1d5390,color:#fff
    classDef component fill:#85bbf0,stroke:#4f86c0,color:#000
    classDef external fill:#6b6b6b,stroke:#4d4d4d,color:#fff
    class doors,store container
    class config,boundsc,agent,search,fetch,chat,journal,providers,version,cache component
    class ollama external
    style pipeline fill:none,stroke:#8c8c8c,stroke-width:1px,stroke-dasharray:6 4
```

Each table is reached one way: `AgentConfig` names the row (`model_server`,
`search_backend`, and `rail_used` for paying), and nothing above it branches on a
name (ADR-0029, ADR-0046, ADR-0057). The cache wraps the chat model only for a
deterministic run, while the journal is keyed by what was asked, never by the
model (ADR-0044, ADR-0060). The version is read once and touches nothing a run
does, so a bug report can say which build wrote it (ADR-0083).

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
        moneyc("<b>Money</b><br/><i>[Component: money.py]</i><br/>Every currency table: its<br/>spellings, what a page is<br/>scanned for, how an amount<br/>is written, its minor units")
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
nothing downstream rewords; it defaults to `models.nothing_recorded`. Steps that
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
        railsc("<b>Rails</b><br/><i>[Component: rails.py]</i><br/>One row per counterparty:<br/>where it listens, whether it<br/>needs an address and a key,<br/>whether it moves money, and<br/>what to say when unreachable")
        mandatesc("<b>Mandates</b><br/><i>[Component: mandates.py]</i><br/>The only module importing<br/>the AP2 SDK: signs Checkout<br/>and Payment Mandates bound to<br/>the signed checkout by hash,<br/>and checks an open mandate")
    end

    counterparty("<b>AP2 endpoint</b><br/><i>[External System]</i><br/>Whatever the operator<br/>names; none is named here")

    cli -->|"10. pay, once a person<br/>approved this cart"| payment
    server -->|"POST /api/pay, with<br/>the page's approval"| payment
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

Step 10 is outside the run's order: `BuyAgent.run` ends at the report, and paying
happens afterwards to one product a person or an open mandate approved. A price no
source printed, or in a currency the run cannot place, is never paid -- the
opposite of the bounds, which keep what they cannot judge (ADR-0046).

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
    operator(["<b>Orchestrator</b><br/><i>[Docker, Kubernetes, Prometheus]</i>"])

    subgraph spa["Web UI [Container: Angular]"]
        form("<b>SearchForm</b><br/><i>[Component: search-form]</i><br/>The request and every option,<br/>seeded from /api/config,<br/>and refused here first")
        app("<b>App</b><br/><i>[Component: app.ts]</i><br/>The run's state in signals:<br/>the top N and the rest, what<br/>moved and what the run took<br/>out, the build in its footer")
        log("<b>ProgressLog</b><br/><i>[Component: progress-log]</i><br/>The agent's log lines as<br/>they arrive, and a transcript<br/>for a failed or stopped run")
        card("<b>ProductCard</b><br/><i>[Component: product-card]</i><br/>One ranked product from the<br/>API's labels: its score's<br/>shares, each listing's price,<br/>a picture of its page, and<br/>two clicks to buy it")
        agentsvc("<b>AgentService</b><br/><i>[Component: agent.ts]</i><br/>HttpClient for the JSON<br/>endpoints; EventSource as<br/>an Observable, so<br/>unsubscribing is Stop")
    end

    subgraph srv["HTTP server [Container: Python]"]
        handler("<b>BuyAgentHandler</b><br/><i>[Component: server.py]</i><br/>Admits the request, then<br/>routes /api to the API and<br/>the rest to the built app,<br/>falling back to index.html")
        guard("<b>_refused</b><br/><i>[Component: server.py]</i><br/>Refuses a request another<br/>site's page made, and a<br/>Host that merely resolves<br/>here")
        relay("<b>_LogRelay</b><br/><i>[Component: server.py]</i><br/>A handler on the package<br/>logger, routing records by<br/>the run's context, threads<br/>included, so two runs never<br/>see each other's progress")
        deploy("<b>Policy and Traffic</b><br/><i>[Component: deployment.py]</i><br/>What other machines add:<br/>the token, the addresses it<br/>may ask, the caps on runs<br/>and connections, the drain<br/>on SIGTERM, /metrics")
        api("<b>API</b><br/><i>[Component: api.py]</i><br/>OPTIONS, the settings both<br/>doors read. A request into an<br/>AgentConfig, a run into JSON<br/>with what it took out and what<br/>moved, a failure into 400 / 502<br/>/ 503; re-ranks or pays for a<br/>finished run without a new one")
    end

    cli("<b>CLI</b><br/><i>[Container]</i>")
    agentpipeline("<b>Agent pipeline</b><br/><i>[Container]</i>")
    paying("<b>Paying</b><br/><i>[Container]</i>")
    camera("<b>Camera</b><br/><i>[Container]</i>")

    browserUser --> form
    form -->|"submit, the paying settings<br/>as they change, and what to<br/>check: the request's bounds,<br/>the sources, the model server"| app
    app --> log
    app --> card
    card -->|"pay, on the<br/>second click"| app
    app -->|"the defaults, search, rank<br/>and pay, and every reading<br/>the form asks for"| agentsvc
    agentsvc -->|"GET /api/search/stream<br/>[SSE: log, result,<br/>failure, ping]"| handler
    agentsvc -->|"GET /api/config,<br/>/api/models, /api/sources,<br/>/api/bounds<br/>POST /api/rank, /api/pay<br/>[JSON]"| handler
    card -->|"GET /api/screenshot,<br/>from an img [JPEG]"| handler

    handler -->|"before any routing"| guard
    guard -->|"the token"| deploy
    handler -->|"admit_run, the counts,<br/>the drain"| deploy
    operator -->|"/healthz, /readyz,<br/>/metrics, SIGTERM"| handler
    handler -->|"parse_options, run_search,<br/>rank_again, pay_now,<br/>screenshot, the readings"| api
    handler -->|"reads the queue<br/>for this run"| relay
    cli -.->|"a flag per OPTIONS row,<br/>run_agent; --json is<br/>results_payload"| api
    api -->|"agent_factory(config)<br/>.run(), then what moved<br/>[worker thread]"| agentpipeline
    api -->|"cart_for,<br/>then pay_for"| paying
    api -->|"camera.shoot(url)"| camera

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef container fill:#2e74c0,stroke:#1d5390,color:#fff
    classDef component fill:#85bbf0,stroke:#4f86c0,color:#000
    class browserUser,operator person
    class cli,agentpipeline,paying,camera container
    class app,form,log,card,agentsvc,handler,guard,relay,deploy,api component
    style spa fill:none,stroke:#8c8c8c,stroke-width:1px,stroke-dasharray:6 4
    style srv fill:none,stroke:#8c8c8c,stroke-width:1px,stroke-dasharray:6 4
```

Deliberate details, each a rule in `CLAUDE.md`:

- **A run is streamed** over `GET /api/search/stream`; `POST /api/search` is the
  same run in one response, for scripts. Closing the stream stops the run at its
  next `checkpoint`, though a chat call in flight finishes (ADR-0034), and the
  failure event is `failure`, not `error`, on which `EventSource` would reconnect
  and search again.
- **Re-ordering and paying run nothing.** `POST /api/rank` calls only
  `rank_products` on the products and currency the page holds (ADR-0035,
  ADR-0056); `POST /api/pay` builds the cart on the server, and the page's echo of
  title, price and currency must match it (ADR-0012, ADR-0046). `dropped` and
  `changes` travel with the result and survive a re-sort, which reports neither of
  its own (ADR-0055, ADR-0060).
- **The browser decides nothing.** Labels such as `price_label` are Python's, and
  `sort_by` is a request even for a finished run. A picture is the card's own
  `<img>` on `/api/screenshot`, only where `/api/config` says this server has a
  camera and the product links somewhere; it loads lazily, and a failed one drops
  its frame (ADR-0065).
- **One table of settings, two doors**, so both refuse the same values in the same
  words (ADR-0033, ADR-0071).
- **Loopback is not a boundary the browser respects**, so `_refused` turns away
  cross-site requests and foreign `Host`s before routing (ADR-0018). **Other
  machines are a boundary of their own** (ADR-0092): the probes answer before any
  check and everything else asks for the token once one is set; bound beyond
  loopback, the server asks only the model server and merchant it was started
  with; a re-sort or payment must bring back the run's `seal`; runs past
  `--max-runs` are a 503; and SIGTERM drains what is in flight.

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
        baseline("<b>Baseline</b><br/><i>[Component: baseline.py]</i><br/>--baseline: each run beside<br/>the same contender's run in<br/>an earlier --json, metric<br/>by metric")
        handler("<b>BenchmarkHandler</b><br/><i>[Component: server.py]</i><br/>The shop's handler,<br/>subclassed: the shop's<br/>refusals, then the<br/>benchmark's endpoints")
        bench("<b>Bench</b><br/><i>[Component: server.py]</i><br/>One comparison at a time,<br/>on a thread of its own that<br/>closing the page does not<br/>stop; Stop ends it at the<br/>next step")
        compare("<b>Comparison</b><br/><i>[Component: compare.py]</i><br/>A contender is a served model<br/>or a script. Times each<br/>question, scores an unreadable<br/>answer 0, raises for a model<br/>that cannot be asked, and<br/>ranks the standings")
        cases("<b>Cases</b><br/><i>[Component: cases.py,<br/>corpus.py, answers.py,<br/>scripted.py, laptops.py,<br/>espresso.py]</i><br/>headphones, laptops,<br/>espresso: a request, its<br/>pages, the key to what they<br/>print, and a perfect and<br/>a sloppy script")
        runner("<b>Runner</b><br/><i>[Component: runner.py]</i><br/>BuyAgent over a case's own<br/>pages: search_web and enrich<br/>swapped out on agent, the<br/>text condensed by the real<br/>fetch.condense")
        scoring("<b>Scoring</b><br/><i>[Component: scoring.py,<br/>query.py]</i><br/>Eight shares in [0, 1]<br/>against the case's key,<br/>product by product, weighed<br/>pair by pair; the query<br/>judged apart, as checks")
        scoredunder("<b>Scored under</b><br/><i>[Component: pipeline.py]</i><br/>A fingerprint of the code<br/>between the pages and the<br/>scorecard, and the settings<br/>that reach the model")
        board("<b>Board</b><br/><i>[Component: board.py]</i><br/>Each contender's latest run<br/>of each case, in board.json,<br/>read afresh on every look;<br/>a run against changed pages<br/>is left out, one under other<br/>code kept and marked")
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
  question is really asked (ADR-0044), and a contender is reached the way a run
  reaches one, `config.model_server.chat_model(config)` (ADR-0029), with a
  `Stopwatch` round each of the two questions.
- **An unreadable answer is a result; a model that cannot be asked is not.** The
  first scores 0 and is kept; the second is reported in its server's own words and
  its remaining cases skipped (ADR-0009). The query is scored apart, since it
  moves the search, not what the pages say.
- **A comparison outlives the page.** Unlike a search (ADR-0034), closing the tab
  does not stop minutes of work somebody comes back to; **Stop** ends it at the
  next `checkpoint`.
- **The page decides nothing.** `/api/state` carries every sentence and order it
  shows, and its script writes text, never markup, since model output reaches it
  (ADR-0012).
- **The board is not a cache.** Nothing expires it, and a run whose case's
  fingerprint (request, pages, key, metrics) no longer matches is left out, since
  it scored another case. A run made under other code or settings is kept and
  marked, its counts true of what made them
  ([ADR-0075](adr/0075-say-what-a-kept-run-was-scored-under-and-compare-it-with-a-baseline.md));
  `--baseline` sets a run beside an earlier `--json` of it, which the board,
  keeping only the latest, cannot.
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
    A-->>UI: event: log (the model it asks, at which address)
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
    A-->>UI: event: log (Finished in ..., then each step)
    A-->>W: ranked products
    W->>J: write this run down, compare it with the last
    J-->>W: what is cheaper, dearer, new or gone
    W-->>H: the products, what was taken out and what moved
    H-->>UI: event: result
    UI-->>S: the shortlist, best first
    Note over UI,W: A quiet stretch sends a ping event every 15s. A failed run sends a failure event carrying its HTTP status. A frame that cannot be written stops the run at its next checkpoint.
```

Every run that returns names the model it asked and ends with how long it took,
the whole then each step, timed in `agent.py` and written by
`logging_setup.seconds_label`, which the benchmark shares (ADR-0084). Where a run
is deterministic, page text and the model's answers come off disk for as long as
`cache_ttl` allows (ADR-0040, ADR-0044). Only query refinement is recoverable,
falling back to the raw request; otherwise `BuyAgent.run()` raises exactly
`ValueError`, `ModelUnavailableError` or `SearchError`, which `api._STATUS` maps
to 400, 503 and 502, the provider writing the remedy the middle one carries.
