"""The sources a shopper trusts, and what "trusted" is allowed to mean.

Left to itself the agent searches the whole web and reports whatever the first
ten results printed. A shopper who knows where the good information is can name
those sites instead, and then every price, rating and quote comes from one of
them: the pages are what grounding checks against, so narrowing the pages
narrows the facts (ADR-0027).

A source is written the way people say it -- a site (``rtings.com``), a section
of one (``rtings.com/headphones``), a pasted URL, or a YouTube handle
(``@mkbhd``) -- and is read down to two parts. The **domain** is enforced: a
result from anywhere else is discarded before the model sees it. The **term** (a
handle, a section) narrows the *search* only, because a URL cannot carry it -- a
video's address says which video it is and nothing about who published it, so
enforcing a handle would throw away every video the channel ever posted and keep
the channel page alone.

Nothing here does any I/O: this module decides what a source *is* and
:class:`~buy_agent.agent.BuyAgent` does the searching, which keeps it testable
without a network and keeps :mod:`buy_agent.search` a DuckDuckGo wrapper and
nothing else (ADR-0021).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Iterable

#: A hostname: dot-separated labels of letters, digits and hyphens. At least one
#: dot, which is what tells a site from a word -- "rtings.com" is a source and
#: "Marques Brownlee" is a person, and only one of the two can be searched.
_HOSTNAME = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")

#: A URL scheme, or the ``//`` of a scheme-relative one. Taken off the front
#: rather than split on wherever it occurs: ``//`` turns up inside a path too,
#: and a source pasted out of an address bar must not lose its host to one.
_SCHEME = re.compile(r"^(?:[a-z][a-z0-9+.-]*:)?//", re.IGNORECASE)

#: What separates one source from the next when they arrive as one string, as
#: they do from the web form. No spec contains either, so both are safe.
_SEPARATORS = re.compile(r"[,\s]+")

#: Path segments that route to a place rather than name one: YouTube writes a
#: channel ``/@mkbhd``, ``/c/mkbhd`` or ``/channel/UC...`` and Reddit writes
#: ``/r/headphones``, and in every one the segment that identifies anything is the
#: next. Left in, ``reddit.com/r/headphones`` narrowed the search on the phrase
#: "r" and dropped the only word the shopper typed.
_ROUTING = frozenset({"c", "user", "channel", "r", "u"})

#: Where a bare ``@handle`` lives -- how people name the one kind of source that
#: is a person rather than a site, and there is only one site it could mean.
_HANDLE_HOST = "youtube.com"

#: Stripped off a host before it is compared: ``www.rtings.com`` and
#: ``rtings.com`` are the same source, and pages link to both.
_WWW = "www."


@dataclass(frozen=True, slots=True)
class Source:
    """One place the shopper is willing to take facts from.

    Attributes:
        spec: What the shopper typed, tidied -- what the logs call this source.
        domain: The host the results have to come from, without any ``www.``.
        term: A channel handle or a section, added to the query to aim the
            search inside the domain. Empty for a source that is a whole site.
    """

    spec: str
    domain: str
    term: str = ""

    def site_query(self, query: str) -> str:
        """``query``, narrowed to this source.

        ``site:`` is what a search engine offers for "only this domain"; the
        term goes in as a quoted phrase beside it, since a channel is not a
        domain and there is no operator for one.
        """
        narrowed = f"{query} site:{self.domain}"
        return f'{narrowed} "{self.term}"' if self.term else narrowed

    def covers(self, url: str) -> bool:
        """Whether ``url`` is a page on this source's domain, subdomains included.

        Subdomains count because a site's own pages live on them and the shopper
        named the site. Whole labels are compared, so ``notrtings.com`` is not
        ``rtings.com`` the way a substring test would have it.
        """
        try:
            host = urlsplit(url).hostname
        except ValueError:
            # An unbracketed IPv6 literal raises rather than naming a host, and a
            # page whose address cannot be read is nobody's.
            return False
        if not host:
            return False
        host = host.removeprefix(_WWW)
        return host == self.domain or host.endswith(f".{self.domain}")


def parse_source(spec: str) -> Source:
    """Read one source out of what the shopper wrote.

    Accepts a domain, a section of one, a full URL, or a YouTube handle::

        rtings.com
        rtings.com/headphones
        https://www.rtings.com/headphones/reviews/best/wireless
        @mkbhd
        youtube.com/@mkbhd

    Raises:
        ValueError: if it names no site -- which a plain word does not, however
            well known the person it names.
    """
    spec = spec.strip()
    if not spec:
        raise ValueError("A source cannot be blank.")

    if spec.startswith("@"):
        return Source(spec=spec, domain=_HANDLE_HOST, term=spec.split("/")[0])

    # Everything after the host is a path, and everything before it is a scheme
    # or credentials -- neither says which site this is.
    host, _, path = _SCHEME.sub("", spec).partition("/")
    host = host.split("@")[-1].split(":")[0].lower().removeprefix(_WWW)
    if not _HOSTNAME.fullmatch(host):
        raise ValueError(
            f"{spec!r} does not name a source. Give a site (rtings.com), a section "
            "of one (rtings.com/headphones) or a YouTube handle (@mkbhd)."
        )

    return Source(spec=spec, domain=host, term=_term(path))


def parse_sources(specs: str | Iterable[str]) -> tuple[Source, ...]:
    """Every source in ``specs``, in the order given and without repeats.

    Takes one string holding several -- how the web form sends them -- or the list
    a repeated flag builds up, in which each entry may again hold several. Both
    separate the same way, so a shopper who commas two sites into one ``--source``
    gets what the form would have given them rather than an error about a hostname
    with a comma in it.

    Two specs naming the same domain and term are one source: a second identical
    search would halve what the other sources are allowed. That holds across
    separators and flags alike, which is why they are parsed together.

    Raises:
        ValueError: if any of them names no site.
    """
    sources: dict[tuple[str, str], Source] = {}
    for entry in [specs] if isinstance(specs, str) else specs:
        for spec in _SEPARATORS.split(entry.strip()):
            if not spec:
                continue
            source = parse_source(spec)
            sources.setdefault((source.domain, source.term), source)
    return tuple(sources.values())


def format_sources(sources: Iterable[Source]) -> str:
    """Sources written back the way they were given, as one field's worth of text.

    The inverse of :func:`parse_sources` over what the shopper typed, so the form
    is handed back what it would have sent. A space separates: no spec has one.
    """
    return " ".join(source.spec for source in sources)


def _term(path: str) -> str:
    """The part of a path worth searching for: a channel handle, or a section.

    A query string and a fragment identify a page, where what is wanted is what
    the pages have in common, so they go -- as do the routing segments.
    """
    segments = [segment for segment in path.split("?")[0].split("#")[0].split("/") if segment]
    while segments and segments[0].lower() in _ROUTING:
        segments.pop(0)
    return segments[0] if segments else ""
