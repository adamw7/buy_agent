"""What a kept run was scored under besides its case and its model: the code between the
pages and the scorecard, and the settings that reach the model. A run made under other
code is kept, and marked as no comparison (ADR-0075)."""

from __future__ import annotations

import ast
import functools
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from buy_agent import (
    agent,
    chat,
    constraints,
    extraction,
    fetch,
    models,
    money,
    providers,
    ranking,
    verification,
)
from benchmark import query, runner, scoring

if TYPE_CHECKING:
    from types import ModuleType

    from buy_agent.config import AgentConfig

#: The modules between the pages and the scorecard; the cases have fingerprints of
#: their own.
MODULES: tuple[ModuleType, ...] = (
    agent,
    chat,
    constraints,
    extraction,
    fetch,
    models,
    money,
    providers,
    ranking,
    verification,
    runner,
    scoring,
    query,
)

_DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _without_docstrings(source: str) -> str:
    """``source`` as its syntax tree without docstrings: what it does, not how it reads."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, _DOCUMENTED)
            and node.body
            and isinstance(first := node.body[0], ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.dump(tree, annotate_fields=False, include_attributes=False)


@functools.cache
def code(modules: tuple[ModuleType, ...] = MODULES) -> str:
    """A fingerprint of the code a run goes through; a reworded docstring does not move it."""
    digest = hashlib.sha256()
    for module in modules:
        digest.update(module.__name__.encode("utf-8"))
        source = Path(str(module.__file__)).read_text(encoding="utf-8")
        digest.update(_without_docstrings(source).encode("utf-8"))
    return digest.hexdigest()[:16]


def settings(config: AgentConfig) -> dict[str, Any]:
    """What reaches the model, besides the case and the contender, named as the doors
    name it (``think``, not ``reasoning``)."""
    shown: dict[str, Any] = {"temperature": config.temperature, "think": config.reasoning}
    if config.model_server.takes_num_ctx:
        shown["num_ctx"] = config.num_ctx
    shown["page_chars"] = config.page_chars
    shown["opinion_chars"] = config.opinion_chars
    return shown


def setting_label(name: str, value: Any) -> str:
    """As "think off", "num_ctx 16384"."""
    if value is None:
        return f"{name} unset"
    if isinstance(value, bool):
        return f"{name} {'on' if value else 'off'}"
    return f"{name} {value:g}" if isinstance(value, float) else f"{name} {value}"


def settings_label(shown: dict[str, Any]) -> str:
    return ", ".join(setting_label(name, value) for name, value in shown.items())


__all__ = ["MODULES", "code", "setting_label", "settings", "settings_label"]
