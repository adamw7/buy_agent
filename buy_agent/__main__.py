"""Command line entry point:  python -m buy_agent "wireless headphones under $200"."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, get_args

from buy_agent import mandates, payment
from buy_agent.agent import BuyAgent, ModelUnavailableError, journal_for
from buy_agent.api import OPTIONS, results_payload
from buy_agent.bounds import notice
from buy_agent.chat import release
from buy_agent.config import (
    DEFAULT_BACKEND,
    DEFAULT_PROVIDER,
    DEFAULT_RAIL,
    LIMITS,
    AgentConfig,
    parse_currency,
    parse_region,
)
from buy_agent.journal import MAX_RUNS, RUNS
from buy_agent.logging_setup import configure_logging, log_changes
from buy_agent.models import RankedProduct
from buy_agent.money import CODES
from buy_agent.payment import PaymentError
from buy_agent.providers import PROVIDERS, provider_for
from buy_agent.rails import RAILS, rail_for
from buy_agent.ranking import SortBy
from buy_agent.search import BACKENDS, SearchError, backend_for
from buy_agent.sources import parse_named_sources, parse_sources

logger = logging.getLogger("buy_agent")

def _a_row_of(table: Mapping[str, Any], named: str) -> str:
    """``named`` where the table has it, and any row of it where it does not.

    Only so that the defaults below can be built at all: an environment that misspelt one
    of the three names is a usage error, said by ``_checked`` when the flag is read, and a
    config that refused to exist here would be a traceback before ``--help`` could print.
    """
    return named if named in table else next(iter(table))


def _defaults() -> AgentConfig:
    """Every flag's default, off one config so the two cannot drift apart."""
    return AgentConfig(
        provider=_a_row_of(PROVIDERS, DEFAULT_PROVIDER),
        rail=_a_row_of(RAILS, DEFAULT_RAIL),
        backend=_a_row_of(BACKENDS, DEFAULT_BACKEND),
    )


_DEFAULTS = _defaults()

#: Exit code for a run that worked and found nothing.
NOTHING_FOUND = 3

#: Exit code for a run that was asked to pay and did not.
PAYMENT_FAILED = 4


#: The paying settings, and how each tells a value somebody typed from one left alone.
_PAYING_FLAGS: tuple[tuple[str, str, Callable[[Any], bool]], ...] = (
    ("--rail", "rail", lambda value: value != DEFAULT_RAIL),
    ("--merchant-url", "merchant_url", bool),
    ("--spend-limit", "spend_limit", lambda value: value is not None),
)


def _offer_noticed_bounds(request: str, config: AgentConfig) -> None:
    """Say what the request asked for in words and nothing enforces (ADR-0059).

    Offered and never applied: a request saying "200 hours of battery" would otherwise
    drop every product in the run and report only that nothing was found. A line of
    narration is what that mistake costs here, and the shopper still has to type the
    flag.
    """
    for seen in notice(request):
        if getattr(config, seen.bound) is not None:
            # Already set, and by the one thing that sets it. Saying it again would read
            # as the run having taken the words for the number.
            continue
        logger.info(
            'Your request says "%s", which shapes the search and nothing else. '
            "%s %s is what would enforce it.",
            seen.phrase,
            # The flag enforcing each bound is named here and nowhere below: the browser
            # shows the same reading and has no command line to type it into.
            f"--{seen.bound.replace('_', '-')}",
            seen.figure,
        )


def _idle_paying_flags(args: argparse.Namespace) -> list[str]:
    """Which paying flags this command line carried that nothing in it will read."""
    return [flag for flag, field, given in _PAYING_FLAGS if given(getattr(args, field))]


def _provider_defaults(setting: str) -> str:
    """One column of :data:`buy_agent.providers.PROVIDERS`, as ``--help`` prints it."""
    return ", ".join(
        f"{getattr(server, setting)} for {name}" for name, server in PROVIDERS.items()
    )


def _bounded(kind: Callable[[str], Any], field: str) -> Callable[[str], Any]:
    """``--results`` and the rest, held to the range the API holds them to."""
    minimum, maximum = LIMITS[field]

    def parse(text: str) -> Any:
        value = kind(text)  # argparse turns the ValueError into "invalid value"
        if not minimum <= value <= maximum:
            raise argparse.ArgumentTypeError(
                f"must be between {minimum} and {maximum}; got {value}"
            )
        return value

    # argparse names the type in its own message for anything this does not catch, and
    # "invalid int value" is what a mistyped number deserves to read.
    parse.__name__ = kind.__name__
    return parse


