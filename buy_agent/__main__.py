"""Command line entry point:  python -m buy_agent "wireless headphones under $200"."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import textwrap
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, get_args

from buy_agent import mandates, payment
from buy_agent.agent import BuyAgent, ModelUnavailableError, journal_for
from buy_agent.api import OPTIONS, number_kind, results_payload, takeable
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
from buy_agent.ranking import ORDERINGS, SortBy
from buy_agent.search import BACKENDS, SearchError, backend_for
from buy_agent.sources import parse_named_sources, parse_sources

logger = logging.getLogger("buy_agent")

def _a_row_of(table: Mapping[str, Any], named: str) -> str:
    """``named`` if the table has it, else any row: a misspelt env var is reported by
    ``_checked``, not as a traceback before ``--help``."""
    return named if named in table else next(iter(table))


def _defaults() -> AgentConfig:
    """Every flag's default, off one config."""
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


#: The paying flags, and how each tells a typed value from the default.
_PAYING_FLAGS: tuple[tuple[str, str, Callable[[Any], bool]], ...] = (
    ("--rail", "rail", lambda value: value != DEFAULT_RAIL),
    ("--merchant-url", "merchant_url", bool),
    ("--spend-limit", "spend_limit", lambda value: value is not None),
)


def _offer_noticed_bounds(request: str, config: AgentConfig) -> None:
    """Log the bounds the request states in words, and the flag enforcing each; never
    apply them (ADR-0059)."""
    for seen in notice(request):
        # Skip a figure the flag would refuse, or a bound already set.
        if not takeable(seen) or getattr(config, seen.bound) is not None:
            continue
        logger.info(
            'Your request says "%s", which shapes the search and nothing else. '
            "%s %s is what would enforce it.",
            seen.phrase,
            # Flags are named only at this door.
            f"--{seen.bound.replace('_', '-')}",
            seen.figure,
        )


def _idle_paying_flags(args: argparse.Namespace) -> list[str]:
    """The paying flags given without ``--pay``."""
    return [flag for flag, field, given in _PAYING_FLAGS if given(getattr(args, field))]


def _provider_defaults(setting: str) -> str:
    """One column of :data:`buy_agent.providers.PROVIDERS`, for ``--help``."""
    return ", ".join(
        f"{getattr(server, setting)} for {name}" for name, server in PROVIDERS.items()
    )


def _bounded(kind: Callable[[str], Any], field: str) -> Callable[[str], Any]:
    """A number type held to its ``LIMITS`` range, and refused in the words the API
    refuses it with."""
    minimum, maximum = LIMITS[field]

    def parse(text: str) -> Any:
        try:
            value = kind(text)
        except ValueError as exc:
            # argparse's own names the converter: "invalid float value".
            raise argparse.ArgumentTypeError(
                f"must be {number_kind(kind)}; got {text!r}"
            ) from exc
        if not minimum <= value <= maximum:
            raise argparse.ArgumentTypeError(
                f"must be between {minimum} and {maximum}; got {value}"
            )
        return value

    return parse


def _json_file(text: str) -> Path:
    """A ``--json`` file whose directory is there, checked before the run: written at the
    end, a typo in the path otherwise costs the whole run first."""
    path = Path(text)
    if path.is_dir():
        raise argparse.ArgumentTypeError(f"{str(path)!r} is a directory; name a file in it")
    if not path.parent.is_dir():
        raise argparse.ArgumentTypeError(
            f"there is no directory {str(path.parent)!r} to write {path.name} into"
        )
    return path


