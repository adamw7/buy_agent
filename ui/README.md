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
| `src/app/agent.ts` | The API: `/api/config`, `/api/models`, `/api/sources`, `/api/bounds`, and the event stream |
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
  box gets marked rather than just the banner. Both marks then go together: the
  form says when the box it named has been typed over, and the banner repeating
  the same sentence is dropped with it rather than going on refusing a value
  nobody can see.

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
surface that names no money is not one. The cart's merchant for the same reason:
`pay_merchant` is the seller a page printed *or* the site it is on, and most
pages print no seller -- so a confirmation reading the product's own `seller`
asked for a purchase naming nobody at all, which is the field a person most
needs before agreeing to one. A single button would be a purchase made by a
misclick on a card in a list.

Under the figures it opens onto **what each page priced the product at**. The
agent reads several pages a run and used to keep one figure and throw the rest
away, so a card showing 149.00 USD looked exactly like one where 149.00 was the
only price anybody quoted
([ADR-0058](../docs/adr/0058-keep-every-listing-a-product-was-priced-at.md)).
The summary is Python's sentence and so is every amount inside it -- the card
formats no money, exactly as it writes no unknown price -- and each listing links
the page that printed it, which is what makes the spread checkable rather than
only readable.

What it emits is the three fields a person was shown, which the server holds
against the cart it builds itself. The card decides nothing else, and a product
it may not buy shows Python's `cannot_pay` sentence rather than no button and no
explanation. Once something is bought the receipt replaces the button: "Paid"
where money moved and "Authorised" where it did not, which for the dry run is
the honest word.

Between those two it says it is waiting. `paying` is the *name* of the product
being bought rather than a flag saying one is: one at a time is why every button
on every card stands down, and which one is why exactly one of them draws
"Authorising … with …" where its button was. Paying is two calls to a
counterparty on a thirty-second budget each, which is the longest wait the page
has and the only one that moves money -- and the whole of what it used to do
about that was grey three buttons out, which is indistinguishable from a click
that never registered and is the moment somebody clicks again. It is said on the
card, in the block the receipt lands in, for the reason the header pill says
**Asking &lt;label&gt;…**: a wait with nothing else to report it reports itself.

That receipt belongs to the *product* and not to the rank it was bought at, and
so does the card drawing it. A re-sort ranks the same products again from 1
([ADR-0035](../docs/adr/0035-re-sort-a-finished-run-without-running-it-again.md)),
so `App.receipts` is keyed by name and the two loops track by name. Tracked by
index, a purchase moves to whatever comes up at that rank next: shown against
something nobody bought, while the thing that was bought is offered a Pay button
for a second go, and a confirmation opened on one product stays open over
another.

### `search-form`

The request box is read by the server as well as searched with. `GET /api/bounds`
answers what the words themselves ask for -- "under $200", "at least 4 stars" --
and the box that would enforce it is filled in, once, only while it is empty,
under Python's own sentence saying where the number came from
([ADR-0059](../docs/adr/0059-notice-a-bound-in-the-request-and-offer-it.md)). It
is a hint and never a mark: nothing is wrong, so `canSubmit` is untouched and the
shopper submits the number or clears it. A cleared box is not filled in again --
re-offering a figure somebody deleted is enforcing it slowly -- and an answer
about a request the box no longer holds is dropped, the way the sources check's
is.

It remembers the advanced settings in `localStorage` and the request
deliberately not -- what to shop for is a new question every time -- and every
read and write is wrapped, so a browser that refuses storage still gets a
working form. One setting is deliberately not remembered: `pay` itself. The
others are standing answers about this machine, and "you may spend my money" is
not one of them; a browser that restored it would arm the next visit's run with
nobody having said so.

Two of its pickers are read out rather than reasoned about. The **Search
backend** field says where that backend is asked and what it is missing, off the
`configured` and `endpoint` its row shipped, so a fourth backend added in Python
says its piece here without a second list of names in TypeScript
([ADR-0057](../docs/adr/0057-a-search-backend-is-a-row-in-a-table.md)). **Count
prices in** is the scale the run's prices are compared on, and its blank is a
value rather than a missing one -- "whatever the pages quote", which is what the
set voting on its own means and what the field defaults to
([ADR-0056](../docs/adr/0056-let-the-shopper-name-the-currency.md)). It is also
what the Max price and Spend limit boxes name themselves in: a budget's number is
the shopper's and its currency is not, so the hint under each says which one it
is being read on. The codes are Python's table, sent with the defaults, for the
reason every other list here is.

