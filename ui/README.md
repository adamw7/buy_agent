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
| `src/app/agent.ts` | The API: `/api/config`, `/api/models`, `/api/sources`, and the event stream |
| `src/app/agent.types.ts` | The shapes the Python API answers with |
| `src/app/search-form/` | What to buy, plus the settings the CLI takes as flags |
| `src/app/progress-log/` | The agent's own log lines, as they arrive |
| `src/app/product-card/` | One ranked product |

Three things are worth knowing before changing it:

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
- **The form refuses a bad setting before the run, on the server's rules**
  ([ADR-0033](../docs/adr/0033-let-the-form-refuse-what-the-server-would.md)).
  The ranges arrive with `/api/config` and are bound into the number fields, so
  there is no second copy of `config.LIMITS` here; a trusted source is not a
  range, so the field asks `/api/sources` when it is left and shows the sentence
  that comes back. Anything the page cannot judge -- a region's shape -- is
  still refused by the server, and the `failure` event names the field so the
  box gets marked rather than just the banner.

## The four components, and why they are shaped that way

The rules a change here has to keep are in the repository's `CLAUDE.md`. This is
the reasoning behind them.

### The header pill

It says whether the model server answered, and `App.unreachable` says why and
what to type. That sentence is the `hint` `installed_models` sent, shown as a
line under the pill rather than a `title`, which is hover-only -- unavailable on
a touch screen, easy to miss, inconsistently announced by screen readers -- and
this is the one failure whose fix is a single command. It is `null` for a server
that answered, and also when the *agent* server is the one that did not: nothing
came back to ask, and a sentence written here rather than in `providers.py`
would be a second wording to keep true. It is `white-space: pre-wrap`, keeping
the run of spaces Python puts in front of the command, and a **Check again**
button sits beside it, since the moment someone has just run that command is the
moment they need to say so.

While a listing is in flight the pill says **Asking &lt;label&gt;…** instead,
and the remedy under it and the model picker beside it both stand down.
`App.asking` holds the `ModelSource` being asked about, so the pill names *that*
server rather than the one still on screen. `installed_models` is a call per
pulled tag on a five-second budget
([ADR-0032](../docs/adr/0032-say-which-models-can-answer-a-prompt.md)), so this
is the one wait on the page with nothing else to say it is happening: without it
the first load had no pill at all, Check again looked like a button that did
nothing, and a model picked in that window was one the new server had never
offered.

### `progress-log`

It follows the tail the way a terminal does, but only while the reader is at it:
the scroll handler sets `sticking` from how far the panel is from the bottom, so
a reader who scrolled up to re-read a finished step is left there.

It offers **Download log** for a run that failed and for one the reader stopped,
and for no other -- a run that finished is on the page in front of you, while
those two leave nothing there at all, and the reason to stop one is usually that
it had gone quiet. `transcript()` writes what the panel was showing plus the
failure message, which the panel never has, a failure arriving as its own SSE
event rather than a log line; a stopped run needs no such line, `App.stop`
having written one into the log itself. It keeps whole logger names where the
panel trims them.

It also counts the wait out, beside the working pill and then as the total in
place of it. Extraction is slow and logs nothing while it runs, so the panel is
otherwise a frozen list under a pulsing dot, with no way to tell a model still
thinking from one that has stopped answering. The timestamps say that
afterwards, `elapsed()` while it is happening. The clock starts and stops on the
`running` input, redraws once a second and is cleared on destroy; `duration()`
writes seconds and minutes (`8s`, `2m 14s`) rather than a `0:08` clock, this
being how long something took and not what time it is.

### `product-card`

It draws one product and, where the run asked to pay and the server can, offers
to buy it in **two** clicks. The second one is the small Trusted Surface AP2
asks for: it restates the *cart* -- the title, the cart's own `pay_label`, the
merchant, which rail, and whether anybody will actually be charged -- rather
than the request that found it, because the cart is what the mandates carry. The
cart's label and not the product's: a page that printed a bare "329.00" leaves
`price_label` with no unit on it while the purchase is in the run's currency all
the same
([ADR-0043](../docs/adr/0043-compare-prices-only-within-one-currency.md)), and a
surface that names no money is not one. A single button would be a purchase made
by a misclick on a card in a list.

What it emits is the three fields a person was shown, which the server holds
against the cart it builds itself. The card decides nothing else, and a product
it may not buy shows Python's `cannot_pay` sentence rather than no button and no
explanation. Once something is bought the receipt replaces the button: "Paid"
where money moved and "Authorised" where it did not, which for the dry run is
the honest word.

That receipt belongs to the *product* and not to the rank it was bought at, and
so does the card drawing it. A re-sort ranks the same products again from 1
([ADR-0035](../docs/adr/0035-re-sort-a-finished-run-without-running-it-again.md)),
so `App.receipts` is keyed by name and the two loops track by name. Tracked by
index, a purchase moves to whatever comes up at that rank next: shown against
something nobody bought, while the thing that was bought is offered a Pay button
for a second go, and a confirmation opened on one product stays open over
another.

