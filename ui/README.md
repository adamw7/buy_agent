# buy_agent UI

An Angular front end for the agent. It is a second way into the same
`BuyAgent.run()` the CLI drives -- searching, grounding and ranking all still
happen in Python.

## Running it

The page needs the API next to it, so start the Python server first:

```powershell
python -m buy_agent.server            # http://127.0.0.1:8000
```

That serves this app once it has been built:

```powershell
cd ui
npm install
npm run build                         # writes dist/ui/browser, which the server serves
```

While working on the UI itself, use the dev server instead -- it rebuilds on
save and proxies `/api` to the Python server on port 8000 (`proxy.conf.json`):

```powershell
npm start                             # http://localhost:4200
```

```powershell
npm test                              # vitest, in jsdom -- no browser needed
```

## How it is put together

| File | Responsibility |
| --- | --- |
| `src/app/app.ts` | The page: holds the run's state and stitches the three components together |
| `src/app/agent.ts` | The API: `/api/config`, `/api/models`, and the event stream |
| `src/app/agent.types.ts` | The shapes the Python API answers with |
| `src/app/search-form/` | What to buy, plus the settings the CLI takes as flags |
| `src/app/progress-log/` | The agent's own log lines, as they arrive |
| `src/app/product-card/` | One ranked product |

Two things are worth knowing before changing it:

- **A run is streamed, not requested.** A search takes tens of seconds, so
  `AgentService.search()` opens an `EventSource` against `/api/search/stream`
  and emits the agent's log lines as they happen, finishing on a `result` or a
  `failure` event. Unsubscribing closes the stream, which is what Stop does.
  The server's failure event is deliberately not called `error`: a browser's
  `EventSource` already delivers transport errors under that name, and it would
  otherwise reconnect and silently start the whole search again.
- **Nothing here decides an answer.** Ranking, grounding and even how a missing
  price reads are Python's, and the payload carries `price_label` and
  `rating_label` alongside the raw numbers so this app never has to reinvent
  them. Sorting is a request parameter, not a client-side re-sort, for the same
  reason.
