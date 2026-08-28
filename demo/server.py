"""Serve the real UI against one of the scripted runs in :mod:`demo`.

``python -m demo.server`` is ``python -m buy_agent.server`` with two names
replaced -- ``search_web`` and ``enrich`` -- and a fake chat model in place of
ChatOllama, through the ``agent_factory=`` seam ``create_server`` already has for
the tests. Everything else on the page is the shipped code path.

``--script`` picks which fabricated web that run searches. A script is a module
offering the five names the run needs -- ``REQUEST``, ``REFINED_QUERY``,
``PAGES``, ``PAGE_TEXT`` and ``EXTRACTED`` -- so a third demo is a module beside
:mod:`demo.books` and a row in :data:`SCRIPTS`.

The waits are the point of the pacing flags. A real run spends most of a minute
inside two model calls that log nothing, which is dead air in a recording, so
``--pace`` scales the stand-in delays: 1.0 keeps them long enough that the
progress log fills in rather than appearing all at once, and 0 removes them.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableLambda

from buy_agent import agent as agent_module
from buy_agent.agent import BuyAgent
from buy_agent.config import AgentConfig
from buy_agent.fetch import condense
from buy_agent.logging_setup import configure_logging
from buy_agent.models import SearchQuery
from buy_agent import server as server_module
from buy_agent.server import DEFAULT_UI_DIR, create_server

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import ModuleType

    from buy_agent.search import SearchResult

logger = logging.getLogger(__name__)

#: The two lines the fetch layer writes, logged under its own name: the SSE
#: relay is attached to the ``buy_agent`` logger, so a line written under
#: ``demo.server`` reaches the console and never reaches the browser.
fetch_logger = logging.getLogger("buy_agent.fetch")

#: Seconds each stand-in step spends, before ``--pace`` scales it. Roughly the
#: shape of a real run -- refining is one short answer, extraction is ten pages
#: of JSON -- with two orders of magnitude taken off the clock.
REFINE_SECONDS = 1.1
FETCH_SECONDS = 1.6
EXTRACT_SECONDS = 2.6


class ScriptedLLM:
    """Stands in for ChatOllama, answering from the script after a pause.

    ``with_structured_output`` is the entire surface the two chains use, and the
    schema it is asked for is what says which of the two is calling.
    """

    def __init__(self, script: ModuleType, pace: float = 1.0) -> None:
        self.script = script
        self.pace = pace

    def with_structured_output(self, schema: type, **_: Any) -> RunnableLambda:
        def respond(_value: Any) -> Any:
            refining = schema is SearchQuery
            time.sleep((REFINE_SECONDS if refining else EXTRACT_SECONDS) * self.pace)
            if refining:
                return SearchQuery(query=self.script.REFINED_QUERY)
            return self.script.EXTRACTED

        return RunnableLambda(respond)


def install_fake_web(script: ModuleType, pace: float = 1.0) -> None:
    """Point the agent at the script's own pages instead of at the web.

    The fake stops at the transport, as ``integration/conftest.py``'s does: the
    text comes from the fixture rather than from a URL, and then goes through the
    real :func:`buy_agent.fetch.condense` on the config's own budgets. Grounding
    runs over that condensed text, so what the pipeline checks against here is
    the same kind of corpus it checks against in production.
    """

    def search(query: str, *, max_results: int = 10, region: str = "us-en") -> list:
        return [result.model_copy() for result in script.PAGES[:max_results]]

    def enrich(
        results: Sequence[SearchResult],
        *,
        max_chars: int = 1200,
        opinion_chars: int = 400,
        **_: object,
    ) -> list:
        fetch_logger.info("Fetching %d result page(s)", len(results))
        time.sleep(FETCH_SECONDS * pace)
        enriched = [
            result.model_copy(
                update={
                    "content": condense(
                        script.PAGE_TEXT[result.url],
                        max_chars=max_chars,
                        opinion_chars=opinion_chars,
                    )
                }
            )
            for result in results
        ]
        with_content = sum(1 for result in enriched if result.content)
        fetch_logger.info(
            "Got usable page text from %d of %d result(s)", with_content, len(enriched)
        )
        return enriched

    agent_module.search_web, agent_module.enrich = search, enrich


#: What ``GET /api/models`` answers with. Ollama is not running here, and the
#: header pill saying so would be the first thing a viewer of the recording
#: read -- so the model picker is scripted along with the rest of the run.
DEMO_MODELS = ("gemma4:12b", "qwen3:8b", "llama4:8b", "lfm2.5")


def install_fake_models() -> None:
    """Answer the UI's model picker without asking a model server."""

    def models(provider: str, base_url: str) -> dict[str, Any]:
        return {
            "provider": provider,
            "label": "Ollama",
            "base_url": base_url,
            "reachable": True,
            "models": list(DEMO_MODELS),
        }

    server_module.installed_models = models


#: The scripts ``--script`` offers, and the modules they name.
SCRIPTS = {"books": "demo.books", "laptops": "demo.laptops"}


def load_script(name: str) -> ModuleType:
    """Import the fabricated web ``name`` stands for."""
    return importlib.import_module(SCRIPTS[name])


def demo_agent(config: AgentConfig, script: ModuleType, *, pace: float = 1.0) -> BuyAgent:
    """Build the agent the demo server runs: the real one, on a scripted model."""
    return BuyAgent(config, llm=ScriptedLLM(script, pace))  # type: ignore[arg-type]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="demo.server",
        description="Serve the buy_agent UI against a scripted run, for recording.",
    )
    parser.add_argument(
        "--script",
        choices=sorted(SCRIPTS),
        default="books",
        help="Which fabricated web to search (default: books).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Interface to bind.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind.")
    parser.add_argument(
        "--ui-dir",
        type=Path,
        default=DEFAULT_UI_DIR,
        help="Directory holding the built Angular app (default: ui/dist/ui/browser).",
    )
    parser.add_argument(
        "--pace",
        type=float,
        default=1.0,
        help="Scale the stand-in delays. 0 removes them entirely.",
    )
    parser.add_argument("--open", action="store_true", help="Open a browser at the UI.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()

    if not (args.ui_dir / "index.html").is_file():
        logger.error("No built UI at %s. Build it with: cd ui && npm run build", args.ui_dir)
        return 1

    script = load_script(args.script)
    install_fake_web(script, args.pace)
    install_fake_models()
    server = create_server(
        args.host,
        args.port,
        ui_dir=args.ui_dir,
        agent_factory=lambda config: demo_agent(config, script, pace=args.pace),
    )
    url = f"http://{args.host}:{server.server_address[1]}"
    logger.info(
        "buy_agent demo UI on %s -- the %s script, no Ollama and no web",
        url,
        args.script,
    )
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever(0.05)
    except KeyboardInterrupt:
        logger.warning("Interrupted.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
