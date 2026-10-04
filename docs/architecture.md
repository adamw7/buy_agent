# Architecture (C4)

The [C4 model](https://c4model.com) at three zoom levels, in Mermaid so GitHub
renders it. Why it is this way is in [adr/](adr/README.md). The idea through all
three levels: **the LLM is not in charge.** It refines the query and reads
products out of pages; filtering, grounding, ranking and ordering are Python.

## Level 1 -- System context

```mermaid
graph TB
    shopper["<b>Shopper</b><br/><i>[Person]</i><br/>Wants to buy something and<br/>would rather not read ten<br/>listicles first"]
    operator["<b>Operator</b><br/><i>[Person]</i><br/>Runs the model server, and<br/>chooses which of its models<br/>the agent should ask"]

    system["<b>buy_agent</b><br/><i>[Software System]</i><br/>Turns a plain-language request into<br/>a ranked shortlist of real products,<br/>each figure backed by a source page,<br/>and scores which local model<br/>does that best"]

    ollama["<b>Model server</b><br/><i>[External System]</i><br/>A local Ollama, a vLLM, or a LiteLLM proxy<br/>routing to whatever its owner chose -- the<br/>last two behind an OpenAI-compatible API.<br/>Refines the query and extracts products,<br/>under a JSON schema that constrains decoding"]
    ddg["<b>Search backend</b><br/><i>[External System]</i><br/>DuckDuckGo with no key, a SearXNG<br/>the shopper runs, or Brave on a key<br/>they hold -- one row each"]
    shops["<b>Shop and review pages</b><br/><i>[External System]</i><br/>The pages the search returns;<br/>the only source of prices,<br/>ratings and review counts"]
    counterparty["<b>AP2 endpoint</b><br/><i>[External System]</i><br/>Whatever merchant or credential provider<br/>the operator names, reached only by a<br/>run that was asked to buy. None is<br/>named in this project"]

    shopper -->|"asks for a product, in their own words,<br/>and watches it become a ranked shortlist<br/>[CLI or web browser]"| system
    operator -->|"compares the models a server holds<br/>over three fixed shopping cases,<br/>and reads how they ranked<br/>[CLI or web browser]"| system
    system -->|"prompts with a JSON schema<br/>[HTTP, :11434, :8000/v1 or :4000/v1]"| ollama
    system -->|"searches<br/>[HTTPS, or HTTP to a SearXNG]"| ddg
    system -->|"fetches and condenses<br/>[HTTPS]"| shops
    system -.->|"photographs, for the card<br/>that links to it -- optional<br/>[headless Chromium]"| shops
    system -->|"presents a signed mandate,<br/>only when asked to buy<br/>[HTTPS]"| counterparty

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef internal fill:#1168bd,stroke:#0b4884,color:#fff
    classDef external fill:#999,stroke:#6b6b6b,color:#fff
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

```mermaid
graph TB
    shopper["<b>Shopper</b><br/><i>[Person]</i>"]
    operator["<b>Operator</b><br/><i>[Person]</i>"]

    subgraph system["buy_agent"]
        cli["<b>CLI</b><br/><i>[Container: Python]</i><br/>python -m buy_agent 'gaming laptop'<br/>A flag per setting the API takes,<br/>filling one AgentConfig; runs one<br/>search, logs the top N"]
        spa["<b>Web UI</b><br/><i>[Container: Angular 22, TypeScript]</i><br/>A form, a live progress log and<br/>the ranked cards. Decides nothing:<br/>it renders what the API sends"]
        server["<b>HTTP server</b><br/><i>[Container: Python, stdlib http.server]</i><br/>Serves the built UI and the JSON API,<br/>and relays a run's log lines as<br/>Server-Sent Events"]
        pipeline["<b>Agent pipeline</b><br/><i>[Container: Python library]</i><br/>BuyAgent.run() -- search, extract,<br/>ground, deduplicate, rank.<br/>The one implementation every<br/>front end drives"]
        paying["<b>Paying</b><br/><i>[Container: Python library, optional]</i><br/>A cart out of one grounded product,<br/>two signed AP2 mandates, and the rail<br/>they are presented to. Runs after the<br/>pipeline, never inside it"]
        camera["<b>Camera</b><br/><i>[Container: Playwright + headless Chromium, optional]</i><br/>One browser on one thread, taking a<br/>picture of the page a card links to.<br/>Only on a server bound to this machine"]
        store[("<b>Cache directory</b><br/><i>[Container: JSON files on disk]</i><br/>Under $BUY_AGENT_CACHE_DIR: the<br/>pages and answers a run may reuse,<br/>the journal of past runs, and the<br/>benchmark's board")]

        subgraph benchtier["benchmark/ -- in the repository, not in the image"]
            bench["<b>Benchmark</b><br/><i>[Container: Python]</i><br/>python -m benchmark, or<br/>python -m benchmark.server on :8100.<br/>Runs models over three fixed cases,<br/>scores and times each run, keeps it"]
            benchpage["<b>Benchmark page</b><br/><i>[Container: HTML and JavaScript, no build]</i><br/>Pick a server, its models and the<br/>cases; read the standings as each<br/>run finishes. Decides nothing either"]
        end
    end

    ollama["<b>Model server</b><br/><i>[External System]</i><br/>Ollama, vLLM or a LiteLLM proxy"]
    ddg["<b>Search backend</b><br/><i>[External System]</i><br/>DuckDuckGo, SearXNG or Brave"]
    shops["<b>Shop and review pages</b><br/><i>[External System]</i>"]
    counterparty["<b>AP2 endpoint</b><br/><i>[External System]</i><br/>Whatever merchant or credential<br/>provider the operator names. None<br/>is named in this project"]

    shopper -->|"types a request<br/>[terminal]"| cli
    shopper -->|"visits localhost:8000<br/>[HTTP]"| spa
    operator -->|"names the models and cases<br/>[terminal]"| bench
    operator -->|"visits localhost:8100<br/>[HTTP]"| benchpage

    spa -->|"GET /api/config, /api/models, /api/sources, /api/bounds<br/>POST /api/rank, /api/pay<br/>GET /api/search/stream (SSE)<br/>[JSON over HTTP]"| server
    spa -->|"GET /api/screenshot, from an img<br/>[JPEG over HTTP]"| server
    server -->|"serves index.html and assets<br/>[HTTP]"| spa
    cli -->|"calls run()"| pipeline
    server -->|"runs a search in a worker thread,<br/>relays its log records"| pipeline

    benchpage -->|"GET /api/config, /api/models,<br/>/api/state each second while one runs<br/>POST /api/run, /api/stop, /api/clear<br/>[JSON over HTTP]"| bench
    bench -->|"serves index.html, app.js<br/>and style.css [HTTP]"| benchpage
    bench -->|"calls run() over a case's own pages,<br/>search and fetch swapped out"| pipeline

    cli -->|"pays, once a person<br/>approved this cart"| paying
    server -->|"POST /api/pay, with the<br/>approval the page witnessed"| paying
    server -->|"asks for a picture,<br/>waits for it"| camera

    pipeline -->|"[HTTP]"| ollama
    pipeline -->|"[HTTPS, or HTTP to a SearXNG]"| ddg
    pipeline -->|"[HTTPS]"| shops
    pipeline -->|"pages and answers to reuse,<br/>and the journal [files]"| store
    bench -->|"keeps each contender's latest<br/>run of each case [file]"| store
    paying -->|"[HTTPS]"| counterparty
    camera -->|"[HTTPS]"| shops

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef container fill:#438dd5,stroke:#2e6295,color:#fff
    classDef external fill:#999,stroke:#6b6b6b,color:#fff
    class shopper,operator person
    class cli,spa,server,pipeline,paying,camera,store,bench,benchpage container
    class ollama,ddg,shops,counterparty external
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

The benchmark is a third way into the same `BuyAgent.run()`, for the operator
rather than the shopper
([ADR-0070](adr/0070-compare-local-models-and-give-the-comparison-a-page.md)).
Search and fetch are swapped out on `agent` for a case's own pages, so every
model reads the same text and none of it comes off the web. Its page is three
static files with no build step, served by a subclass of the shop's handler, so
a request is admitted, refused and answered exactly as the shop's are. Nothing
in the package imports it.

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

## Level 3 -- Components of the agent pipeline

```mermaid
graph TB
    cli["<b>CLI</b><br/><i>[Container]</i>"]
    server["<b>HTTP server</b><br/><i>[Container]</i>"]

    subgraph pipeline["Agent pipeline"]
        agent["<b>BuyAgent</b><br/><i>[Component: agent.py]</i><br/>Orchestrates the fixed pipeline,<br/>hands each step the checkpoint, the<br/>wait and the recorder, and translates<br/>transport failures into an<br/>actionable message"]
        config["<b>AgentConfig</b><br/><i>[Component: config.py]</i><br/>Every setting of a run and its<br/>default, the range each number is<br/>held to, and what a region and a<br/>currency must look like -- the rules<br/>both doors' options are read by"]
        providers["<b>Providers</b><br/><i>[Component: providers.py]</i><br/>Everything that differs between<br/>Ollama, vLLM and a LiteLLM proxy, one<br/>row each: the model, address and key<br/>it defaults to, the client and how it<br/>declares a schema, the listing, which<br/>of the two settings it takes per<br/>request, the errors that mean<br/>&quot;not there&quot;, and what to say"]
        chat["<b>Chat</b><br/><i>[Component: chat.py]</i><br/>A prompt with the run's values in it,<br/>a chain binding one to a schema, and<br/>the answer read back as that schema<br/>-- or refused"]
        extraction["<b>Extraction</b><br/><i>[Component: extraction.py]</i><br/>Both prompts and both chains,<br/>plus name cleaning and merging<br/>of variant names"]
        search["<b>Search</b><br/><i>[Component: search.py]</i><br/>Which backend a search is asked<br/>through, one row each: where it<br/>listens, its key, how it is asked and<br/>what it answers. Asks once more where<br/>the backend failed, and raises<br/>SearchError when that one does too"]
        sources["<b>Sources</b><br/><i>[Component: sources.py]</i><br/>Reads a trusted source down to a<br/>domain and a term, narrows the<br/>query to it, and says whether a<br/>result came from it"]
        fetch["<b>Fetch</b><br/><i>[Component: fetch.py]</i><br/>Fetches result pages in parallel and<br/>keeps the lines quoting a figure and<br/>the lines passing judgement, each<br/>on a budget of its own; asks a page<br/>that said to come back once more;<br/>tallies how the rest failed"]
        cache["<b>Cache</b><br/><i>[Component: cache.py]</i><br/>What a run can reuse: the text of a<br/>fetched page, and the answer a model<br/>gave about it. Kept on disk for a<br/>day, and bounded by size as well as<br/>age. Best-effort: every failure is a<br/>miss, never a failed run"]
        journal["<b>Journal</b><br/><i>[Component: journal.py]</i><br/>What past runs of this same search<br/>reported -- a name, a price and a<br/>currency each -- so this one can say<br/>what is cheaper, dearer, new or<br/>gone. Bounded by a count and never<br/>by an age, and one setting off"]
        boundsc["<b>Noticed bounds</b><br/><i>[Component: bounds.py]</i><br/>Reads &quot;under $200&quot; out of the<br/>request in ordinary Python, and<br/>answers the figure and the words it<br/>read. Offered at both doors and<br/>applied at neither"]
        verification["<b>Verification</b><br/><i>[Component: verification.py]</i><br/>Drops products the sources never<br/>named, blanks any figure and any<br/>quote the page text does not<br/>contain, and links each product --<br/>and each quote -- to the page it<br/>came off"]
        constraints["<b>Constraints</b><br/><i>[Component: constraints.py]</i><br/>The shopper's bounds -- max price,<br/>min rating, min reviews -- applied<br/>after merging and before ranking.<br/>An unknown figure is not a violation"]
        ranking["<b>Ranking</b><br/><i>[Component: ranking.py]</i><br/>Weighted score over rating,<br/>popularity and price -- prices<br/>compared inside one currency -- and<br/>the shares it was blended from. No LLM"]
        models["<b>Models</b><br/><i>[Component: models.py]</i><br/>ExtractedProduct (sentinels, for the<br/>LLM's schema) vs Product (None), the<br/>Offer each page priced it at, and the<br/>Removal a step hands over when it<br/>takes a candidate out"]
        moneyc["<b>Money</b><br/><i>[Component: money.py]</i><br/>Every currency table: which<br/>spellings are one currency, which<br/>ones a page is scanned for, how an<br/>amount is written and how many<br/>minor units it comes to"]
        logsetup["<b>Report and logging</b><br/><i>[Component: logging_setup.py]</i><br/>Log format, the top-N report the<br/>browser also reads as events, and<br/>what moved since the last run,<br/>for --compare"]
    end

    subgraph paying["Paying -- optional, off, and after the run"]
        payment["<b>Payment</b><br/><i>[Component: payment.py]</i><br/>What may be bought and for how<br/>much: a cart out of a grounded<br/>product, the spend limit, and the<br/>receipt. One failure, PaymentError"]
        mandatesc["<b>Mandates</b><br/><i>[Component: mandates.py]</i><br/>The only module that imports the<br/>AP2 SDK. Signs the Checkout and<br/>Payment Mandates, bound to the<br/>merchant&#39;s signed checkout by hash,<br/>and checks an open mandate&#39;s<br/>constraints"]
        railsc["<b>Rails</b><br/><i>[Component: rails.py]</i><br/>Who the payment goes through, one<br/>row each: where it listens, whether<br/>it needs an address and an enrolled<br/>key, whether it moves money, and<br/>what to say when it cannot be<br/>reached"]
    end

    ollama["<b>Model server</b><br/><i>[External System]</i><br/>Ollama, vLLM or a LiteLLM proxy"]
    ddg["<b>Search backend</b><br/><i>[External System]</i><br/>DuckDuckGo, SearXNG or Brave"]
    shops["<b>Shop and review pages</b><br/><i>[External System]</i>"]
    counterparty["<b>AP2 endpoint</b><br/><i>[External System]</i><br/>Whatever merchant or credential<br/>provider the operator names. None<br/>is named in this project"]

    cli -->|"run(request, sort_by)"| agent
    server -->|"run(request, sort_by)"| agent
    cli -.->|"builds, one flag per<br/>api.OPTIONS row"| config
    server -.->|"builds from the request,<br/>one key per row"| config
    config -.->|"configures"| agent

    extraction -.->|"a prompt, a schema,<br/>one answer"| chat
    chat -.->|"asks the server the<br/>provider built"| providers

    agent -->|"1. refine the query"| extraction
    agent -->|"2. search -- once, or once<br/>per named source"| search
    agent -.->|"narrows the query,<br/>then holds the results to it"| sources
    agent -->|"3. enrich the results"| fetch
    agent -->|"4. extract products"| extraction
    agent -->|"5. clean, then ground<br/>against the same text"| verification
    agent -->|"6. deduplicate"| extraction
    agent -->|"7. hold to the<br/>shopper's bounds"| constraints
    agent -->|"8. rank"| ranking
    agent -->|"9. log the top N"| logsetup
    agent -.->|"builds the chat model,<br/>names the failure"| providers
    config -.->|"model_server: the model, the<br/>address and the key per provider"| providers
    config -.->|"search_backend: which row<br/>a search is asked through"| search

    providers -->|"prompts under a JSON schema<br/>[HTTP]"| ollama
    search -->|"[HTTPS]"| ddg
    fetch -->|"[HTTPS]"| shops
    cli -->|"10. pay, once a person<br/>approved this cart"| payment
    server -->|"POST /api/pay, with the<br/>approval the page witnessed"| payment
    payment -.->|"signs the two mandates"| mandatesc
    payment -.->|"config.rail_used: the<br/>checkout, then the settlement"| railsc
    railsc -.->|"signs its own checkout<br/>on the dry run"| mandatesc
    railsc -->|"[HTTPS]"| counterparty
    payment -.->|"only a grounded price<br/>may be paid"| models
    config -.->|"rail_used: where a payment<br/>goes and what it needs"| railsc
    fetch -.->|"reads what it read<br/>last time"| cache
    agent -.->|"reuses what the model<br/>answered last time"| cache
    cli -.->|"offers what the request<br/>asked for in words"| boundsc
    server -.->|"GET /api/bounds, which<br/>runs nothing"| boundsc
    cli -.->|"writes this run down, then<br/>says what moved"| journal
    server -.->|"the same, beside the<br/>products it answers with"| journal
    agent -.->|"keys a journal by what<br/>was asked, not how"| journal
    extraction -.->|"ExtractedProduct → Product;<br/>a Removal per headline,<br/>fold and nameless entry"| models
    verification -.->|"a Removal per product<br/>no page named"| models
    constraints -.->|"a Removal per product<br/>the bounds would not have"| models
    ranking -.-> models
    models -.->|"places the spelling a<br/>listing named"| moneyc
    fetch -.->|"scans for the spellings it<br/>can place"| moneyc
    payment -.->|"counts the cart in<br/>minor units"| moneyc

    classDef container fill:#438dd5,stroke:#2e6295,color:#fff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000
    classDef external fill:#999,stroke:#6b6b6b,color:#fff
    class cli,server container
    class agent,config,providers,chat,extraction,search,sources,fetch,cache,journal,boundsc,verification,constraints,ranking,models,moneyc,logsetup component
    class payment,mandatesc,railsc component
    class ollama,ddg,shops,counterparty external
```

Three joints in that order are load-bearing:

- `clean_products` runs **before** `ground`, so a name wearing its publisher
  suffix ("... Review | AudioSite") does not fail the name check.
- `ground` runs **before** `deduplicate`, so merging only combines figures and
  links the sources back.
- The bounds run **after** `deduplicate`, which may supply the price, and
  **before** `rank_products`, since price scores relative to the set (ADR-0039).

Step 10 is outside that order: `BuyAgent.run` ends at the report, and paying
happens afterwards to one product a person or an open mandate approved. A price
no source printed, or in a currency the run cannot place, is never paid -- the
opposite of the bounds, which keep what they cannot judge (ADR-0046).

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

The five heuristics that take a whole candidate out (`clean_products`,
`drop_ungrounded`, `merge_variants`, `deduplicate`'s nameless drop and
`Constraints.apply`) also call `record` with a `models.Removal`, whose sentence
nothing downstream rewords. It defaults to `models.nothing_recorded`. Steps that
only blank something record nothing (ADR-0055).

## Level 3 -- Components of the web tier

```mermaid
graph TB
    browserUser["<b>Shopper</b><br/><i>[Person]</i>"]

    subgraph spa["Web UI [Angular]"]
        app["<b>App</b><br/><i>[Component: app.ts]</i><br/>Holds the run's state in signals;<br/>splits the answer into the top N<br/>and the rest, and folds under it<br/>what moved since the last run<br/>and what the run took out"]
        form["<b>SearchForm</b><br/><i>[Component: search-form]</i><br/>The request and every option,<br/>seeded from /api/config,<br/>and refused here first"]
        log["<b>ProgressLog</b><br/><i>[Component: progress-log]</i><br/>The agent's own log lines, as they<br/>arrive, and a transcript to download<br/>for a run that failed or was stopped"]
        card["<b>ProductCard</b><br/><i>[Component: product-card]</i><br/>One ranked product, drawn from<br/>the labels the API sent: the shares<br/>its score was blended from, each<br/>listing's price, a picture of its<br/>page, and two clicks to buy it"]
        agentsvc["<b>AgentService</b><br/><i>[Component: agent.ts]</i><br/>HttpClient for the JSON endpoints;<br/>wraps EventSource as an Observable,<br/>so unsubscribing is the Stop button"]
    end

    subgraph srv["HTTP server [Python]"]
        handler["<b>BuyAgentHandler</b><br/><i>[Component: server.py]</i><br/>Admits the request, then routes /api<br/>to the API and everything else to the<br/>built app, falling back to index.html"]
        guard["<b>_refused</b><br/><i>[Component: server.py]</i><br/>Refuses a request another site's page<br/>made, and a Host that merely resolves<br/>here -- loopback is not a boundary<br/>the browser respects"]
        relay["<b>_LogRelay</b><br/><i>[Component: server.py]</i><br/>A logging handler that fans records<br/>out by the context a run is watched<br/>through, so two concurrent runs never<br/>see each other's progress and a step<br/>with threads of its own still reaches<br/>the right stream"]
        api["<b>API</b><br/><i>[Component: api.py]</i><br/>OPTIONS, the settings both doors<br/>read. Coerces a request into an<br/>AgentConfig, runs the pipeline,<br/>collects what it took out and what<br/>moved, shapes products as JSON,<br/>maps each failure to a status<br/>(400 / 502 / 503); re-ranks or pays<br/>for a finished run without running one"]
    end

    cli["<b>CLI</b><br/><i>[Container]</i>"]
    agentpipeline["<b>Agent pipeline</b><br/><i>[Container]</i>"]
    paying["<b>Paying</b><br/><i>[Container]</i>"]
    camera["<b>Camera</b><br/><i>[Container]</i>"]

    browserUser --> form
    form -->|"submit, and what to check:<br/>the request's bounds, the<br/>sources, the model server"| app
    app --> log
    app --> card
    card -->|"pay, on the second click"| app
    app -->|"the defaults, search, rank and pay,<br/>and every reading the form asks for"| agentsvc
    agentsvc -->|"GET /api/search/stream<br/>[SSE: log, result, failure, ping]"| handler
    agentsvc -->|"GET /api/config, /api/models, /api/sources, /api/bounds<br/>POST /api/rank, /api/pay<br/>[JSON]"| handler
    card -->|"GET /api/screenshot, from an img<br/>[JPEG]"| handler

    handler -->|"before any routing"| guard
    handler -->|"parse_options, run_search, rank_again,<br/>pay_now, screenshot and the readings"| api
    handler -->|"reads the queue for this run"| relay
    cli -.->|"a flag per OPTIONS row;<br/>--json is results_payload"| api
    api -->|"agent_factory(config).run(), then<br/>what moved [worker thread]"| agentpipeline
    api -->|"cart_for, then pay_for"| paying
    api -->|"camera.shoot(url)"| camera
    agentpipeline -.->|"log records"| relay

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef container fill:#438dd5,stroke:#2e6295,color:#fff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000
    class browserUser person
    class cli,agentpipeline,paying,camera container
    class app,form,log,card,agentsvc,handler,guard,relay,api component
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
graph TB
    operator["<b>Operator</b><br/><i>[Person]</i>"]
    benchpage["<b>Benchmark page</b><br/><i>[Container: benchmark/web]</i><br/>Draws what /api/state answers,<br/>sends what the form says, and<br/>writes text, never markup"]

    subgraph benchmark["Benchmark [Python, not shipped]"]
        commandline["<b>Command line</b><br/><i>[Component: __main__.py]</i><br/>python -m benchmark: every contender<br/>over every case named, the standings<br/>printed, and --json the page's<br/>own payload"]
        handler["<b>BenchmarkHandler</b><br/><i>[Component: server.py]</i><br/>The shop's handler, subclassed:<br/>it refuses what the shop's does,<br/>then routes to the benchmark's<br/>endpoints in place of the shop's"]
        bench["<b>Bench</b><br/><i>[Component: server.py]</i><br/>One comparison at a time, on a<br/>thread of its own: closing the page<br/>does not stop it, and Stop ends it<br/>at the next step"]
        compare["<b>Comparison</b><br/><i>[Component: compare.py]</i><br/>A contender is a model on a server<br/>or a script. Times each question,<br/>keeps an unreadable answer as a 0,<br/>raises for a model that cannot be<br/>asked, and ranks the standings"]
        runner["<b>Runner</b><br/><i>[Component: runner.py]</i><br/>Runs BuyAgent over a case's own<br/>pages: search_web and enrich swapped<br/>out on agent, the text condensed by<br/>the real fetch.condense"]
        cases["<b>Cases</b><br/><i>[Component: cases.py, one module each]</i><br/>headphones, laptops, espresso: a<br/>request, its pages, the key to what<br/>they print, and a perfect and a<br/>sloppy script"]
        scoring["<b>Scoring</b><br/><i>[Component: scoring.py, query.py]</i><br/>Eight shares in [0, 1] against the<br/>case's key, and the query judged<br/>apart, as checks with sentences"]
        board["<b>Board</b><br/><i>[Component: board.py]</i><br/>Each contender's latest run of each<br/>case, read afresh on every look; a<br/>run scored against pages since<br/>changed is left out"]
    end

    shophandler["<b>BuyAgentHandler</b><br/><i>[Component of the HTTP server]</i>"]
    pipeline["<b>Agent pipeline</b><br/><i>[Container]</i>"]
    store[("<b>Cache directory</b><br/><i>[Container]</i>")]
    ollama["<b>Model server</b><br/><i>[External System]</i>"]

    operator -->|"picks a server, its<br/>models and the cases"| benchpage
    operator -->|"--model, --scripted, --case<br/>[terminal]"| commandline
    benchpage -->|"GET /api/config, /api/models,<br/>/api/state each second while one runs<br/>POST /api/run, /api/stop, /api/clear<br/>[JSON]"| handler
    handler -.->|"subclasses: _refused, the<br/>static files, the model listing"| shophandler
    handler -->|"start, stop, clear, state"| bench
    bench -->|"run_case, contender by contender<br/>[its own thread]"| compare
    commandline -->|"run_case, contender<br/>by contender"| compare
    compare -.->|"the request, the key,<br/>the scripts"| cases
    compare -->|"run_benchmark, the contender's<br/>model behind a stopwatch"| runner
    compare -.->|"builds a served contender's<br/>model off its provider row"| pipeline
    runner -->|"BuyAgent.run(), over the case"| pipeline
    runner -->|"score_run"| scoring
    compare -->|"judge_query"| scoring
    scoring -.->|"matches names by grounding's own<br/>rule, orders by rank_products"| pipeline
    bench -->|"adds each run,<br/>reads them all"| board
    commandline -->|"the same, unless<br/>--no-save"| board
    board -->|"benchmark/board.json<br/>[JSON file]"| store
    pipeline -->|"[HTTP]"| ollama

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef container fill:#438dd5,stroke:#2e6295,color:#fff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000
    classDef external fill:#999,stroke:#6b6b6b,color:#fff
    class operator person
    class benchpage,pipeline,store container
    class commandline,handler,bench,compare,runner,cases,scoring,board,shophandler component
    class ollama external
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

## A streamed run, end to end

```mermaid
sequenceDiagram
    autonumber
    actor S as Shopper
    participant UI as Web UI
    participant H as BuyAgentHandler
    participant W as Worker thread
    participant J as Journal
    participant A as BuyAgent
    participant O as Model server
    participant D as Search backend
    participant P as Shop pages

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
    Note over H,UI: A quiet stretch sends a ping event every 15s.<br/>A failed run sends a failure event carrying its HTTP status.<br/>A frame that cannot be written stops the run at its next checkpoint.
```

Where a run is deterministic, the page text and the model's answers come off
disk for as long as `cache_ttl` allows, rather than off the web and the model
(ADR-0040, ADR-0044).

Only query refinement is recoverable: it falls back to the raw request but lets
`ModelUnavailableError` through. `BuyAgent.run()` raises exactly `ValueError`,
`ModelUnavailableError` and `SearchError`, which `api._STATUS` maps to 400, 503
and 502; the provider writes the remedy the middle one carries.
