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
from buy_agent.agent import BuyAgent, ModelUnavailableError
from buy_agent.alerts import Alert
from buy_agent.api import OPTIONS, Option, results_payload, run_agent, takeable
from buy_agent.bounds import notice
from buy_agent.config import DEFAULT_BACKEND, DEFAULT_PROVIDER, DEFAULT_RAIL, AgentConfig
from buy_agent.journal import MAX_RUNS, RUNS
from buy_agent.logging_setup import configure_logging, log_alert, log_changes, wrap_help
from buy_agent.models import RankedProduct
from buy_agent.money import CODES
from buy_agent.payment import PaymentError
from buy_agent.providers import PROVIDERS
from buy_agent.rails import RAILS
from buy_agent.ranking import ORDERINGS, SortBy, scale_of
from buy_agent.search import BACKENDS, SearchError
from buy_agent.sources import parse_named_sources, parse_sources
from buy_agent.version import current

logger = logging.getLogger("buy_agent")


def _a_row_of(table: Mapping[str, Any], named: str) -> str:
    """``named`` if the table has it, else any row: a misspelt env var is then refused
    by the config, not as a traceback before ``--help``."""
    return named if named in table else next(iter(table))


def _defaults() -> AgentConfig:
    return AgentConfig(
        provider=_a_row_of(PROVIDERS, DEFAULT_PROVIDER),
        rail=_a_row_of(RAILS, DEFAULT_RAIL),
        backend=_a_row_of(BACKENDS, DEFAULT_BACKEND),
    )


_DEFAULTS = _defaults()

NOTHING_FOUND = 3
PAYMENT_FAILED = 4
#: ``--alert-below`` was given and nothing was at or under it (ADR-0080).
ABOVE_ALERT = 5

#: The paying flags, and how each tells a typed value from the default.
_PAYING_FLAGS: tuple[tuple[str, str, Callable[[Any], bool]], ...] = (
    ("--rail", "rail", lambda value: value != DEFAULT_RAIL),
    ("--merchant-url", "merchant_url", bool),
    ("--spend-limit", "spend_limit", lambda value: value is not None),
)


def _apply_noticed_bounds(request: str, settings: dict[str, Any]) -> None:
    """Set each bound the request states in words whose flag was not given, and log
    the words it read, the flag it stands for and how to change it (ADR-0094)."""
    for seen in notice(request):
        if not takeable(seen) or settings[seen.bound] is not None:
            continue
        settings[seen.bound] = int(seen.value) if seen.bound == "min_reviews" else seen.value
        # Flags are named only at this door.
        flag = f"--{seen.bound.replace('_', '-')}"
        logger.info(
            'Your request says "%s", so this run applies %s %s. Give %s yourself to '
            "change it, or leave it out of the request to search without it.",
            seen.phrase,
            flag,
            seen.figure,
            flag,
        )


def _idle_paying_flags(args: argparse.Namespace) -> list[str]:
    return [flag for flag, field, given in _PAYING_FLAGS if given(getattr(args, field))]


def _provider_defaults(setting: str) -> str:
    return ", ".join(f"{getattr(server, setting)} for {name}" for name, server in PROVIDERS.items())


def _json_file(text: str) -> Path:
    """A ``--json`` path checked before the run, so a typo does not cost a whole run."""
    path = Path(text)
    if path.is_dir():
        raise argparse.ArgumentTypeError(f"{str(path)!r} is a directory; name a file in it")
    if not path.parent.is_dir():
        raise argparse.ArgumentTypeError(
            f"there is no directory {str(path.parent)!r} to write {path.name} into"
        )
    return path


