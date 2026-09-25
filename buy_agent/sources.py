"""The sources a shopper trusts, and what "trusted" means (ADR-0027, ADR-0021)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Iterable

#: A hostname: dot-separated labels of letters, digits and hyphens.
_HOSTNAME = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")

#: A URL scheme, or the ``//`` of a scheme-relative one.
_SCHEME = re.compile(r"^(?:[a-z][a-z0-9+.-]*:)?//", re.IGNORECASE)

#: What separates sources given as one string (the web form).
_SEPARATORS = re.compile(r"[,\s]+")

#: Path segments that route rather than name (``/c/mkbhd``, ``/r/headphones``): the
#: identifying segment is the next one.
_ROUTING = frozenset({"c", "user", "channel", "r", "u"})

#: Where a bare ``@handle`` lives.
_HANDLE_HOST = "youtube.com"

#: What has to follow that ``@``.
_HANDLE = re.compile(r"@[a-z0-9][a-z0-9._-]*", re.IGNORECASE)

#: Stripped off a host before comparing: ``www.rtings.com`` is ``rtings.com``.
_WWW = "www."

#: The shapes that work, written once.
_SHAPES = (
    "Give a site (rtings.com), a section of one (rtings.com/headphones) or a "
    "YouTube handle (@mkbhd)."
)


@dataclass(frozen=True, slots=True)
class Source:
    """One place the shopper is willing to take facts from."""

    spec: str
    domain: str
    term: str = ""

    def site_query(self, query: str) -> str:
        """``query``, narrowed to this source."""
        narrowed = f"{query} site:{self.domain}"
        return f'{narrowed} "{self.term}"' if self.term else narrowed

    def covers(self, url: str) -> bool:
        """Whether ``url`` is a page on this source's domain, subdomains included."""
        try:
            host = urlsplit(url).hostname
        except ValueError:
            # E.g. an unbracketed IPv6 literal: an unreadable address is nobody's.
            return False
        if not host:
            return False
        host = host.removeprefix(_WWW)
        return host == self.domain or host.endswith(f".{self.domain}")


def parse_source(spec: str) -> Source:
    """Read one source out of what the shopper wrote."""
    spec = spec.strip()
    if not spec:
        raise _not_a_source_at_all()

    if spec.startswith("@"):
        handle = spec.split("/")[0]
        if not _HANDLE.fullmatch(handle):
            raise _not_a_source(spec)
        return Source(spec=spec, domain=_HANDLE_HOST, term=handle)

    # Drop the scheme, credentials, port and path: only the host names the site.
    host, _, path = _SCHEME.sub("", spec).partition("/")
    host = host.split("@")[-1].split(":")[0].lower().removeprefix(_WWW)
    if not _HOSTNAME.fullmatch(host):
        raise _not_a_source(spec)

    return Source(spec=spec, domain=host, term=_term(path))


def _not_a_source(spec: str) -> ValueError:
    """The refusal both shapes carry, naming the ones that work."""
    return ValueError(f"{spec!r} does not name a source. {_SHAPES}")


def _not_a_source_at_all() -> ValueError:
    """The refusal for a blank spec."""
    return ValueError(f"A source cannot be blank. {_SHAPES}")


def parse_sources(specs: str | Iterable[str]) -> tuple[Source, ...]:
    """Every source in ``specs``, in the order given and without repeats."""
    sources: dict[tuple[str, str], Source] = {}
    for entry in [specs] if isinstance(specs, str) else specs:
        for spec in _SEPARATORS.split(entry.strip()):
            if not spec:
                continue
            source = parse_source(spec)
            sources.setdefault((source.domain, source.term), source)
    return tuple(sources.values())


def parse_named_sources(specs: str | Iterable[str]) -> tuple[Source, ...]:
    """The sources in ``specs``, where naming none of them is the mistake (ADR-0012,
    ADR-0027)."""
    sources = parse_sources(specs)
    if not sources:
        raise _not_a_source_at_all()
    return sources


def format_sources(sources: Iterable[Source]) -> str:
    """Sources written back the way they were given, as one field's worth of text."""
    return " ".join(source.spec for source in sources)


def _term(path: str) -> str:
    """The part of a path worth searching for: a channel handle, or a section."""
    segments = [segment for segment in path.split("?")[0].split("#")[0].split("/") if segment]
    while segments and segments[0].lower() in _ROUTING:
        segments.pop(0)
    return segments[0] if segments else ""
