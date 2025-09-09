#!/usr/bin/env python3
# dirindex_urls.py — list every entry from open directory-index pages (clean URLs, one per line).
# Usage:
#   python3 dirindex_urls.py http://10.129.236.86/vendor/ --depth 8

import argparse, re, sys, urllib.parse as up, requests
from html.parser import HTMLParser
from collections import deque
from requests.adapters import HTTPAdapter
try:
    # urllib3>=1.26 / 2.x
    from urllib3.util.retry import Retry
except Exception:  # pragma: no cover
    Retry = None  # retries will be skipped if urllib3 isn't available


# ----------------------------
# HTML parsing
# ----------------------------

class AnchorParser(HTMLParser):
    """
    Capture anchors from common autoindex layouts:
      - Apache (table)
      - nginx/lighttpd/dufs (pre)
      - generic <a> fallback
    """
    def __init__(self):
        super().__init__()
        self.links = []
        self._in_table = False
        self._in_pre = False

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t == "table":
            self._in_table = True
        elif t == "pre":
            self._in_pre = True
        if t == "a":  # accept anchors in table/pre or anywhere as a fallback
            href = dict(attrs).get("href")
            if href:
                self.links.append(href.strip())

    def handle_endtag(self, tag):
        t = tag.lower()
        if t == "table":
            self._in_table = False
        elif t == "pre":
            self._in_pre = False


# ----------------------------
# Helpers
# ----------------------------

def is_index_of(html: str) -> bool:
    """Heuristics for autoindex pages."""
    return bool(
        re.search(r"<title>\s*Index of\b", html, re.I) or
        re.search(r"<h\d[^>]*>\s*Index of\b", html, re.I) or
        re.search(r"<th[^>]*>\s*Name\s*</th>", html, re.I) or
        # nginx-style: a <pre> with a header row including Name / Last modified
        re.search(r"<pre>.*?\bName\s+Last\s+modified\b", html, re.I | re.S)
    )

def normalize(u: str) -> str:
    p = up.urlsplit(u)
    # keep query for files, drop fragment; ensure path has single slashes
    path = re.sub(r"(?<!:)//+", "/", p.path or "/")
    return up.urlunsplit((p.scheme, p.netloc, path, p.query, ""))

def ensure_dir(u: str) -> str:
    return u if u.endswith("/") else u + "/"

def resolve(base: str, href: str) -> str:
    return normalize(up.urljoin(base, href))

def _host_port(u: str):
    p = up.urlsplit(u)
    host = (p.hostname or "").lower()
    port = p.port
    if port is None:
        if p.scheme == "http":
            port = 80
        elif p.scheme == "https":
            port = 443
    return host, port

def same_origin(u: str, base_url: str) -> bool:
    """Compare host + effective port (handles example.com vs example.com:80)."""
    return _host_port(u) == _host_port(base_url)

def parent_of(url: str) -> str:
    # canonical parent directory URL (with trailing /)
    return ensure_dir(normalize(up.urljoin(url, "../")))


# ----------------------------
# Core
# ----------------------------

def crawl(start_url: str, depth: int, timeout: float, quiet: bool, no_dedupe: bool):
    sess = requests.Session()
    sess.headers.update({"User-Agent": "dirindex-urls/1.1"})

    # Optional retry policy for transient errors
    if Retry is not None:
        retry = Retry(
            total=2,
            backoff_factor=0.3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(["GET", "HEAD"])
        )
        adapter = HTTPAdapter(max_retries=retry)
        sess.mount("http://", adapter)
        sess.mount("https://", adapter)

    start_url = ensure_dir(normalize(start_url))
    base_origin = start_url  # keep full URL for origin compare

    q = deque([(start_url, 0)])
    visited_dirs = set()     # directory-index pages we already parsed
    printed = set()          # URLs already printed (avoid duplicates)

    def say(u: str):
        if no_dedupe or u not in printed:
            print(u, flush=True)
            printed.add(u)

    def log(msg: str):
        if not quiet:
            print(msg, file=sys.stderr, flush=True)

    while q:
        url, d = q.popleft()
        if url in visited_dirs or d > depth:
            continue
        visited_dirs.add(url)

        try:
            r = sess.get(url, timeout=timeout, allow_redirects=True)
            r.raise_for_status()
            r.encoding = r.encoding or "utf-8"
            html = r.text
            base = normalize(r.url)
            if not base.endswith("/"):
                # landed on a file, not an index page
                say(base)
                continue
        except requests.RequestException as e:
            log(f"[warn] {url} -> {e.__class__.__name__}: {e}")
            continue
        except Exception as e:  # unlikely, keep crawler resilient
            log(f"[warn] {url} -> {e.__class__.__name__}")
            continue

        if not is_index_of(html):
            # not an autoindex page — stop recursion from here
            continue

        # parent directory (absolute) to skip cycles
        parent_abs = parent_of(base)

        # parse links in the index
        parser = AnchorParser()
        parser.feed(html)

        for href in parser.links:
            # ignore obvious parent markers and sort links
            if href.startswith("?") or href.startswith("#"):
                continue
            # resolve to absolute
            child = resolve(base, href)

            # obey same-origin scope (avoid redirecting off-site silently)
            if not same_origin(child, base_origin):
                log(f"[skip] out-of-scope: {child}")
                continue

            # skip explicit parent/self to prevent vendor <-> composer ping-pong
            if child == base or child == parent_abs:
                continue

            if child.endswith("/"):
                # directory entry (index page)
                say(child)                      # print directory URL
                if child not in visited_dirs and d + 1 <= depth:
                    q.append((child, d + 1))
            else:
                # file entry
                say(child)


def main():
    ap = argparse.ArgumentParser(description="Print ALL URLs from open directory indexes (clean, one per line).")
    ap.add_argument("url", help="Starting directory URL (e.g., http://host/vendor/)")
    ap.add_argument("--depth", type=int, default=8, help="Max recursion depth (default: 8)")
    ap.add_argument("--timeout", type=float, default=8.0, help="HTTP timeout seconds (default: 8.0)")
    ap.add_argument("--quiet", action="store_true", help="Suppress warnings/debug to stderr")
    ap.add_argument("--no-dedupe", action="store_true", help="Do not deduplicate printed URLs (lower memory)")
    args = ap.parse_args()

    crawl(args.url, args.depth, args.timeout, args.quiet, args.no_dedupe)


if __name__ == "__main__":
    main()