def _checked(check: Callable[[str], object]) -> Callable[[str], str]:
    """A flag type that validates with ``check`` but keeps the text (ADR-0027, ADR-0031)."""

    def parse(text: str) -> str:
        try:
            check(text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc
        return text

    return parse


class _Help(argparse.RawDescriptionHelpFormatter):
    """Wraps a flag's help between words and never inside one: textwrap breaks at a
    hyphen, which left ``--no-`` ending one line and ``cpu-only`` starting the next --
    a flag, or a value like ``dry-run``, that nobody can copy -- at whatever width the
    terminal happened to be."""

    def _split_lines(self, text: str, width: int) -> list[str]:
        return textwrap.wrap(" ".join(text.split()), width, break_on_hyphens=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buy_agent",
        description="Search the web for what you want to buy, rank it, log the best.",
        # --help is the CLI's only documentation.
        epilog=(
            # Wrapped by hand (a convention test checks): the raw formatter prints it
            # as written.
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
        formatter_class=_Help,
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
    # "" because the right default depends on --provider (ADR-0012).
    parser.add_argument(
        "--model",
        default="",
        help="Model to use, empty for the provider's own default "
        f"({_provider_defaults('model')}). Override with $OLLAMA_MODEL, $VLLM_MODEL or "
        "$LITELLM_MODEL.",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="Model server URL, empty for the provider's own default "
        f"({_provider_defaults('base_url')}). Override with $OLLAMA_HOST, $VLLM_HOST or "
        "$LITELLM_HOST.",
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
        # Read off the type, so it matches rank_products.
        choices=get_args(SortBy),
        default="score",
        # Each with its direction, off the table the report's heading reads.
        help="How to order the report: "
        + ", ".join(f"{name} for {phrase}" for name, phrase in ORDERINGS.items())
        + " (default: score, a blend of rating, reviews and price).",
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
        # Spellings fold, so ``choices`` cannot be used; the help lists the codes.
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
        # On a command line, ``--source ""`` is a mistake, not "unset".
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
        # "Payment endpoint" is the setting's name everywhere else.
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
        # None, so only a typed number earns the warning in ``main``.
        default=None,
        help=f"Context window in tokens (default: {_DEFAULTS.num_ctx}). The "
        "extraction prompt runs to ~4.3k tokens, so a larger window leaves room for "
        "more products; a model that need not think is fine on Ollama's own 4096. "
        "Ollama only -- vLLM fixes its window with --max-model-len when it starts, "
        "and a LiteLLM proxy leaves it to the server it routes to.",
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
        "its device when it starts, and a LiteLLM proxy leaves it to the server it "
        "routes to.",
    )
    parser.add_argument(
        "--no-fetch",
        dest="fetch",
        action="store_false",
        # Off the config, like every other default.
        default=_DEFAULTS.fetch_pages,
        help="Extract from search snippets only, without opening the result pages "
        "(default: they are read). Much faster without them, but snippets rarely "
        "quote a price.",
    )
    parser.add_argument(
        "--json",
        type=_json_file,
        # "--json JSON" would read like a format switch.
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


# ``parser.error`` exits, which pylint reads as falling off the end.
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

    # Flags land under their ``api.OPTIONS`` keys, so both doors share one table.
    settings = {option.field: getattr(args, option.key) for option in OPTIONS}
    # What the table cannot say: the ``--num-ctx`` sentinel, the list of sources, and
    # never searching fewer pages than the report shows.
    settings["num_ctx"] = _DEFAULTS.num_ctx if args.num_ctx is None else args.num_ctx
    settings["sources"] = parse_sources(args.source) if args.source else _DEFAULTS.sources
    settings["search_results"] = max(args.results, args.top)

    config = _configured(parser, **settings)

    if not config.pay and (idle := _idle_paying_flags(args)):
        # Otherwise buying nothing would read as a failed rail.
        logger.warning(
            "Nothing will be bought: %s %s nothing without --pay.",
            ", ".join(idle),
            "does" if len(idle) == 1 else "do",
        )

    if args.num_ctx is not None and not config.model_server.takes_num_ctx:
        # The form disables this field; the CLI says so instead.
        logger.warning(
            "%s fixes its context window when it starts (--max-model-len), so "
            "--num-ctx %s is ignored on this run.",
            config.model_server.label,
            args.num_ctx,
        )

    if args.cpu_only and not config.model_server.takes_cpu_only:
        # Likewise.
        logger.warning(
            "%s chooses its device when it starts (--device), so --cpu-only is "
            "ignored on this run.",
            config.model_server.label,
        )

    if args.compare and not config.journal:
        # Otherwise it would read as nothing having moved.
        logger.warning(
            "--compare has nothing to read: --no-journal is what keeps a run from "
            "being written down."
        )

    _offer_noticed_bounds(args.request, config)

    # Opened before the run, so it holds the previous one (ADR-0060).
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
        # Release the model connection now, not at exit.
        release(agent)

    # Always recorded, so the next run has something to compare with (ADR-0060).
    changes = journal.against([entry.product for entry in ranked])
    if args.compare:
        log_changes(changes, journal.compared_with())

    if args.json:
        # Written even when empty, so a stale file never looks current.
        payload = results_payload(ranked, config.currency or None)
        try:
            args.json.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            # An exit code, not a traceback: the report is already on stdout.
            logger.error("Could not write %s (%s)", args.json, exc)
            return 1
        logger.info("Wrote %d products to %s", len(payload), args.json)

    # Last, so a failed purchase never costs the report or the file.
    if args.pay and ranked:
        try:
            bought = _bought(ranked, config)
        except KeyboardInterrupt:
            # Ctrl-C at the approval prompt: answered as anywhere else.

            logger.warning("Interrupted. Nothing was bought.")
            return 130
        return 0 if bought else PAYMENT_FAILED

    return 0 if ranked else NOTHING_FOUND


if __name__ == "__main__":
    sys.exit(main())
