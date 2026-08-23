# Architecture (C4)

The [C4 model](https://c4model.com) at three zoom levels: who uses the system,
what it is made of, and how the pieces inside each part fit together. The
diagrams are Mermaid, so GitHub renders them in place.

The one idea worth carrying through all three levels: **the LLM is not in
charge.** It refines the query and reads products out of pages; every decision
that shapes the answer -- filtering, grounding, ranking, ordering -- is ordinary
Python. That is why the component diagram has one small box for the model and
several for the code around it.

## Level 1 -- System context

```mermaid
graph TB
    shopper["<b>Shopper</b><br/><i>[Person]</i><br/>Wants to buy something and<br/>would rather not read ten<br/>listicles first"]

    system["<b>buy_agent</b><br/><i>[Software System]</i><br/>Turns a plain-language request into<br/>a ranked shortlist of real products,<br/>each figure backed by a source page"]

    ollama["<b>Ollama</b><br/><i>[External System]</i><br/>Local LLM server. Refines the query<br/>and extracts products, under a<br/>JSON schema that constrains decoding"]
    ddg["<b>DuckDuckGo</b><br/><i>[External System]</i><br/>Web search, no API key"]
    shops["<b>Shop and review pages</b><br/><i>[External System]</i><br/>The pages the search returns;<br/>the only source of prices,<br/>ratings and review counts"]

    shopper -->|"asks for a product, in their own words<br/>[CLI or web browser]"| system
    system -->|"reports the ranked shortlist<br/>and its progress"| shopper
    system -->|"prompts with a JSON schema<br/>[HTTP, localhost:11434]"| ollama
    system -->|"searches<br/>[HTTPS]"| ddg
    system -->|"fetches and condenses<br/>[HTTPS]"| shops

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef internal fill:#1168bd,stroke:#0b4884,color:#fff
    classDef external fill:#999,stroke:#6b6b6b,color:#fff
    class shopper person
    class system internal
    class ollama,ddg,shops external
```

Everything runs on the shopper's own machine: no accounts, no API keys, and
nothing about a search leaves the laptop except the search itself and the page
fetches.

## Level 2 -- Containers

```mermaid
graph TB
    shopper["<b>Shopper</b><br/><i>[Person]</i>"]

    subgraph system["buy_agent"]
        cli["<b>CLI</b><br/><i>[Container: Python]</i><br/>python -m buy_agent 'gaming laptop'<br/>Parses flags into an AgentConfig,<br/>runs one search, logs the top N"]
        spa["<b>Web UI</b><br/><i>[Container: Angular 22, TypeScript]</i><br/>A form, a live progress log and<br/>the ranked cards. Decides nothing:<br/>it renders what the API sends"]
        server["<b>HTTP server</b><br/><i>[Container: Python, stdlib http.server]</i><br/>Serves the built UI and the JSON API,<br/>and relays a run's log lines as<br/>Server-Sent Events"]
        pipeline["<b>Agent pipeline</b><br/><i>[Container: Python library]</i><br/>BuyAgent.run() -- search, extract,<br/>ground, deduplicate, rank.<br/>The one implementation both<br/>front ends drive"]
    end

    ollama["<b>Ollama</b><br/><i>[External System]</i>"]
    ddg["<b>DuckDuckGo</b><br/><i>[External System]</i>"]
    shops["<b>Shop and review pages</b><br/><i>[External System]</i>"]

    shopper -->|"types a request<br/>[terminal]"| cli
    shopper -->|"visits localhost:8000<br/>[HTTPS/HTTP]"| spa

    spa -->|"GET /api/config, /api/models<br/>POST /api/search<br/>GET /api/search/stream (SSE)<br/>[JSON over HTTP]"| server
    server -->|"serves index.html and assets<br/>[HTTP]"| spa
    cli -->|"calls run()"| pipeline
    server -->|"runs a search in a worker thread,<br/>relays its log records"| pipeline

    pipeline -->|"[HTTP]"| ollama
    pipeline -->|"[HTTPS]"| ddg
    pipeline -->|"[HTTPS]"| shops

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef container fill:#438dd5,stroke:#2e6295,color:#fff
    classDef external fill:#999,stroke:#6b6b6b,color:#fff
    class shopper person
    class cli,spa,server,pipeline container
    class ollama,ddg,shops external
```

The CLI and the server are two front ends onto the same `BuyAgent.run()`. The
server is stdlib-only on purpose -- a run that takes a minute and serves one
person does not need a framework under it.

## Level 3 -- Components of the agent pipeline

```mermaid
graph TB
    cli["<b>CLI</b><br/><i>[Container]</i>"]
    server["<b>HTTP server</b><br/><i>[Container]</i>"]

    subgraph pipeline["Agent pipeline"]
        agent["<b>BuyAgent</b><br/><i>[Component: agent.py]</i><br/>Orchestrates the fixed pipeline and<br/>translates Ollama transport failures<br/>into an actionable message"]
        config["<b>AgentConfig</b><br/><i>[Component: config.py]</i><br/>Model, search, fetch and ranking<br/>settings; the CLI's flag defaults"]
        extraction["<b>Extraction</b><br/><i>[Component: extraction.py]</i><br/>Both prompts and both chains,<br/>plus name cleaning and merging<br/>of variant names"]
        search["<b>Search</b><br/><i>[Component: search.py]</i><br/>DuckDuckGo wrapper; raises<br/>SearchError on a rate limit"]
        fetch["<b>Fetch</b><br/><i>[Component: fetch.py]</i><br/>Fetches result pages in parallel and<br/>keeps only the lines quoting a price<br/>or a rating"]
        verification["<b>Verification</b><br/><i>[Component: verification.py]</i><br/>Drops products the sources never<br/>named, and blanks any figure the<br/>page text does not contain"]
        ranking["<b>Ranking</b><br/><i>[Component: ranking.py]</i><br/>Weighted score over rating,<br/>popularity and price. No LLM"]
        models["<b>Models</b><br/><i>[Component: models.py]</i><br/>ExtractedProduct (sentinels, for the<br/>LLM's schema) vs Product (None)"]
        logsetup["<b>Report and logging</b><br/><i>[Component: logging_setup.py]</i><br/>Log format, and the top-N report<br/>the browser also reads as events"]
    end

    ollama["<b>Ollama</b><br/><i>[External System]</i>"]
    ddg["<b>DuckDuckGo</b><br/><i>[External System]</i>"]
    shops["<b>Shop and review pages</b><br/><i>[External System]</i>"]

    cli -->|"run(request, sort_by)"| agent
    server -->|"run(request, sort_by)"| agent
    cli -.->|"builds"| config
    server -.->|"builds from the request"| config
    config -.->|"reads"| agent

    agent -->|"1. refine the query"| extraction
    agent -->|"2. search"| search
    agent -->|"3. enrich the results"| fetch
    agent -->|"4. extract products"| extraction
    agent -->|"5. clean, then ground<br/>against the same text"| verification
    agent -->|"6. deduplicate"| extraction
    agent -->|"7. rank"| ranking
    agent -->|"8. log the top N"| logsetup

    extraction -->|"invokes the chains<br/>[JSON schema]"| ollama
    search -->|"[HTTPS]"| ddg
    fetch -->|"[HTTPS]"| shops
    extraction -.->|"ExtractedProduct → Product"| models
    verification -.-> models
    ranking -.-> models

    classDef container fill:#438dd5,stroke:#2e6295,color:#fff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000
    classDef external fill:#999,stroke:#6b6b6b,color:#fff
    class cli,server container
    class agent,config,extraction,search,fetch,verification,ranking,models,logsetup component
    class ollama,ddg,shops external
```

Two joints in that order are load-bearing, and both are about not ranking a
number nobody wrote down:

- `clean_products` runs **before** `ground`, so a name still wearing its
  publisher suffix ("... Review | AudioSite") is not failed by a coverage check
  for tokens the page never had to contain.
- `ground` runs **before** `deduplicate`, so merging only ever combines figures
  the sources back.

Extraction and verification must be handed the *same* text, which is why
`fetch.enrich()` puts the condensed page content on `SearchResult` rather than
passing it alongside.

## Level 3 -- Components of the web tier

```mermaid
graph TB
    browserUser["<b>Shopper</b><br/><i>[Person]</i>"]

    subgraph spa["Web UI [Angular]"]
        app["<b>App</b><br/><i>[Component: app.ts]</i><br/>Holds the run's state in signals;<br/>splits the answer into the top N<br/>and the rest"]
        form["<b>SearchForm</b><br/><i>[Component: search-form]</i><br/>The request and every option,<br/>seeded from /api/config"]
        log["<b>ProgressLog</b><br/><i>[Component: progress-log]</i><br/>The agent's own log lines,<br/>as they arrive"]
        card["<b>ProductCard</b><br/><i>[Component: product-card]</i><br/>One ranked product, rendered<br/>from the labels the API sent"]
        agentsvc["<b>AgentService</b><br/><i>[Component: agent.ts]</i><br/>HttpClient for the JSON endpoints;<br/>wraps EventSource as an Observable,<br/>so unsubscribing is the Stop button"]
    end

    subgraph srv["HTTP server [Python]"]
        handler["<b>BuyAgentHandler</b><br/><i>[Component: server.py]</i><br/>Routes /api to the API and<br/>everything else to the built app,<br/>falling back to index.html"]
        relay["<b>_LogRelay</b><br/><i>[Component: server.py]</i><br/>A logging handler that fans records<br/>out by thread id, so two concurrent<br/>runs never see each other's progress"]
        api["<b>API</b><br/><i>[Component: api.py]</i><br/>Coerces options into an AgentConfig,<br/>runs the pipeline, shapes products as<br/>JSON, maps each failure to a status<br/>(400 / 502 / 503)"]
    end

    agentpipeline["<b>Agent pipeline</b><br/><i>[Container]</i>"]

    browserUser --> form
    form -->|"submit"| app
    app --> log
    app --> card
    app -->|"search(options)"| agentsvc
    agentsvc -->|"GET /api/search/stream<br/>[SSE: log, result, failure, ping]"| handler
    agentsvc -->|"GET /api/config, /api/models<br/>POST /api/search<br/>[JSON]"| handler

    handler -->|"parse_options, run_search"| api
    handler -->|"reads the queue for this run"| relay
    api -->|"agent_factory(config).run()<br/>[worker thread]"| agentpipeline
    agentpipeline -.->|"log records"| relay

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef container fill:#438dd5,stroke:#2e6295,color:#fff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000
    class browserUser person
    class agentpipeline container
    class app,form,log,card,agentsvc,handler,relay,api component
```

Three details there are easy to get wrong and are deliberate:

- **A run is streamed, not requested.** A search takes tens of seconds, so the
  UI uses `GET /api/search/stream` and watches the same progress the CLI prints.
  `POST /api/search` is the same run in one response, for scripts.
- **The stream's failure event is called `failure`, not `error`.** A browser's
  `EventSource` delivers transport errors under `error` and then reconnects; a
  named `error` event would be indistinguishable from a dropped connection, and
  the reconnect would silently start the whole search again.
- **The browser decides nothing.** Ranking, grounding and even the wording of an
  unknown price stay in Python: `product_payload` sends `price_label` and
  `rating_label` next to the raw figures, and `sort_by` is a request parameter
  rather than a client-side re-sort.

## A streamed run, end to end

```mermaid
sequenceDiagram
    autonumber
    actor S as Shopper
    participant UI as Web UI
    participant H as BuyAgentHandler
    participant W as Worker thread
    participant A as BuyAgent
    participant O as Ollama
    participant D as DuckDuckGo
    participant P as Shop pages

    S->>UI: "wireless headphones under $200"
    UI->>H: GET /api/search/stream?... (EventSource)
    H-->>UI: 200 text/event-stream
    H->>W: start the run, attach the log relay
    W->>A: run(request, sort_by)
    A->>O: refine the query
    O-->>A: search query
    A-->>UI: event: log
    A->>D: search
    D-->>A: up to 10 results
    A->>P: fetch pages in parallel
    P-->>A: HTML, condensed to priced lines
    A->>O: extract products (JSON schema)
    O-->>A: candidates
    A->>A: clean → ground → deduplicate → rank
    A-->>UI: event: log (top 3 report)
    W-->>H: ranked products
    H-->>UI: event: result
    UI-->>S: the shortlist, best first
    Note over H,UI: A quiet stretch sends a ping event every 15s.<br/>A failed run sends a failure event carrying its HTTP status.
```

Only query refinement is recoverable: it falls back to the raw request, but lets
`OllamaUnavailableError` through rather than searching with a model that is not
there. `BuyAgent.run()` raises exactly three things -- `ValueError`,
`OllamaUnavailableError`, `SearchError` -- which `__main__.main()` logs and
`api._STATUS` maps onto 400, 503 and 502.