def _flag(parse: Callable[[str], Any]) -> Callable[[str], Any]:
    """A flag ``type`` whose refusal argparse prints as written, rather than "invalid
    float value" (ADR-0033)."""

    def typed(text: str) -> Any:
        try:
            return parse(text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    return typed


def _source(text: str) -> str:
    """One ``--source``, checked here and parsed with the rest in ``main``."""
    parse_named_sources(text)
    return text


def _flag_for(option: Option, written: dict[str, Any]) -> dict[str, Any]:
    """``add_argument``'s keywords for one ``api.OPTIONS`` row; ``written`` wins."""
    derived: dict[str, Any] = {"dest": option.key, "default": option.unset(_DEFAULTS)}
    if option.switch:
        derived["action"] = argparse.BooleanOptionalAction
    else:
        derived["type"] = _flag(option.parse)
    if option.choices is not None:
        derived["choices"] = option.choices
    return {**derived, **written}


class _Help(argparse.RawDescriptionHelpFormatter):
    """Wraps help with :func:`~buy_agent.logging_setup.wrap_help`."""

    def _split_lines(self, text: str, width: int) -> list[str]:
        return wrap_help(text, width)


def _written() -> dict[str, dict[str, Any]]:
    """Each ``api.OPTIONS`` row's help, and where its flag differs from the row. A line
    or two each: what it does, its default, and the one thing that would surprise; the
    reasons are in the README and the ADRs."""
    return {
        "provider": {
            # As written, so a misspelt variable is refused by name.
            "default": DEFAULT_PROVIDER,
            "help": f"Which model server to use (default: {DEFAULT_PROVIDER}, or "
            "$BUY_AGENT_PROVIDER). It decides what --model and --base-url mean.",
        },
        # "" because the right default depends on --provider (ADR-0012).
        "model": {
            "help": "Model to use, empty for the provider's default "
            f"({_provider_defaults('model')}), or $OLLAMA_MODEL, $VLLM_MODEL, "
            "$LITELLM_MODEL, $TRTLLM_MODEL.",
        },
        "base_url": {
            "help": "Model server address, empty for the provider's default "
            f"({_provider_defaults('base_url')}), or $OLLAMA_HOST, $VLLM_HOST, "
            "$LITELLM_HOST, $TRTLLM_HOST.",
        },
        "results": {
            "help": f"How many products to find (default: {_DEFAULTS.num_products}).",
        },
        "top": {"help": f"How many products to show (default: {_DEFAULTS.top_n})."},
        "region": {
            "help": "Search region, a country then a language (default: "
            f"{_DEFAULTS.region}; also uk-en, pl-pl).",
        },
        "backend": {
            "default": DEFAULT_BACKEND,
            "help": f"Search backend (default: {DEFAULT_BACKEND}, or $BUY_AGENT_BACKEND). "
            "ddg needs no account but limits heavy use; searxng needs $SEARXNG_HOST, "
            "brave needs $BRAVE_API_KEY.",
        },
        "currency": {
            "metavar": "CODE",
            # Spellings fold, so no ``choices``; the help lists the codes.
            "help": "Compare prices in this currency (default: the one most pages "
            f"use). One of {', '.join(sorted(CODES))}, or a spelling such as $ or "
            "euros. Nothing is converted: a price in another currency ranks as the "
            "dearest and passes every limit.",
        },
        "max_price": {
            "help": "Leave out anything dearer than this, in the run's currency "
            "(default: no limit). Products with no price are kept.",
        },
        "min_rating": {
            "help": "Leave out anything rated below this, out of 5 (default: no limit). "
            "Unrated products are kept.",
        },
        "min_reviews": {
            "help": "Leave out ratings averaged over fewer reviews than this (default: "
            "no limit). Ratings with no review count are kept.",
        },
        "alert_below": {
            "metavar": "PRICE",
            "help": "Say whether anything in stock is at or under this price, and exit "
            f"with {ABOVE_ALERT} if not (default: no alert). Unlike --max-price it "
            "removes nothing.",
        },
        "cache_ttl": {
            "metavar": "SECONDS",
            "help": "How long fetched pages and the model's answers are reused "
            f"(default: {_DEFAULTS.cache_ttl:g}, a day; 0 turns it off). Kept under "
            "$BUY_AGENT_CACHE_DIR.",
        },
        "journal": {
            "help": "Remember a name and price per product, so --compare can say what "
            "changed next time (default: "
            f"{'--journal' if _DEFAULTS.journal else '--no-journal'}; at most "
            f"{MAX_RUNS} runs per search, in {RUNS}/ under $BUY_AGENT_CACHE_DIR).",
        },
        "pay": {
            "help": "Buy the top product once you approve it (default: --no-pay). Only a "
            "price a page printed can be paid; $BUY_AGENT_AP2_MANDATE pre-approves "
            "runs that nobody watches.",
        },
        "rail": {
            "default": DEFAULT_RAIL,
            "help": f"Who to pay through (default: {DEFAULT_RAIL}, or $BUY_AGENT_RAIL). "
            "dry-run charges nobody.",
        },
        "merchant_url": {
            "help": "The payment endpoint a paying rail talks to, empty for the rail's "
            "default ($BUY_AGENT_MERCHANT_URL).",
        },
        "spend_limit": {
            "metavar": "AMOUNT",
            "help": "Refuse to pay more than this for one product, in the run's currency "
            "(default: no limit). A price it cannot read is refused.",
        },
        "temperature": {
            "help": f"Model temperature (default: {_DEFAULTS.temperature}). Above 0 the "
            "answers vary between runs and --cache-ttl keeps none of them.",
        },
        "num_ctx": {
            # None, so only a typed number earns the warning in ``main``.
            "default": None,
            "help": f"Context window in tokens (default: {_DEFAULTS.num_ctx}). Ollama "
            "only: vLLM and TensorRT-LLM set it when they start, and a LiteLLM proxy "
            "leaves it to the server it routes to.",
        },
        "model_timeout": {
            "metavar": "SECONDS",
            "help": "How long to wait for one answer from the model (default: "
            f"{_DEFAULTS.model_timeout:g}). Not retried.",
        },
        "think": {
            "help": "Turn the model's thinking mode on or off (default: --no-think, "
            "which thinking models need to answer at all).",
        },
        "cpu_only": {
            "help": "Keep the model off the GPU (default: --no-cpu-only). Ollama only: "
            "vLLM and TensorRT-LLM pick their device when they start, and a LiteLLM "
            "proxy leaves it to the server it routes to.",
        },
        "fetch": {
            "help": "Read each result page (default: --fetch). --no-fetch reads only the "
            "search snippets: faster, but they rarely give a price.",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buy_agent",
        description="Search the web for what you want to buy, rank it, log the best.",
        # Wrapped by hand: the raw formatter prints it as written.
        epilog=(
            "The report is written to stdout and the progress to stderr, so\n"
            "`... > top.txt` keeps the report and leaves the narration on screen.\n"
            "\n"
            "A limit written in the request (\"under $200\", \"at least 4 stars\") is\n"
            "applied as if its flag were given, unless the flag is given.\n"
            "\n"
            "Exit codes:\n"
            "  0  products were found and reported\n"
            "  1  the run failed -- the reason is the last line on stderr\n"
            "  2  the command line could not be understood\n"
            f"  {NOTHING_FOUND}  the run worked and found nothing\n"
            f"  {PAYMENT_FAILED}  --pay was asked for and nothing was bought\n"
            f"  {ABOVE_ALERT}  --alert-below was given and nothing was at or under it\n"
            "  130  interrupted with Ctrl-C\n"
        ),
        formatter_class=_Help,
    )
    add = parser.add_argument
    add("request", help="What you want to buy, in plain words.")
    for option in OPTIONS:
        add(f"--{option.key.replace('_', '-')}", **_flag_for(option, _written()[option.key]))
    add("--sort-by", choices=get_args(SortBy), default="score",
        help="Order: "
             + ", ".join(f"{name} for {phrase}" for name, phrase in ORDERINGS.items())
             + " (default: score, a blend of rating, reviews and price).")
    # ``--source ""`` is a mistake here, not "unset".
    add("--source", action="append", metavar="SITE", type=_flag(_source),
        help="Search only this site (rtings.com), section (rtings.com/headphones) or "
             "YouTube handle (@mkbhd); repeat for several.")
    add("--compare", action="store_true",
        help="Say what is cheaper, dearer, new or gone since the last run of this "
             "search with the same settings.")
    add("--json", type=_json_file, metavar="FILE",
        help="Also write all results, not only the top ones, to this JSON file.")
    add("-v", "--verbose", action="store_true", help="Debug logging.")
    add("--version", action="version", version=f"%(prog)s {current().label}")
    return parser


def _approved(cart: payment.Cart, config: AgentConfig) -> bool:
    """Ask the shopper to approve this exact cart (ADR-0046)."""
    if not sys.stdin.isatty():
        raise PaymentError(
            f"Paying {cart.label()} for {cart.title} needs your approval, and this "
            f"run has no terminal to ask at. Run it where you can answer, or "
            f"authorise it in advance with a pre-signed open mandate at "
            f"${mandates.MANDATE_PATH}."
        )
    rail = config.rail_used
    charge = "will be charged" if rail.moves_money else "will NOT be charged"
    listing = f"    listing   {cart.listing}\n" if cart.listing else ""
    sys.stderr.write(
        f"\n  Pay {cart.label()} for {cart.title}\n"
        f"    merchant  {cart.merchant}\n"
        f"    page      {cart.url}\n"
        f"{listing}"
        f"    rail      {rail.label} -- you {charge}\n"
        f"  Type yes to authorise: "
    )
    sys.stderr.flush()
    return sys.stdin.readline().strip().lower() in {"y", "yes"}


def _bought(ranked: list[RankedProduct], config: AgentConfig) -> bool:
    """Buy the top-ranked product, and say what came of it (ADR-0046)."""
    products = [entry.product for entry in ranked]
    try:
        # In the run's own currency, not a second vote (ADR-0056).
        cart = payment.cart_for(products[0], products, config, scale=scale_of(ranked))
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
        receipt.reference, receipt.title, receipt.price_label, receipt.merchant,
    )
    return True


# ``parser.error`` exits, which pylint reads as falling off the end.
# pylint: disable-next=inconsistent-return-statements
def _configured(parser: argparse.ArgumentParser, **settings: Any) -> AgentConfig:
    try:
        return AgentConfig(**settings)
    except ValueError as exc:
        parser.error(str(exc))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose)

    settings = {option.field: getattr(args, option.key) for option in OPTIONS}
    # What the table cannot say: the ``--num-ctx`` sentinel and the sources.
    settings["num_ctx"] = _DEFAULTS.num_ctx if args.num_ctx is None else args.num_ctx
    settings["sources"] = parse_sources(args.source) if args.source else _DEFAULTS.sources
    _apply_noticed_bounds(args.request, settings)

    config = _configured(parser, **settings)
    if not config.pay and (idle := _idle_paying_flags(args)):
        # Otherwise buying nothing would read as a failed rail.
        logger.warning(
            "Nothing will be bought: %s %s nothing without --pay.",
            ", ".join(idle),
            "does" if len(idle) == 1 else "do",
        )

    if args.num_ctx is not None and not config.model_server.takes_num_ctx:
        # The form disables this field; the CLI says so, naming no server's own flag:
        # vLLM's is not TensorRT-LLM's, and a LiteLLM proxy has none.
        logger.warning(
            "With %s the context window is chosen where the model is served, so "
            "--num-ctx %s is ignored on this run.",
            config.model_server.label,
            args.num_ctx,
        )

    if args.cpu_only and not config.model_server.takes_cpu_only:
        logger.warning(
            "With %s the device is chosen where the model is served, so --cpu-only is "
            "ignored on this run.",
            config.model_server.label,
        )

    if args.compare and not config.journal:
        # Otherwise it would read as nothing having moved.
        logger.warning(
            "--compare has nothing to read: --no-journal is what keeps a run from "
            "being written down."
        )

    try:
        finished = run_agent(args.request, config, sort_by=args.sort_by, agent_factory=BuyAgent)
    except (ModelUnavailableError, SearchError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logger.warning("Interrupted.")
        return 130

    ranked, alert = finished.ranked, finished.alert
    if args.compare:
        log_changes(finished.changes, finished.compared_with)
    if alert is not None:
        log_alert(alert)

    if args.json:
        # Written even when empty, so a stale file never looks current.
        payload = results_payload(ranked, config.currency or None)
        try:
            args.json.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            logger.error("Could not write %s (%s)", args.json, exc)
            return 1
        logger.info("Wrote %d products to %s", len(payload), args.json)

    # Last, so a failed purchase never costs the report or the file.
    return _settled(ranked, config, pay=args.pay, alert=alert)


def _settled(
    ranked: list[RankedProduct], config: AgentConfig, *, pay: bool, alert: Alert | None
) -> int:
    """The exit code of a run that worked: what paying came to, else whether anything
    was found, else whether the price alert was met (ADR-0080)."""
    if pay and ranked:
        try:
            bought = _bought(ranked, config)
        except KeyboardInterrupt:
            logger.warning("Interrupted. Nothing was bought.")
            return 130
        return 0 if bought else PAYMENT_FAILED

    if not ranked:
        return NOTHING_FOUND
    return ABOVE_ALERT if alert is not None and not alert.met else 0


if __name__ == "__main__":
    sys.exit(main())
