# buy_agent UI

An Angular front end for the agent: a second way into the same `BuyAgent.run()`
the CLI drives. Searching, grounding and ranking all happen in Python.

## Running it

```powershell
python -m buy_agent.server            # http://127.0.0.1:8000, the API and the build
cd ui
npm install
npm run build                         # writes dist/ui/browser, which the server serves
npm start                             # dev server on :4200, proxying /api (proxy.conf.json)
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

- **A run is streamed.** `AgentService.search()` wraps an `EventSource` on
  `/api/search/stream`, ending on `result` or `failure`; unsubscribing is Stop.
  The failure event is not called `error`, because `EventSource` reconnects on
  `error` and would start the search again.
- **Nothing here decides an answer.** The payload carries `price_label`,
  `rating_label` and the rest beside the raw numbers, and sorting is a request.
- **The form refuses a bad setting first, on the server's rules**
  ([ADR-0033](../docs/adr/0033-let-the-form-refuse-what-the-server-would.md)).
  Ranges arrive with `/api/config`; sources are checked by `/api/sources`. A
  `failure` naming a field marks that box, and the mark and banner leave together
  once the box is typed over.

The rules are in the repository's `CLAUDE.md`; this is the reasoning behind them.

## The header pill

It says whether the model server answered. `App.unreachable` is Python's `hint`,
shown as a line under the pill (a `title` is hover-only) with a **Check again**
button. While a listing is in flight the pill says **Asking &lt;label&gt;…**,
naming the server in `App.asking`, and the picker stands down. A run that
contradicts the pill -- results while it said unreachable, a 503 while it said up
-- asks again.

## `progress-log`

It follows the tail only while the reader is at the bottom (`sticking`). `App`
scrolls it into view with the run's first line (`nearest`), and it reserves the
height it will grow to, since with Settings open the form alone fills a laptop
screen. A failure's banner is scrolled to; a refusal is not, because the form
marks and opens the box it names.

**Download log** is offered only for a failed or stopped run, which leave nothing
else on the page; `transcript()` adds the failure message and whole logger names.
`elapsed()` counts the wait once a second (`8s`, `2m 14s`), since extraction logs
nothing for minutes. `App.tabTitle` puts **Searching…**, **7 found**, **Nothing
found**, **Failed** or **Stopped** in the tab title, without a per-second clock.

## `product-card`

Buying takes **two** clicks. The second restates the *cart* -- title, the cart's
`pay_label` (a bare "329.00" still has a currency to be paid in,
[ADR-0043](../docs/adr/0043-compare-prices-only-within-one-currency.md)),
`pay_merchant` (most pages print no seller), the rail, and whether anyone is
charged. A product that may not be bought shows Python's `cannot_pay`. While
paying, the card in `paying` (a name, not a flag) says "Authorising … with …"
and every other button stands down; then the receipt says "Paid" or
"Authorised".

Each step replaces the block before it, button included, so focus that was in
the payment area moves to what replaced it (`LANDING`): the cart, never the button
that buys, so Enter twice cannot skip the confirmation; Pay again; the wait; the
receipt. Focus the reader moved elsewhere stays there, and nothing scrolls.

These blocks use only tokens `styles.css` declares, each with a dark value; a
convention test holds every stylesheet to that, since jsdom measures no contrast.

Receipts belong to the product, not its rank: a re-sort renumbers from 1
([ADR-0035](../docs/adr/0035-re-sort-a-finished-run-without-running-it-again.md)),
so `App.receipts` and both loops are keyed by name.

Under the figures it opens onto what each page priced it at: Python's
`offers_label` and each offer's `price_label`, each linking its page
([ADR-0058](../docs/adr/0058-keep-every-listing-a-product-was-priced-at.md)).

On its right is a picture of the page it links to, where the server's
`screenshots` says it takes them
([ADR-0065](../docs/adr/0065-photograph-each-products-page-from-a-server-bound-to-this-machine.md))
and the product links somewhere. It is `loading="lazy"`, reserves 640 x 400 in a
grid column that exists only with a picture, and a failed image drops the frame,
remembered by address. On a phone it goes under the text.

## `search-form`

**Noticed bounds.** `GET /api/bounds` reads "under $200" out of the request, and
the matching box is filled once, only while empty, under Python's note
([ADR-0059](../docs/adr/0059-notice-a-bound-in-the-request-and-offer-it.md)).
It is a hint, never a mark, and a cleared box is not refilled. The request is
read on `change`, which Enter fires on its way to submitting, so a submit waits
for a reading on its way (`reading`, `held`). If the reading filled a box, it
sends nothing, focuses that box and brings its whole field into view, where a
line under Python's note says nothing was searched yet (`stoppedAt`); pressing
again searches with it. `App`
answers a failed reading as one that noticed nothing. The note sits above the
box's hint, which says the currency the figure is read in.

**Storage.** Settings are remembered in `localStorage` and the request is not.
Every call is wrapped. `pay` is never remembered: "you may spend my money" is not
a standing answer. Nor are the three bounds, which belong to the request they
were set for: restored into a shut panel, one filtered the next visit's search
for something else, and nothing on the page said so. A row declares `remembered:
false` and is neither written nor read back
([ADR-0077](../docs/adr/0077-keep-the-shoppers-bounds-out-of-what-the-form-remembers.md)).

**Pickers.** The search backend's note comes from its row's `configured` and
`endpoint` ([ADR-0057](../docs/adr/0057-a-search-backend-is-a-row-in-a-table.md)).
**Count prices in** has a blank meaning "whatever the pages quote"
([ADR-0056](../docs/adr/0056-let-the-shopper-name-the-currency.md)), and names
the currency the budget boxes are read in. `App` sends the run's currency, or its
voted `scale`, back with a re-sort or payment, since a re-vote can break a tie the
other way. The payment block is drawn only when `pay_available`, and its fields
only once paying is ticked; `moves_money` and `needs_endpoint` come off the rail's
row.

**Refusals.** `problems()` checks each number against the server's ranges and the
sources against the last `/api/sources` answer, and gates `canSubmit`. A field
whose `off()` is true is neither checked nor sent. `notes()` adds the server's
`rejected` field while the box still holds what `submitted` recorded, and
`options()` is the one place the payload is built. A mark is `aria-invalid` plus
`aria-describedby` pointing at the sentence's `problemId`; hints and bound notes
need no pointer because each box sits inside its label. `numberTyped` reads
`validity.badInput`, since a box of non-numbers otherwise reaches `ngModel` as
`null`, the cleared box
([ADR-0012](../docs/adr/0012-the-browser-decides-nothing.md)). Sources are checked
on `change` and after `restore`.

**Number boxes** are declared once in `numberFields` (key, label, step, hint,
whether it belongs to paying) and drawn by one `ng-template`. Its keys are held
against `limits_payload` by a convention test. `placeholders()` names each box's
default. A mark opens the Settings panel once per change of marks; closing it is
the reader's.

**The model field** is a `<select>` over `GET /api/models`. A configured model
not served stays as "not served"; one with no `completion` capability is
"embedding only" (`ModelOption.note`, from Python's `completion`). An empty list
falls back to a text box. While `checking`, it is disabled and names the server it
is waiting on. Editing the address emits `refresh` with a `ModelSource` (provider
and address), and changing the provider refills model and address first. An
address that is this server (vLLM's `:8000`) is explained by the server and
refused on `base_url`. `takes_num_ctx` and `takes_cpu_only` disable the fields
that do not apply.

## Testing it

Components are tested in jsdom with `TestBed`, and `AgentService` against a fake
`EventSource`. Each component also runs `src/app/a11y.ts`, which applies
`axe-core` rules one at a time, each with the promise it holds
([ADR-0049](../docs/adr/0049-load-the-optional-pylint-checkers.md)'s argument,
one language over), rather than a blanket run of all 105. Rules needing a browser
or a whole document, or about markup the app lacks, are listed as left out. An
inconclusive rule fails. Two promises no axe rule states are asserted directly:
log lines land in a live region, and a coloured level is also named. Contrast and
target size need a browser.