### `search-form`

It remembers the advanced settings in `localStorage` and the request
deliberately not -- what to shop for is a new question every time -- and every
read and write is wrapped, so a browser that refuses storage still gets a
working form. One setting is deliberately not remembered: `pay` itself. The
others are standing answers about this machine, and "you may spend my money" is
not one of them; a browser that restored it would arm the next visit's run with
nobody having said so.

Its payment block is drawn only when `pay_available` says the server has the AP2
SDK at all -- a switch whose only outcome is a message about pip is worse than a
sentence -- and the rail picker, its address field and the spend limit appear
only once paying is ticked. `moves_money` and `needs_endpoint` come off the
rail's own row, so the warning under the picker and the disabled address box are
Python's answers rather than a second reading of a rail's name here.

It refuses what the server would, before the run rather than a minute into it.
`problems()` is what the page worked out -- each number against the range that
came down with the defaults, and the sources field against whatever `GET
/api/sources` last said about the text it holds -- and it gates `canSubmit`,
none of it costing anything to know. A box the run does not take is outside all
of that: a field whose `off()` is true is neither held to its range nor sent at
all (`sent()` reads it as the cleared box it is drawn as), because it is
disabled, and a mark on a disabled box is one nobody can act on -- a form that
will not search pointing at a field that cannot be typed into. Switching to a
vLLM over a context window the form had already refused, or turning paying off
over a spend limit it had, was exactly that.

`notes()` is what is shown under each field: `problems()`, plus the `rejected`
input for a field the page has no rule for, which is the `field` a `failure`
event named. The server's mark does not gate the button, being about what was
sent, and it is shown only while the box still holds what was sent: `submitted`
keeps the payload each run went out with, and a mark that outlived the mistake
was a red field, an `aria-invalid` and a "1 setting to look at" over a form with
nothing wrong with it. `options()` is the one place that payload is built, since
the comparison and the run have to agree on what a box holds.

One check is the page's own rather than a range. A number box holding text that
is not a number reports the empty string, which reaches `ngModel` as the `null`
a *cleared* box means
([ADR-0012](../docs/adr/0012-the-browser-decides-nothing.md)), so `numberTyped`
asks the element's `validity.badInput` on every keystroke and `problems()` marks
it. Left to the `null`, a box visibly full of nonsense was sent as unset and the
run quietly used the default. The sources check goes out on `change` rather than
on every keystroke, and once more after `restore`, a remembered bad source being
one nobody is about to retype.

The `numberFields` table is the one place a number box is declared -- the key it
is sent under, which is also the key its range arrives under and its refusal
names, its label, its step and its hint -- and the template loops over it rather
than repeating the same twenty lines of markup per setting.
`tests/test_conventions.py` holds its keys against `limits_payload`, so a box
that is drawn is a box that is held to a range. `placeholders()` is the smaller
half of the same idea: a cleared number box means "use the default", an answer
rather than a mistake, so each box names the number it falls back to, read off
`defaults_payload` by the same key the box is sent under rather than listed a
second time.

Every one of those marks is on a field inside the Settings panel, which is shut
until somebody opens it, so the form opens it itself the first time `flagged()`
is not zero, and the summary carries the count for a reader who has shut it
again. Left alone, a marked box says nothing and a run refused for a setting
leaves a greyed-out button with no visible reason; the case that needs no
keystroke at all is a remembered source or number the server would refuse,
restored, checked and marked before the form is first drawn. The effect fires on
the marks changing and not on the panel's state, so closing it again stays the
reader's to do.

Its model field is a `<select>` over `GET /api/models`, and its three edge cases
are the point. A name chosen but *not* in that list -- a remembered setting, or
a default for a model nobody pulled -- is kept marked "not served" rather than
dropped, since dropping it would silently run the search on whichever model
sorted first. A model that *is* there and reports no `completion` capability is
marked "embedding only" for the same reason turned around: it is a pull someone
made on purpose, and hiding it would leave nothing to explain why the tag they
remember is gone. Which of the two an entry gets is `ModelOption.note`, filled
from the `completion` Python sent: the browser writes the suffix, not the
judgement. An empty list falls back to the text box it used to be, a dropdown
holding one unusable entry being worse than typing.

The field is disabled while `checking` is set, and says which server it is
waiting on, since what it holds until the answer lands is the *last* server's
models. Because the list belongs to one server, editing the address field emits
`refresh` and `App.refreshModels` asks that one instead; `refresh` carries a
`ModelSource`, the provider *and* the address, because a vLLM asked Ollama's
question answers 404. Changing the provider picker emits the same event after
filling the model and address fields from that provider's row. `takes_num_ctx`
on that row is what disables the context field and replaces its note, rather
than the form testing the provider's name.

## Testing it

Angular components are tested in jsdom with `TestBed`, and `AgentService`
against a fake `EventSource` rather than a live one. The Python server's own
tests inject a stub agent through `create_server(agent_factory=...)`.
