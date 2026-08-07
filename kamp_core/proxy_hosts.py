"""Which remote hosts kamp will talk to, and the one rule for deciding (KAMP-649).

The host-matching rule was written twice before this module existed — once in
``kamp_core.server`` for the Electron proxy relay, once in
``kamp_daemon.discovery_sources`` with a comment noting it mirrored the first. A
third copy was about to be added for the crate art proxy, so it lives here now
and everyone imports it.

The rule is exact-match-or-subdomain, and the *exact* half is load-bearing: a
bare suffix test rejects ``bandcamp.com`` itself, which is where the discover API
lives, while a bare ``in`` test would accept ``evilbandcamp.com``.
"""

from __future__ import annotations

from urllib.parse import urlparse

#: Hosts that may be reached through Electron's ``net.fetch``, which carries the
#: user's Bandcamp session cookies. Anything not listed here could be used by a
#: malicious extension or local process to exfiltrate those cookies.
ALLOWED_PROXY_HOSTS: frozenset[str] = frozenset(
    {"bandcamp.com", "f4.bcbits.com", "t4.bcbits.com"}
)

#: Hosts the crate art proxy will fetch images from. A strict subset of
#: ALLOWED_PROXY_HOSTS: the art CDN serves covers publicly with no cookies, while
#: bandcamp.com is an authenticated surface with no business answering an <img>.
#: Narrower is deliberate — the stored art_url is remote data, and
#: ``art_url_from_image`` passes through any string that starts with ``http``.
ART_HOSTS: frozenset[str] = frozenset({"f4.bcbits.com", "t4.bcbits.com"})

#: Hosts discovery will fetch pages and API responses from.
FETCHABLE_HOSTS: frozenset[str] = frozenset({"bandcamp.com"})


def host_allowed(url: str, hosts: frozenset[str]) -> bool:
    """True if *url*'s host is one of *hosts* or a subdomain of one."""
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith(f".{h}") for h in hosts)
