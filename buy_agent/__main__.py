"""Command line entry point:  python -m buy_agent "wireless headphones under $200"."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, get_args

from buy_agent import mandates, payment
from buy_agent.agent import BuyAgent, ModelUnavailableError
from buy_agent.api import results_payload
from buy_agent.chat import release
from buy_agent.config import (
    DEFAULT_PROVIDER,
    DEFAULT_RAIL,
    LIMITS,
    AgentConfig,
    parse_region,
)
from buy_agent.logging_setup import configure_logging
from buy_agent.models import RankedProduct
from buy_agent.payment import PaymentError
from buy_agent.providers import PROVIDERS, provider_for
from buy_agent.rails import RAILS, rail_for
from buy_agent.ranking import SortBy
from buy_agent.search import SearchError
from buy_agent.sources import parse_named_sources, parse_sources

logger = logging.getLogger("buy_agent")

def _defaults() -> AgentConfig:
    """Every flag's default, off one config so the two cannot drift apart.

    Built on a provider and a rail that exist rather than on ``$BUY_AGENT_PROVIDER``
    and ``$BUY_AGENT_RAIL``, which are a shopper's to misspell: resolved at import
    time, a bad one was a ``ValueError`` out of importing this module, with ``--help``
    and its list of the names there are unreachable too. Both are still read below,
    where ``_checked`` turns either into the usage error it deserves.
    """
    return AgentConfig(
        provider=DEFAULT_PROVIDER if DEFAULT_PROVIDER in PROVIDERS else next(iter(PROVIDERS)),
        rail=DEFAULT_RAIL if DEFAULT_RAIL in RAILS else next(iter(RAILS)),
    )


_DEFAULTS = _defaults()

#: Exit code for a run that worked and found nothing. Its own code because a
#: shell cannot otherwise tell it from a stopped model server; 2 is argparse's.
NOTHING_FOUND = 3

#: Exit code for a run that was asked to pay and did not. Its own code because
#: the report on stdout is real either way -- a script reading 0 here would file
#: the products and never learn that nothing was bought.
PAYMENT_FAILED = 4

#: What ``--num-ctx`` holds when it was not given. A sentinel rather than the
#: config's default: "8192" and "the default, which is 8192" are the same number
#: and different requests, and only the first is worth a warning.
_UNSET = object()


def _provider_defaults(setting: str) -> str:
    """One column of :data:`buy_agent.providers.PROVIDERS`, as ``--help`` prints it.

    ``--model`` and ``--base-url`` have a default per provider, so the help names them
    all. Read off the table, so a third provider appears here by being added there.
    """
    return ", ".join(
        f"{getattr(server, setting)} for {name}" for name, server in PROVIDERS.items()
    )


def _bounded(kind: Callable[[str], Any], field: str) -> Callable[[str], Any]:
    """``--results`` and the rest, held to the range the API holds them to.

    Read off :data:`buy_agent.config.LIMITS` so the two front ends cannot disagree:
    unchecked, ``--results 0`` reads ten pages to ask the model for no products.
    Checked here rather than after parsing, so it is a usage error printed with the
    flag that carries it.
    """
    minimum, maximum = LIMITS[field]

    def parse(text: str) -> Any:
        value = kind(text)  # argparse turns the ValueError into "invalid value"
        if not minimum <= value <= maximum:
            raise argparse.ArgumentTypeError(
                f"must be between {minimum} and {maximum}; got {value}"
            )
        return value

    # argparse names the type in its own message for anything this does not
    # catch, and "invalid int value" is what a mistyped number deserves to read.
    parse.__name__ = kind.__name__
    return parse


def _checked(check: Callable[[str], object]) -> Callable[[str], str]:
    """A flag's value as argparse takes it: refused here, and kept as written.

    Four settings are judged before the run -- a source, a provider, a rail and a
    region -- each by the same function a run would have used. The wrapper is what
    argparse needs: a ``ValueError`` out of a ``type`` function becomes "invalid
    value" with the sentence thrown away, and the sentence is the whole message.

    Checked *here* because two of the four otherwise fail quietly: a source naming no
    site and a region no engine knows both come back as an empty report with nothing
    to explain it (ADR-0027, ADR-0031). The other two because a ``type`` function also
    runs over a string *default*, where ``choices`` does not --
    ``$BUY_AGENT_PROVIDER=olama`` sailed past ``choices`` and reached
    ``AgentConfig``, outside the ``try`` that names the three failures a run has.

    The text comes back as typed rather than as ``check`` read it, so ``main`` parses
    every ``--source`` together and the region is lower-cased where every other caller
    lower-cases it.
    """

    def parse(text: str) -> str:
        try:
            check(text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc
        return text

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buy_agent",
        description="Search the web for what you want to buy, rank it, log the best.",
        # --help is the only documentation the CLI has, so the split between the
        # two streams and the codes a script branches on are both worth saying.
        epilog=(
            "The report is written to stdout and the progress to stderr, so "
            "`... > top.txt` keeps the report and leaves the narration on screen.\n"
            "\n"
            "Exit codes:\n"
            "  0  products were found and reported\n"
            "  1  the run failed -- the reason is the last line on stderr\n"
            "  2  the command line could not be understood\n"
            f"  {NOTHING_FOUND}  the run worked and found nothing\n"
            f"  {PAYMENT_FAILED}  --pay was asked for and nothing was bought\n"
            "  130  interrupted with Ctrl-C\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("request", help="What you want to buy, in plain words.")
    parser.add_argument(
        "--provider",
        type=_checked(provider_for),
        choices=tuple(PROVIDERS),
        default=DEFAULT_PROVIDER,
        help=f"Which model server to talk to (default: {DEFAULT_PROVIDER}, override "
        "with $BUY_AGENT_PROVIDER). It decides what --model and --base-url mean.",
    )
    # Both default to "" rather than a value: which one is right depends on
    # --provider, which argparse has not read yet. The config resolves an empty one
    # per provider, so the help quotes every pair.
    parser.add_argument(
        "--model",
        default="",
        help="Model to use, empty for the provider's own default "
        f"({_provider_defaults('model')}). Override with $OLLAMA_MODEL or $VLLM_MODEL.",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="Model server URL, empty for the provider's own default "
        f"({_provider_defaults('base_url')}). Override with $OLLAMA_HOST or $VLLM_HOST.",
    )
    parser.add_argument(
        "--results",
        type=_bounded(int, "num_products"),
        default=_DEFAULTS.num_products,
        help=f"How many products to find (default: {_DEFAULTS.num_products}).",
    )
    parser.add_argument(
        "--top",
        type=_bounded(int, "top_n"),
        default=_DEFAULTS.top_n,
        help=f"How many products to log (default: {_DEFAULTS.top_n}).",
    )
    parser.add_argument(
        "--sort-by",
        # Read off the type: rank_products has a branch per criterion, and a
        # fourth must not be offered here without one there.
        choices=get_args(SortBy),
        default="score",
        help="Ranking criterion (default: score, a blend of rating, reviews and price).",
    )
    parser.add_argument(
        "--region",
        type=_checked(parse_region),
        default=_DEFAULTS.region,
        help="Search region: a country and then a language, hyphenated (default: "
        f"{_DEFAULTS.region}; also uk-en, pl-pl). Anything else is a usage error, "
        "since a region no search engine knows returns nothing at all.",
    )
    parser.add_argument(
        "--source",
        action="append",
        metavar="SITE",
        # ``parse_named_sources`` rather than ``parse_sources``: on a command
        # line "unset" is spelled by leaving the flag off, so ``--source ""`` is a
        # mistake. Left to parse, it came back empty and the run searched the
        # whole web -- the opposite of what was asked for.
        type=_checked(parse_named_sources),
        help="Take the facts from this source only; repeat for several. A site "
        "(rtings.com), a section of one (rtings.com/headphones) or a YouTube "
        "handle (@mkbhd). Without it the whole web is searched.",
    )
    parser.add_argument(
        "--max-price",
        type=_bounded(float, "max_price"),
        default=_DEFAULTS.max_price,
        help="Report nothing dearer than this (default: no limit). Read in the "
        "currency the run's prices are counted in -- the commonest one the pages "
        "quote -- and nothing is converted, so a price in another currency is one "
        "this cannot judge and does not. A product whose price no page printed is "
        "kept too: a blank is the extractor's miss, not a $900 tag.",
    )
    parser.add_argument(
        "--min-rating",
        type=_bounded(float, "min_rating"),
        default=_DEFAULTS.min_rating,
        help="Report nothing rated below this, out of 5 (default: no limit). "
        "Unrated products are kept, for the reason unpriced ones are.",
    )
    parser.add_argument(
        "--min-reviews",
        type=_bounded(int, "min_reviews"),
        default=_DEFAULTS.min_reviews,
        help="Report nothing whose rating was averaged over fewer reviews than "
        "this (default: no limit). A 5.0 from two people is not a rating.",
    )
    parser.add_argument(
        "--cache-ttl",
        type=_bounded(float, "cache_ttl"),
        default=_DEFAULTS.cache_ttl,
        metavar="SECONDS",
        help=f"How long a fetched page, and the model's answer about it, stay "
        f"usable on disk (default: {_DEFAULTS.cache_ttl:g}, a day; 0 reads every "
        "page off the web and asks the model every question). Most of a repeated "
        "run is opening the same pages again and asking the same thing about "
        "them. A run at a temperature above 0 is never remembered. "
        "$BUY_AGENT_CACHE_DIR says where it is all kept.",
    )
    parser.add_argument(
        "--pay",
        action=argparse.BooleanOptionalAction,
        default=_DEFAULTS.pay,
        help="Buy the top-ranked product once you have approved it (default: "
        "--no-pay). The purchase is authorised with signed AP2 mandates rather "
        "than a stored card, and only a product whose price a source actually "
        "printed can be paid for. With $BUY_AGENT_AP2_MANDATE naming a pre-signed "
        "open mandate this runs unattended, within that mandate's constraints; "
        "without one you are asked, and a run with nothing to type into is "
        "refused rather than assumed.",
    )
    parser.add_argument(
        "--rail",
        type=_checked(rail_for),
        choices=tuple(RAILS),
        default=DEFAULT_RAIL,
        help=f"Who to pay through (default: {DEFAULT_RAIL}, override with "
        "$BUY_AGENT_RAIL). The default signs a real authorisation and charges "
        "nobody, so --pay on its own never spends anything.",
    )
    parser.add_argument(
        "--merchant-url",
        default="",
        help="The AP2-speaking endpoint a paying rail talks to, empty for the "
        "rail's own default ($BUY_AGENT_MERCHANT_URL). It is asked for a signed "
        "checkout at {url}/checkout and presented the mandates at {url}/payment.",
    )
    parser.add_argument(
        "--spend-limit",
        type=_bounded(float, "spend_limit"),
        default=_DEFAULTS.spend_limit,
        metavar="AMOUNT",
        help="Refuse to pay more than this for one product (default: no limit), "
        "in the currency the run's prices are counted in. Unlike --max-price, a "
        "price this run cannot place fails it: a bound that cannot judge a "
        "candidate keeps it, but an amount nobody can place is not one to send.",
    )
    parser.add_argument(
        "--temperature",
        type=_bounded(float, "temperature"),
        default=_DEFAULTS.temperature,
        help=f"Model temperature (default: {_DEFAULTS.temperature}).",
    )
    parser.add_argument(
        "--num-ctx",
        type=_bounded(int, "num_ctx"),
        # The sentinel, not the value: the help below names the default either
        # way, and only a number actually typed is worth a warning.
        default=_UNSET,
        help=f"Context window in tokens (default: {_DEFAULTS.num_ctx}). The "
        "extraction prompt runs to ~4.3k tokens, so a larger window leaves room for "
        "more products; a model that need not think is fine on Ollama's own 4096. "
        "Ollama only -- vLLM fixes its window with --max-model-len when it starts.",
    )
    parser.add_argument(
        "--think",
        action=argparse.BooleanOptionalAction,
        default=_DEFAULTS.reasoning,
        help="Force the model's thinking mode on or off (default: --no-think). "
        "Thinking models need --no-think: they reason until the context runs out and "
        "never answer; a model that cannot think ignores either.",
    )
    parser.add_argument(
        "--no-fetch",
        dest="fetch",
        action="store_false",
        help="Extract from search snippets only, without opening the result pages "
        "(much faster, but snippets rarely quote a price).",
    )
    parser.add_argument(
        "--json",
        type=Path,
        # A path and not a format: left to argparse the flag read "--json JSON",
        # which is the one metavar on here that says the value again instead of
        # saying what it is -- and reads like a switch asking for JSON on stdout,
        # which is where the report already goes.
        metavar="FILE",
        help="Also write all results, not only the top ones, to this JSON file.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    return parser


def _approved(cart: payment.Cart, config: AgentConfig) -> bool:
    """Ask the shopper to approve this exact cart, and mean it.

    AP2's Trusted Surface, small: the surface showing a person what they are agreeing
    to before anything is signed. So it restates the cart the mandates will carry --
    the title, the price, the merchant and which rail -- rather than the request that
    found it, and says whether the rail can charge anybody.

    The prompt goes to stderr and the answer is read off stdin, keeping the report on
    stdout a report. A run with nothing to type into is **refused**: silence is not
    consent, and a script piping in nothing would otherwise have bought something.

    Raises:
        PaymentError: if nobody could have answered.
    """
    if not sys.stdin.isatty():
        raise PaymentError(
            f"Paying {cart.label()} for {cart.title} needs your approval, and this "
            f"run has no terminal to ask at. Run it where you can answer, or "
            f"authorise it in advance with a pre-signed open mandate at "
            f"${mandates.MANDATE_PATH}."
        )
    rail = config.rail_used
    charge = "will be charged" if rail.moves_money else "will NOT be charged"
    sys.stderr.write(
        f"\n  Pay {cart.label()} for {cart.title}\n"
        f"    merchant  {cart.merchant}\n"
        f"    page      {cart.url}\n"
        f"    rail      {rail.label} -- you {charge}\n"
        f"  Type yes to authorise: "
    )
    sys.stderr.flush()
    return sys.stdin.readline().strip().lower() in {"y", "yes"}


def _bought(ranked: list[RankedProduct], config: AgentConfig) -> bool:
    """Buy the top-ranked product, and say what came of it.

    The top one and not a choice of one: the report is already an ordering, and a flag
    naming a rank would be a second way of saying what ``--sort-by`` said. Everything
    that can go wrong is one failure with one sentence
    (:class:`~buy_agent.payment.PaymentError`), caught in its own place rather than
    added to the three a *run* raises.
    """
    products = [entry.product for entry in ranked]
    try:
        cart = payment.cart_for(products[0], products, config)
        if not payment.unattended() and not _approved(cart, config):
            logger.warning("Not paid: %s was not approved.", cart.title)
            return False
        receipt = payment.pay_for(cart, config)
    except PaymentError as exc:
        logger.error("%s", exc)
        return False

    logger.info("%s", receipt.detail)
    logger.info(
        "Authorisation %s covers %s at %s from %s",
        receipt.reference,
        receipt.title,
        receipt.price_label,
        receipt.merchant,
    )
    return True


# ``parser.error`` exits rather than returning, so the ``except`` below ends the
# process and pylint reads it as a branch that falls off the end with no value.
# pylint: disable-next=inconsistent-return-statements
def _configured(parser: argparse.ArgumentParser, **settings: Any) -> AgentConfig:
    """The run's config, with the one thing it refuses said the way a flag is.

    ``AgentConfig`` checks what no single flag can: a rail that moves money and has
    nowhere to send it, which is two flags and an environment variable between them.
    Every other setting judged before the run is refused by a ``type`` function
    (:func:`_checked`); left to escape, this one came out of ``main`` as a traceback.

    Raises:
        SystemExit: argparse's own, code 2, carrying the config's sentence.
    """
    try:
        return AgentConfig(**settings)
    except ValueError as exc:
        parser.error(str(exc))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose)

    config = _configured(
        parser,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        temperature=args.temperature,
        num_ctx=_DEFAULTS.num_ctx if args.num_ctx is _UNSET else args.num_ctx,
        reasoning=args.think,
        search_results=max(args.results, args.top),
        num_products=args.results,
        top_n=args.top,
        max_price=args.max_price,
        min_rating=args.min_rating,
        min_reviews=args.min_reviews,
        cache_ttl=args.cache_ttl,
        region=args.region,
        # Repeated flags build a list; no flag leaves None, and the fallback is
        # the config's own default rather than an empty one written down again.
        sources=parse_sources(args.source) if args.source else _DEFAULTS.sources,
        fetch_pages=args.fetch,
        pay=args.pay,
        rail=args.rail,
        merchant_url=args.merchant_url,
        spend_limit=args.spend_limit,
    )

    if args.num_ctx is not _UNSET and not config.model_server.takes_num_ctx:
        # The form disables the field; the CLI has none to disable, so it says so
        # here rather than dropping the number without a word.
        logger.warning(
            "%s fixes its context window when it starts (--max-model-len), so "
            "--num-ctx %s is ignored on this run.",
            config.model_server.label,
            args.num_ctx,
        )

    agent = None
    try:
        agent = BuyAgent(config)
        ranked = agent.run(args.request, sort_by=args.sort_by)
    except (ModelUnavailableError, SearchError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logger.warning("Interrupted.")
        return 130
    finally:
        # The agent is this run and nothing after it, so its connection is let
        # go of here rather than whenever the process ends. Built inside the
        # guard: a provider that refuses the config is one of the three failures
        # above, and ``None`` is an agent that was never built. Asked rather than
        # called outright, this being where the tests put a stand-in.
        release(agent)

    if args.json:
        # Written even when the run found nothing, and so before the exit code
        # is decided: skipped, a script waiting on this file finds the last run's
        # results looking current. The API's own shaping, not a second one.
        payload = results_payload(ranked)
        try:
            args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            # Worth an exit code and not a traceback: the report is already on
            # stdout, so what failed is the copy.
            logger.error("Could not write %s (%s)", args.json, exc)
            return 1
        logger.info("Wrote %d products to %s", len(payload), args.json)

    # After the report and the file: both are true whatever the payment does,
    # and a failed purchase must not cost the shopper the answer.
    if args.pay and ranked:
        try:
            bought = _bought(ranked, config)
        except KeyboardInterrupt:
            # Ctrl-C at the approval prompt is somebody deciding not to buy, and
            # the prompt is where a shopper hesitates -- so it is answered as a
            # Ctrl-C anywhere else in this run is. A traceback reads as a crash at
            # the one point where what matters is whether money moved.
            logger.warning("Interrupted. Nothing was bought.")
            return 130
        if not bought:
            return PAYMENT_FAILED

    return 0 if ranked else NOTHING_FOUND


if __name__ == "__main__":
    sys.exit(main())
