# ADR-0046: Pay on the shopper's behalf, with AP2 mandates and a rail table

- **Status:** Accepted
- **Date:** 2026-09-06

## Context

The agent stops at a report. It searches, reads pages, extracts products, drops
every figure the sources do not back (ADR-0006), ranks what is left and logs the
top three -- and then the shopper opens another tab and buys the thing. The last
step is the one the agent could plausibly do, and the one it has no safe way to
do.

The obvious way is the wrong one. An agent that holds a card number and posts it
to whatever checkout it found has three problems at once: the merchant cannot
tell an agent's purchase from a person's, so it carries the fraud risk of a
card-not-present transaction with none of the signals; the shopper has no
evidence of what they agreed to, because "I said buy headphones" and "you paid
329.99 to this shop" are different claims and nothing links them; and this
pipeline's own central rule -- that nothing is reported unless a source page
printed it -- would be quietly abandoned at the one step where being wrong costs
money rather than credibility.

[AP2](https://ap2-protocol.org) is the protocol built for exactly that gap. It
is Apache-2.0, developed in the open, and framework-neutral: its *samples* use
Google's ADK and Gemini, and its SDK uses neither -- pure Python over
`cryptography`, `jwcrypto` and `sd-jwt`. It defines five roles and two signed
mandates, and it says nothing at all about who you send them to: "the exact
details of the Commerce Protocol are outside the scope of AP2". That last
sentence is what makes it adoptable here without picking a payment vendor.

Three things about this project shaped how it lands.

**A run raises exactly three failures (ADR-0009)**, and that agreement is checked
across `BuyAgent.run`'s docstring, `api._STATUS` and the CLI's `except` tuple. A
purchase is a fourth kind of failure, and folding it in would either widen that
contract or hide a payment failure inside `ValueError`.

**The browser decides nothing (ADR-0012)**, and a Pay button is the strongest
possible temptation to let it. What a cart contains, what it costs and whether it
may be bought at all are exactly the judgements a page must not be trusted with.

**A price this run cannot place is a price it does not judge (ADR-0039,
ADR-0043)** -- the shopper's bounds keep a product whose figure is unknown or in
another currency, because dropping it would punish the extractor's miss rather
than the product. That is right for a filter and wrong for money.

## Decision

**Paying is a step after a run, never a step in one.** `BuyAgent.run` is
untouched: no new checkpoint, no new entry in its `Raises:`, no fourth row in
`api._STATUS`. Both front doors call `payment.pay_for` themselves, once, on one
product, after a separate decision. `payment.PaymentError` is that step's own
failure, mapped by its own table (`api.PAY_STATUS`) and caught in its own place
in the CLI.

**It is optional, off, and its dependency is optional too.** `AgentConfig.pay`
defaults to `False`. The AP2 SDK is installed separately
(`requirements-ap2.txt`, with `--no-deps`), `buy_agent.mandates` imports it
lazily, and a build without it has a working `--help`, a working run and a form
that says paying is unavailable rather than a button whose only outcome is a
message about pip.

**Never pay on an unverified number.** ADR-0006's rule, turned around.
`payment._check` refuses a product whose price grounding blanked, whose currency
no page printed, whose price is in a currency this run cannot place, or which
has no source page to name a merchant from. Every one of those is already the
answer to "did a source say so", which is why the check is four lines and not a
policy engine. Where a filter keeps what it cannot judge, money does the
opposite: an amount nobody can place is not an amount to send.

**Both AP2 modes, and the mode is not a flag.** Human Present is the default: the
shopper approves the exact cart at the exact price, and the closed Checkout and
Payment Mandates are signed directly, which is what the specification means by a
signature "coming from a User directly". Human Not Present is reached by pointing
`$BUY_AGENT_AP2_MANDATE` at a pre-signed *open* mandate; the agent closes it
within its constraints, evaluated by the SDK's own evaluator. There is no second
switch, because the open mandate **is** the authorisation -- a flag saying "run
unattended" would only fail without the file anyway.

**A rail is one row in one table.** `rails.RAILS` holds each counterparty whole,
the way `providers.PROVIDERS` holds each model server (ADR-0029): where it
listens (from its own environment variable), whether it needs an address and an
enrolled key, how a cart becomes a merchant-signed checkout, how an authorisation
is presented, which transport failures mean "not there", and the sentence one
carries. `AgentConfig.rail_used` is the only place a rail name becomes behaviour;
there is no `if rail == ...` above this module. Two rails ship: `dry-run`, the
default, which plays every role, signs a genuinely verifiable chain and charges
nobody; and `http`, which speaks to whatever AP2-speaking endpoint the operator
names. **No merchant, wallet, processor or cloud is named anywhere in this
project.**

**The page witnesses consent; it does not assert it.** `POST /api/pay` runs no
pipeline, the way `POST /api/rank` runs none (ADR-0035). The browser posts the
run's products, which one to buy, and `approved` -- an echo of the title, price
and currency it put in front of a person. The server builds the cart itself from
those products and refuses unless the echo matches. So a page showing a stale
price cannot buy at that price, and a page that asked nobody cannot guess the
right echo. On the CLI the same surface is a typed `yes` at a terminal, and a run
with no terminal is **refused**: silence is not consent.

**A receipt never carries the mandate chain.** A chain authorises this purchase
to whoever holds it until it expires, and a receipt is logged, sent to a browser
and saved to a file. `reference` -- the SHA-256 of the closed leaf JWT -- is what
points back at it, which is what AP2 says a receipt binds by.

## Consequences

What it buys: the shopper's authorisation is a signed artefact naming one cart at
one price from one merchant, rather than a card number and a hope. A merchant can
verify that this agent was allowed to buy this; the shopper can hold the run to
what they approved; and an unattended purchase is bounded by constraints they
signed rather than by the agent's judgement.

What it costs: four settings, three modules, an optional dependency that is not
on PyPI, and a genuinely new class of failure in a project that had three. The
AP2 SDK's published metadata pins `pydantic` and `pytest` to versions this
project does not use, which is why it is installed with `--no-deps` from a
separate requirements file and its real imports are pinned there instead. That
wart is the price of tracking the reference implementation rather than writing
our own SD-JWT layer, and writing our own is the worse trade: interoperating with
real verifiers would become our bug to find.

What it obliges:

- **A third rail is a row in `rails.RAILS` and nothing else.** A branch on a
  rail's name anywhere above that module is the thing this record exists to
  prevent, and `tests/test_conventions.py` holds the table against `--rail`,
  `api.RAIL_OPTIONS` and the form's picker.
- **`buy_agent.mandates` stays the only importer of `ap2`.** A second one is a
  second place a missing optional dependency becomes an `ImportError`; a
  convention test checks it.
- **The payment's failures stay out of `_STATUS`.** ADR-0009's three-failure
  agreement is about `BuyAgent.run`. A payment failure added there would make the
  run promise something it does not raise.
- **A new payment field is mirrored in `agent.types.ts`.** `Receipt`,
  `RailOption` and `PayOptions` are held against their Python payloads field for
  field, the way every other payload already is.
- **The approval echo must keep comparing what a person was shown.** If the
  confirmation ever restates something the server does not check -- a merchant, a
  delivery date -- then the page is deciding it, and ADR-0012 is broken at the
  one endpoint where it costs money.
- **`payment._check` is where "may this be bought" lives, and only there.** The
  card's button, the CLI's prompt and the payment itself all ask that one
  function; a second reading of it in TypeScript would offer a button the server
  refuses.
