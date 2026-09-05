# Architecture (C4)

The [C4 model](https://c4model.com) at three zoom levels: who uses the system,
what it is made of, and how the pieces inside each part fit together. The
diagrams are Mermaid, so GitHub renders them in place.

This is what the system *is*; why it is this way is the decision log in
[adr/](adr/README.md), and a boundary that moves here usually means a new record
there. The one idea worth carrying through all three levels: **the LLM is not in
charge.** It refines the query and reads products out of pages; every decision
that shapes the answer -- filtering, grounding, ranking, ordering -- is ordinary
Python, which is why the component diagram has one small box for the model and
several for the code around it.

## Level 1 -- System context

```mermaid
graph TB
    shopper["<b>Shopper</b><br/><i>[Person]</i><br/>Wants to buy something and<br/>would rather not read ten<br/>listicles first"]

    system["<b>buy_agent</b><br/><i>[Software System]</i><br/>Turns a plain-language request into<br/>a ranked shortlist of real products,<br/>each figure backed by a source page"]

    ollama["<b>Model server</b><br/><i>[External System]</i><br/>A local Ollama, or a vLLM behind its<br/>OpenAI-compatible API. Refines the<br/>query and extracts products, under a<br/>JSON schema that constrains decoding"]
    ddg["<b>DuckDuckGo</b><br/><i>[External System]</i><br/>Web search, no API key"]
    shops["<b>Shop and review pages</b><br/><i>[External System]</i><br/>The pages the search returns;<br/>the only source of prices,<br/>ratings and review counts"]

    shopper -->|"asks for a product, in their own words<br/>[CLI or web browser]"| system
    system -->|"reports the ranked shortlist<br/>and its progress"| shopper
    system -->|"prompts with a JSON schema<br/>[HTTP, :11434 or :8000/v1]"| ollama
    system -->|"searches<br/>[HTTPS]"| ddg
    system -->|"fetches and condenses<br/>[HTTPS]"| shops

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef internal fill:#1168bd,stroke:#0b4884,color:#fff
    classDef external fill:#999,stroke:#6b6b6b,color:#fff
    class shopper person
    class system internal
    class ollama,ddg,shops external
```

Everything runs on the shopper's own machine, or on one they control: no
accounts, no hosted models, and nothing about a search leaves except the search
itself and the page fetches. Which model server that is -- Ollama by default, or a
vLLM already serving a model on a GPU box -- is `AgentConfig.provider`, and
nothing downstream of `buy_agent/providers.py` knows the difference: one table row
holds a server whole, and `AgentConfig.model_server` is the only place a provider
name becomes behaviour
([ADR-0028](adr/0028-serve-the-model-from-ollama-or-vllm.md),
[ADR-0029](adr/0029-one-table-per-model-server.md)).

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

    ollama["<b>Model server</b><br/><i>[External System]</i><br/>Ollama or vLLM"]
    ddg["<b>DuckDuckGo</b><br/><i>[External System]</i>"]
    shops["<b>Shop and review pages</b><br/><i>[External System]</i>"]

    shopper -->|"types a request<br/>[terminal]"| cli
    shopper -->|"visits localhost:8000<br/>[HTTPS/HTTP]"| spa

    spa -->|"GET /api/config, /api/models, /api/sources<br/>POST /api/search, /api/rank<br/>GET /api/search/stream (SSE)<br/>[JSON over HTTP]"| server
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
person does not need a framework under it. It binds loopback, which keeps it off
the network but not out of the browser: every request is admitted before it is
routed, so the API answers its own page and not the other tabs
([ADR-0018](adr/0018-guard-the-loopback-server-against-other-pages.md)).

The three containers inside the box ship as one image when the `Dockerfile` is
used: the UI is built in a Node stage and copied into the Python one, and the same
image runs either front end. The model server stays outside it, on the host or on
another machine, for the reasons in
[ADR-0015](adr/0015-package-the-web-tier-as-a-container.md) -- the boundary drawn
here is the one the image keeps.

## Level 3 -- Components of the agent pipeline

```mermaid
graph TB
    cli["<b>CLI</b><br/><i>[Container]</i>"]
    server["<b>HTTP server</b><br/><i>[Container]</i>"]

    subgraph pipeline["Agent pipeline"]
        agent["<b>BuyAgent</b><br/><i>[Component: agent.py]</i><br/>Orchestrates the fixed pipeline and<br/>translates transport failures into<br/>an actionable message"]
        config["<b>AgentConfig</b><br/><i>[Component: config.py]</i><br/>Provider, model, search, fetch and<br/>ranking settings; the CLI's flag<br/>defaults, and the ranges and the<br/>region shape both front doors<br/>hold a request to"]
        providers["<b>Providers</b><br/><i>[Component: providers.py]</i><br/>Everything that differs between<br/>Ollama and vLLM, one row each: the<br/>model, address and key it defaults<br/>to, the client and how it declares a<br/>schema, the listing, the errors that<br/>mean &quot;not there&quot;, and what to say"]
        chat["<b>Chat</b><br/><i>[Component: chat.py]</i><br/>A prompt with the run's values in it,<br/>a chain binding one to a schema, and<br/>the answer read back as that schema<br/>-- or refused"]
        extraction["<b>Extraction</b><br/><i>[Component: extraction.py]</i><br/>Both prompts and both chains,<br/>plus name cleaning and merging<br/>of variant names"]
        search["<b>Search</b><br/><i>[Component: search.py]</i><br/>DuckDuckGo wrapper; raises<br/>SearchError on a rate limit"]
        sources["<b>Sources</b><br/><i>[Component: sources.py]</i><br/>Reads a trusted source down to a<br/>domain and a term, narrows the<br/>query to it, and says whether a<br/>result came from it"]
        fetch["<b>Fetch</b><br/><i>[Component: fetch.py]</i><br/>Fetches result pages in parallel and<br/>keeps the lines quoting a figure and<br/>the lines passing judgement, each<br/>on a budget of its own; tallies how<br/>the pages that yielded nothing failed"]
        cache["<b>Page cache</b><br/><i>[Component: cache.py]</i><br/>The text of a fetched page, kept on<br/>disk for a day. Best-effort: every<br/>failure is a miss, never a failed run"]
        verification["<b>Verification</b><br/><i>[Component: verification.py]</i><br/>Drops products the sources never<br/>named, blanks any figure and any<br/>quote the page text does not<br/>contain, and links each product<br/>to the page naming it"]
        constraints["<b>Constraints</b><br/><i>[Component: constraints.py]</i><br/>The shopper's bounds -- max price,<br/>min rating, min reviews -- applied<br/>after merging and before ranking.<br/>An unknown figure is not a violation"]
        ranking["<b>Ranking</b><br/><i>[Component: ranking.py]</i><br/>Weighted score over rating,<br/>popularity and price, and the shares<br/>it was blended from. No LLM"]
        models["<b>Models</b><br/><i>[Component: models.py]</i><br/>ExtractedProduct (sentinels, for the<br/>LLM's schema) vs Product (None)"]
        logsetup["<b>Report and logging</b><br/><i>[Component: logging_setup.py]</i><br/>Log format, and the top-N report<br/>the browser also reads as events"]
    end

    ollama["<b>Model server</b><br/><i>[External System]</i><br/>Ollama or vLLM"]
    ddg["<b>DuckDuckGo</b><br/><i>[External System]</i>"]
    shops["<b>Shop and review pages</b><br/><i>[External System]</i>"]

    cli -->|"run(request, sort_by)"| agent
    server -->|"run(request, sort_by)"| agent
    cli -.->|"builds"| config
    server -.->|"builds from the request"| config
    config -.->|"reads"| agent

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

    providers -->|"[HTTP]"| ollama
    extraction -->|"invokes the chains<br/>[JSON schema]"| ollama
    search -->|"[HTTPS]"| ddg
    fetch -->|"[HTTPS]"| shops
    fetch -.->|"reads what it read<br/>last time"| cache
    extraction -.->|"ExtractedProduct → Product"| models
    verification -.-> models
    constraints -.-> models
    ranking -.-> models

    classDef container fill:#438dd5,stroke:#2e6295,color:#fff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000
    classDef external fill:#999,stroke:#6b6b6b,color:#fff
    class cli,server container
    class agent,config,providers,extraction,search,sources,fetch,cache,verification,constraints,ranking,models,logsetup component
    class ollama,ddg,shops external
```

Three joints in that order are load-bearing, and the first two are about not
ranking a number nobody wrote down:

- `clean_products` runs **before** `ground`, so a name still wearing its
  publisher suffix ("... Review | AudioSite") is not failed by a coverage check
  for tokens the page never had to contain.
- `ground` runs **before** `deduplicate`, so merging only ever combines figures
  -- and links -- the sources back.
- The shopper's bounds run **after** `deduplicate` and **before** `rank_products`
  (ADR-0039). After, because `_fill_gaps` may be what supplies the price they are
  judged on; before, because price scores relative to the candidate set, and
  scored against products the shopper cannot buy "the cheapest of these" names an
  option that is not on offer.

Order alone is not enough for the merge, because a merge also *pairs* figures.
`models.QUALIFIERS` names what only qualifies another field -- price with
currency, rating with review count -- and `_fill_gaps` moves whole groups, so a
listing that quoted 129 and one that quoted "249 EUR" are never reported together
as "129.00 EUR" (ADR-0022). Extraction and verification must be handed the *same*
text, which is why `fetch.enrich()` puts the condensed page content on
`SearchResult` rather than passing it alongside.

Step 2 is one search, unless the shopper named the sources the facts should come
from -- `site:` takes one domain at a time, so each source is searched separately
and the results pooled, deduplicated by URL and cut back to the width the run was
configured for. `sources.py` decides only what a source *is*; the searching stays
in `agent.py` and the DuckDuckGo call in `search.py` (ADR-0021). Nothing further
down knows the feature exists: the pool is what gets fetched, extracted from and
grounded against either way, which makes "every fact came from a page you named"
true by construction rather than by promise (ADR-0027).

Step 3 also says how it went, because grounding downstream blanks every figure the
pages did not back: a run whose fetches all failed reports "price unknown" for
everything, which reads as a bad model rather than as nothing having been read. So
the tally names the *kinds* of failure and counts them -- "Got usable page text
from 0 of 10 result(s): 7 refused (403), 2 timed out, 1 quoted no prices and no
verdicts" -- one line at INFO, which is what the browser's progress panel relays,
with the per-URL reasons left at DEBUG. Nothing read at all is a warning rather
than another step of the narration.

Grounding covers the quoted opinions too, and holds them to a stricter bar than a
figure in two ways. A quote has to appear as running text -- overlapping runs of
five consecutive words, most of which must be found -- rather than as words that
each occur somewhere, since a model paraphrasing out of the vocabulary it has just
read would clear any looser bar (ADR-0024). And it has to appear on a *single page
that mentions the product* rather than anywhere in the ten pooled together, a real
verdict on the electric kettle three results down being no evidence about these
headphones (ADR-0025). The merge treats opinions as the exception they are: they
come from *both* listings, two reviewers being no conflict.

Grounding also decides where a product *links*. The model is asked for a `url` but
reliably leaves it empty, so `attribute_sources()` gives each product the first
searched page whose own text mentions it, and keeps a model-supplied link only
when it names one of those pages (ADR-0017).

Step 7 is where the shopper's own terms are finally enforced rather than merely
searched for. A budget in the request text only ever shaped the query -- a page is
returned for matching words, not for obeying them -- so `--max-price`,
`--min-rating` and `--min-reviews` are numbers checked here, and a run that set
any of them says what it did with them ("1 of 10 product(s) are within the
limits"). A product whose figure the run never learned is kept: grounding blanks
what the pages did not back, and dropping blanks would reject products for the
extractor's misses, which is the reasoning that already scores them neutral
(ADR-0039).

Step 8 keeps what it worked out. `rank_products` blends three shares into one
score, and the shares travel beside it: without them a report says where a product
placed and not what it placed on, and -- worse -- cannot distinguish a criterion
that scored middling from one nothing was published for, both being the same 0.5
(ADR-0041).

## Level 3 -- Components of the web tier

```mermaid
graph TB
    browserUser["<b>Shopper</b><br/><i>[Person]</i>"]

    subgraph spa["Web UI [Angular]"]
        app["<b>App</b><br/><i>[Component: app.ts]</i><br/>Holds the run's state in signals;<br/>splits the answer into the top N<br/>and the rest"]
        form["<b>SearchForm</b><br/><i>[Component: search-form]</i><br/>The request and every option,<br/>seeded from /api/config,<br/>and refused here first"]
        log["<b>ProgressLog</b><br/><i>[Component: progress-log]</i><br/>The agent's own log lines,<br/>as they arrive"]
        card["<b>ProductCard</b><br/><i>[Component: product-card]</i><br/>One ranked product, rendered<br/>from the labels the API sent,<br/>with the shares its score<br/>was blended from"]
        agentsvc["<b>AgentService</b><br/><i>[Component: agent.ts]</i><br/>HttpClient for the JSON endpoints;<br/>wraps EventSource as an Observable,<br/>so unsubscribing is the Stop button"]
    end

    subgraph srv["HTTP server [Python]"]
        handler["<b>BuyAgentHandler</b><br/><i>[Component: server.py]</i><br/>Admits the request, then routes /api<br/>to the API and everything else to the<br/>built app, falling back to index.html"]
        guard["<b>_admits</b><br/><i>[Component: server.py]</i><br/>Refuses a request another site's page<br/>made, and a Host that merely resolves<br/>here -- loopback is not a boundary<br/>the browser respects"]
        relay["<b>_LogRelay</b><br/><i>[Component: server.py]</i><br/>A logging handler that fans records<br/>out by thread id, so two concurrent<br/>runs never see each other's progress"]
        api["<b>API</b><br/><i>[Component: api.py]</i><br/>Coerces options into an AgentConfig,<br/>runs the pipeline, shapes products as<br/>JSON, maps each failure to a status<br/>(400 / 502 / 503); re-ranks a finished<br/>run without running one"]
    end

    agentpipeline["<b>Agent pipeline</b><br/><i>[Container]</i>"]

    browserUser --> form
    form -->|"submit"| app
    app --> log
    app --> card
    app -->|"search(options), rank(products)"| agentsvc
    agentsvc -->|"GET /api/search/stream<br/>[SSE: log, result, failure, ping]"| handler
    agentsvc -->|"GET /api/config, /api/models, /api/sources<br/>POST /api/search, /api/rank<br/>[JSON]"| handler

    handler -->|"before any routing"| guard
    handler -->|"parse_options, run_search, rank_again"| api
    handler -->|"reads the queue for this run"| relay
    api -->|"agent_factory(config).run()<br/>[worker thread]"| agentpipeline
    agentpipeline -.->|"log records"| relay

    classDef person fill:#08427b,stroke:#052e56,color:#fff
    classDef container fill:#438dd5,stroke:#2e6295,color:#fff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000
    class browserUser person
    class agentpipeline container
    class app,form,log,card,agentsvc,handler,guard,relay,api component
```

Six details there are easy to get wrong and are deliberate:

- **A run is streamed, not requested.** A search takes tens of seconds, so the UI
  uses `GET /api/search/stream` and watches the same progress the CLI prints.
  `POST /api/search` is the same run in one response, for scripts.
- **Re-ordering a finished run runs nothing.** `POST /api/rank` takes the products
  the page is already holding and answers the shape a run answers with, having
  called `rank_products` and nothing else. The ordering stays in Python; only the
  searching is skipped (ADR-0035).
- **Closing the stream stops the run, at its next step.** `BuyAgent.run` calls a
  `checkpoint` before each step, and the first frame the handler cannot write sets
  the flag that makes it raise. A chat call already in flight still finishes, so
  the page's Stop line says as much rather than promising the run is over
  (ADR-0034).
- **The stream's failure event is called `failure`, not `error`.** A browser's
  `EventSource` delivers transport errors under `error` and then reconnects; a
  named `error` event would be indistinguishable from a dropped connection, and the
  reconnect would silently start the whole search again.
- **The browser decides nothing.** Ranking, grounding and even the wording of an
  unknown price stay in Python: `product_payload` sends `price_label` and
  `rating_label` next to the raw figures, and `sort_by` is a request parameter
  rather than a client-side re-sort -- for a finished run too, which posts its
  products back rather than sorting the array it holds (ADR-0035).
- **Loopback is not a boundary the browser respects.** Any page the shopper has
  open can reach `127.0.0.1`, so `_admits` runs before routing and refuses both a
  request another site's page made -- which would start a run whose answer it could
  never read -- and a `Host` that merely resolves here, which is how such a page
  would arrange to read one (ADR-0018).

## A streamed run, end to end

```mermaid
sequenceDiagram
    autonumber
    actor S as Shopper
    participant UI as Web UI
    participant H as BuyAgentHandler
    participant W as Worker thread
    participant A as BuyAgent
    participant O as Model server
    participant D as DuckDuckGo
    participant P as Shop pages

    S->>UI: "wireless headphones under $200"
    UI->>H: GET /api/search/stream?... (EventSource)
    H-->>UI: 200 text/event-stream
    H->>W: start the run, attach the log relay
    W->>A: run(request, sort_by, checkpoint)
    A->>O: refine the query
    O-->>A: search query
    A-->>UI: event: log
    A->>D: search (once per named source, else once)
    D-->>A: up to 10 results, from the named sources only
    A->>P: fetch pages in parallel
    P-->>A: HTML, condensed to figures and verdicts
    A->>O: extract products (JSON schema)
    O-->>A: candidates
    A->>A: clean → ground → deduplicate → rank
    A-->>UI: event: log (top 3 report)
    W-->>H: ranked products
    H-->>UI: event: result
    UI-->>S: the shortlist, best first
    Note over H,UI: A quiet stretch sends a ping event every 15s.<br/>A failed run sends a failure event carrying its HTTP status.<br/>A frame that cannot be written stops the run at its next checkpoint.
```

Only query refinement is recoverable: it falls back to the raw request, but lets
`ModelUnavailableError` through rather than searching with a model that is not
there. `BuyAgent.run()` raises exactly three things -- `ValueError`,
`ModelUnavailableError`, `SearchError` -- which `__main__.main()` logs and
`api._STATUS` maps onto 400, 503 and 502. Which sentence that middle one carries
-- `ollama pull` or `vllm serve` -- is the provider's to write, and is the whole
value of the exception.