That scale then travels with the finished run. `App` sends it back on a re-sort
and on a payment, out of the settings the run was started with: both are handed
the products by the browser (ADR-0035), so a set left to vote again could come
back in a different order, or priced into a different cart, for one run.

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
all (`sent()` reads it as the cleared box it is drawn as), because it is either
disabled or not drawn at all, and a mark on a box nobody can type into is one
nobody can act on -- a form that will not search pointing at a field that cannot
be answered. Switching to a vLLM over a context window the form had already
refused, or turning paying off over a spend limit it had, was exactly that.

`notes()` is what is shown under each field: `problems()`, plus the `rejected`
input for a field the page has no rule for, which is the `field` a `failure`
event named. The server's mark does not gate the button, being about what was
sent, and it is shown only while the box still holds what was sent: `submitted`
keeps the payload each run went out with, and a mark that outlived the mistake
was a red field, an `aria-invalid` and a "1 setting to look at" over a form with
nothing wrong with it. `options()` is the one place that payload is built, since
the comparison and the run have to agree on what a box holds.

A marked box also *points* at its sentence. `aria-invalid` alone is the box
saying something is wrong and never what, and a live `role="alert"` is announced
once as it appears and is a paragraph beside a box from then on -- so a reader
arriving at a box already marked, by a remembered value or by tabbing back to
it, was told there was a problem and not which. `problemId` answers the id of
that sentence, or nothing where nothing marks the box, and it is read at both
ends of the pointer: the sentence is given that id and the box is described by
it, so the two cannot drift into an `aria-describedby` naming an element that is
not there. That is what puts `duplicate-id-aria` among the rules the
accessibility check runs -- two boxes sharing one id is a mark read at random --
and it is why a box this run does not take loses its `aria-describedby` with its
`aria-invalid` rather than keeping a pointer to a sentence no longer drawn.

The mark is the only one of the three sentences a box can carry that needs the
pointer, and that is the reason rather than a preference. Each box is inside its
own label, so a `<small>` under it is part of what the box is *called*: the hint
and the bound read out of the request (ADR-0059) are read out with the name,
which is where a hint belongs. A live `role="alert"` is the one that is not --
it is excluded from that name, announced as it appears and unreachable
afterwards -- so it is the one the box has to point at.

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
names, its label, its step, its hint and whether it belongs to the paying block
-- and the template loops over it rather than repeating the same twenty lines of
markup per setting, through one `ng-template` both loops draw. That last column
is a partition and not a second table: the spend limit is the one number a
shopper sets about *paying*, so it is drawn beside the switch that turns it on
with the other two, rather than four rows above it under a sentence naming a
control the reader has to go looking for -- which on a phone is off the screen
entirely.
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
and `takes_cpu_only` on that row are what disable the context field and the
CPU-only box and replace their notes, rather than the form testing the
provider's name.

## Testing it

Angular components are tested in jsdom with `TestBed`, and `AgentService`
against a fake `EventSource` rather than a live one. The Python server's own
tests inject a stub agent through `create_server(agent_factory=...)`.

Each of the four is also held to an accessibility check, because everything
argued above is a claim about what somebody can perceive and reach: a refusal
drawn on the box it is about
([ADR-0033](../docs/adr/0033-let-the-form-refuse-what-the-server-would.md)), a bound
offered and never marked
([ADR-0059](../docs/adr/0059-notice-a-bound-in-the-request-and-offer-it.md)),
a `movement` that is a word to colour by and never one to compose from. Every
one of those is green under a spec asserting a CSS class, and none of them is a
promise kept to a reader who cannot see the colour.

`src/app/a11y.ts` is what the four specs call, and the rules it runs are named
one at a time, each with the sentence saying which promise it holds -- which is
`.pylintrc`'s argument
([ADR-0049](../docs/adr/0049-load-the-optional-pylint-checkers.md))
one language over. A blanket `axe(element)` over all 105 of its rules would fail
on rules nobody here has decided about and pass on the ones jsdom cannot answer.
What is left out is written down beside what is in: the rules that want a
browser, the rules about a whole document where three of the four subjects are
one component of it, and the rules about markup this app has none of. A rule
that ran and could not decide counts as a rule that did not pass, which is how
the check reported the one defect it found on the day it was written -- an
`aria-label` on a `<div>`, an element whose role forbids it to carry a name, so
"Rank 1" was an announcement no reader owed anybody.

Two of the promises no rule of axe's states, so they are asserted where they are
made: that the progress panel's lines land in a live region, and that a level
the panel gives a colour to is a level it also names. And there is one dev
dependency for all of it rather than two -- `axe-core` itself, with the
assertion written here. The vitest matcher for it pins itself to an axe two
major versions back and brings five packages along to format a message, which
is a sentence `a11y.ts` writes in three lines.

What the check cannot do here still needs a browser: contrast is pixels and a
target's size is layout. That gap is the shape of the one the CSP and the
critical-CSS inliner already have -- neither suite can see those either.