def _checked(check: Callable[[str], object]) -> Callable[[str], str]:
    """A flag's value as argparse takes it: refused here, and kept as written (ADR-0027,
    ADR-0031)."""

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
            # Wrapped by hand, and held to it by a convention test: the formatter
            # below prints this block exactly as written, so a sentence left as one
            # long line is the only part of --help an 80-column terminal breaks
            # mid-word -- and the exit codes underneath are what the raw formatter
            # is here for.
            "The report is written to stdout and the progress to stderr, so\n"
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
    # Both default to "" rather than a value: which one is right depends on --provider,
    # which argparse has not read yet.
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
        # Read off the type: rank_products has a branch per criterion, and a fourth must
        # not be offered here without one there.
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
        "--backend",
        type=_checked(backend_for),
        choices=tuple(BACKENDS),
        default=DEFAULT_BACKEND,
        help=f"Which search backend to ask (default: {DEFAULT_BACKEND}, override with "
        "$BUY_AGENT_BACKEND). The default needs no key and no account and rate-limits "
        "heavy use; the others are an instance you run ($SEARXNG_HOST) and a key you "
        "hold ($BRAVE_API_KEY), each read off the environment and neither a flag.",
    )
    parser.add_argument(
        "--currency",
        type=_checked(parse_currency),
        default=_DEFAULTS.currency,
        metavar="CODE",
        # The codes spelled out, off ``money``'s own table rather than listed again:
        # this is the one flag whose value comes from a closed set and cannot carry
        # ``choices``, since a spelling is folded on the way in ($, usd and USD are one
        # answer) and argparse would refuse the two that are not the code. Left to the
        # refusal, the only way to read the set was to get it wrong first -- while the
        # form has had a picker over the same table all along.
        help="Count this run's prices in this currency, empty for whatever the pages "
        f"quote (the default). One of: {', '.join(sorted(CODES))} -- or any spelling "
        "a page uses for one of them ($, usd, euros). Nothing is converted, so a price "
        "in any other currency is one this run cannot place: it scores neutral, sinks "
        "in a price sort and passes every limit. Naming one your pages never quote is "
        "the way to ask for a report whose price criterion is entirely assumed, and "
        "the run says so.",
    )
    parser.add_argument(
        "--source",
        action="append",
        metavar="SITE",
        # ``parse_named_sources`` rather than ``parse_sources``: on a command line
        # "unset" is spelled by leaving the flag off, so ``--source ""`` is a mistake.
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
        "--journal",
        action=argparse.BooleanOptionalAction,
        default=_DEFAULTS.journal,
        help="Write down what this run reported, so the next run of the same search "
        f"can say what moved (default: {'--journal' if _DEFAULTS.journal else '--no-journal'}). "
        "It keeps a name, a price and a currency per product and nothing else, at "
        f"most {MAX_RUNS} runs per search, in {RUNS}/ beside the cached pages under "
        "$BUY_AGENT_CACHE_DIR -- deleting that directory throws the whole history "
        "away. Unlike the cache it does not expire: a record that did is no use for "
        "the one question it answers.",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Report what changed since the last run of this same search: what is "
        "cheaper, what is dearer, what is new and what has gone. Reads what --journal "
        "wrote, and a search whose settings differ -- another region, another budget "
        "-- is a different question and has a history of its own.",
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
        # "Payment endpoint" is the name the form gives this box, and the name the
        # refusals below the doors use, so a shopper reading one of those here has a
        # word to look up.
        help="Payment endpoint: the AP2-speaking address a paying rail talks to, "
        "empty for the rail's own default ($BUY_AGENT_MERCHANT_URL). "
        "It is asked for a signed "
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
        # None, not the value: the help below names the default either way, and only a
        # number actually typed is worth a warning.
        default=None,
        help=f"Context window in tokens (default: {_DEFAULTS.num_ctx}). The "
        "extraction prompt runs to ~4.3k tokens, so a larger window leaves room for "
        "more products; a model that need not think is fine on Ollama's own 4096. "
        "Ollama only -- vLLM fixes its window with --max-model-len when it starts.",
    )
    parser.add_argument(
        "--model-timeout",
        type=_bounded(float, "model_timeout"),
        default=_DEFAULTS.model_timeout,
        metavar="SECONDS",
        help=f"How long to wait for one answer from the model server (default: "
        f"{_DEFAULTS.model_timeout:g}). Asked once and not retried, so this is the "
        "whole wait: a server that took the prompt and went quiet ends the run with "
        "something to act on rather than hanging it. A slow model on a long prompt "
        "is what the wait is for -- try a smaller model or a smaller --num-ctx "
        "before a bigger number here.",
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
        "--cpu-only",
        action=argparse.BooleanOptionalAction,
        default=_DEFAULTS.cpu_only,
        help="Keep the model off the GPU entirely (default: --no-cpu-only, which "
        "leaves the offload to the model server). Slower, but it leaves the card "
        "free and runs a model too large to fit on it. Ollama only -- vLLM picks "
        "its device when it starts.",
    )
    parser.add_argument(
        "--no-fetch",
        dest="fetch",
        action="store_false",
        # Off the config like every other flag's default, rather than argparse's own
        # implicit ``True``: the two agree today, and this is what keeps them agreeing.
        default=_DEFAULTS.fetch_pages,
        help="Extract from search snippets only, without opening the result pages "
        "(default: they are read). Much faster without them, but snippets rarely "
        "quote a price.",
    )
    parser.add_argument(
        "--json",
        type=Path,
        # A path and not a format: left to argparse the flag read "--json JSON", the one
        # metavar here that says the value again instead of saying what it is -- and
        # reads like a switch asking for JSON on stdout, where the report already goes.
        metavar="FILE",
        help="Also write all results, not only the top ones, to this JSON file.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    return parser


def _approved(cart: payment.Cart, config: AgentConfig) -> bool:
    """Ask the shopper to approve this exact cart, and mean it (ADR-0046)."""
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
    """Buy the top-ranked product, and say what came of it (ADR-0009, ADR-0046)."""
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


# ``parser.error`` exits rather than returning, so the ``except`` below ends the process
# and pylint reads it as a branch that falls off the end with no value.
# pylint: disable-next=inconsistent-return-statements
def _configured(parser: argparse.ArgumentParser, **settings: Any) -> AgentConfig:
    """The run's config, with the one thing it refuses said the way a flag is."""
    try:
        return AgentConfig(**settings)
    except ValueError as exc:
        parser.error(str(exc))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose)

    # Every flag lands under the request key its setting has, so which field it fills
    # in is read off ``api.OPTIONS`` rather than written out a second time here: a
    # setting listed once is one both doors carry or neither does.
    settings = {option.field: getattr(args, option.key) for option in OPTIONS}
    # The three the table cannot answer for. An untyped ``--num-ctx`` is not a value to
    # pass on; repeated flags build a list, and no flag at all leaves ``None``, where the
    # fallback is the config's own default rather than an empty one written down again;
    # and searching for fewer pages than the report intends to show would cap it.
    settings["num_ctx"] = _DEFAULTS.num_ctx if args.num_ctx is None else args.num_ctx
    settings["sources"] = parse_sources(args.source) if args.source else _DEFAULTS.sources
    settings["search_results"] = max(args.results, args.top)

    config = _configured(parser, **settings)

    if not config.pay and (idle := _idle_paying_flags(args)):
        # Said rather than dropped, for the reason the context window below is: a run
        # that spends its minute and then buys nothing reads as a rail that failed.
        logger.warning(
            "Nothing will be bought: %s %s nothing without --pay.",
            ", ".join(idle),
            "does" if len(idle) == 1 else "do",
        )

    if args.num_ctx is not None and not config.model_server.takes_num_ctx:
        # The form disables the field; the CLI has none to disable, so it says so here
        # rather than dropping the number without a word.
        logger.warning(
            "%s fixes its context window when it starts (--max-model-len), so "
            "--num-ctx %s is ignored on this run.",
            config.model_server.label,
            args.num_ctx,
        )

    if args.cpu_only and not config.model_server.takes_cpu_only:
        # The same warning for the same reason: a switch the run cannot honour is worth
        # a line rather than a card quietly staying busy.
        logger.warning(
            "%s chooses its device when it starts (--device), so --cpu-only is "
            "ignored on this run.",
            config.model_server.label,
        )

    if args.compare and not config.journal:
        # Said rather than silently doing nothing, for the reason the idle paying flags
        # are: a run asked to compare and given nothing to compare against reads as a
        # search that has not moved.
        logger.warning(
            "--compare has nothing to read: --no-journal is what keeps a run from "
            "being written down."
        )

    _offer_noticed_bounds(args.request, config)

    # Opened before the run and asked after it, so what it answers with is the last run
    # of this search and not this one (ADR-0060).
    journal = journal_for(args.request, config)

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
        # The agent is this run and nothing after it, so its connection is let go of
        # here rather than whenever the process ends.
        release(agent)

    # After the report and under it: this is the second half of the answer. The run is
    # written down whether or not anybody asked to be told, or there would never be an
    # earlier one to compare the next with (ADR-0060).
    changes = journal.against([entry.product for entry in ranked])
    if args.compare:
        log_changes(changes, journal.compared_with())

    if args.json:
        # Written even when the run found nothing, and so before the exit code is
        # decided: skipped, a script waiting on this file finds the last run's results
        # looking current.
        payload = results_payload(ranked, config.currency or None)
        try:
            args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            # Worth an exit code and not a traceback: the report is already on stdout,
            # so what failed is the copy.
            logger.error("Could not write %s (%s)", args.json, exc)
            return 1
        logger.info("Wrote %d products to %s", len(payload), args.json)

    # After the report and the file: both are true whatever the payment does, and a
    # failed purchase must not cost the shopper the answer.
    if args.pay and ranked:
        try:
            bought = _bought(ranked, config)
        except KeyboardInterrupt:
            # Ctrl-C at the approval prompt is somebody deciding not to buy, and the
            # prompt is where a shopper hesitates -- so it is answered as a Ctrl-C
            # anywhere else in this run is.
            logger.warning("Interrupted. Nothing was bought.")
            return 130
        return 0 if bought else PAYMENT_FAILED

    return 0 if ranked else NOTHING_FOUND


if __name__ == "__main__":
    sys.exit(main())
