"""The sources a shopper trusts, and what "trusted" is allowed to mean (ADR-0027,
ADR-0021).
"""

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

#: What separates one source from the next when they arrive as one string, as they do
#: from the web form.
_SEPARATORS = re.compile(r"[,\s]+")

#: Path segments that route to a place rather than name one: YouTube writes a channel
#: ``/@mkbhd`` or ``/c/mkbhd``, Reddit ``/r/headphones`` -- in each the identifying
#: segment is the next.
_ROUTING = frozenset({"c", "user", "channel", "r", "u"})

#: Where a bare ``@handle`` lives -- how people name the one kind of source that is a
#: person rather than a site, and there is only one site it could mean.
_HANDLE_HOST = "youtube.com"

#: What has to follow that ``@``. Checked for the reason a host is: a spec naming its
#: site without naming anything *on* it searched YouTube for the literal "@" and
#: reported nothing found (ADR-0027).
_HANDLE = re.compile(r"@[a-z0-9][a-z0-9._-]*", re.IGNORECASE)

#: Stripped off a host before it is compared: ``www.rtings.com`` and ``rtings.com`` are
#: the same source, and pages link to both.
_WWW = "www."

#: The shapes that work, written once. Every refusal in this module ends in them: what
#: the shopper needs is a spec they can type.
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
            # An unbracketed IPv6 literal raises rather than naming a host, and a page
            # whose address cannot be read is nobody's.
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

    # Everything after the host is a path, and everything before it is a scheme or
    # credentials -- neither says which site this is.
    host, _, path = _SCHEME.sub("", spec).partition("/")
    host = host.split("@")[-1].split(":")[0].lower().removeprefix(_WWW)
    if not _HOSTNAME.fullmatch(host):
        raise _not_a_source(spec)

    return Source(spec=spec, domain=host, term=_term(path))


def _not_a_source(spec: str) -> ValueError:
    """The refusal both shapes carry, naming the ones that work."""
    return ValueError(f"{spec!r} does not name a source. {_SHAPES}")


def _not_a_source_at_all() -> ValueError:
    """The refusal for a spec naming nothing at all, which two callers reach.

    :func:`parse_source` meets it as a spec that is blank on its own, and
    :func:`parse_named_sources` as a ``--source ""`` that would otherwise come
    back as no sources and widen the search to the whole web (ADR-0027). One
    sentence, for the reason :func:`_not_a_source` is one: a shopper reading
    either needs the same shapes back.
    """
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
    ADR-0027).
    """
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
