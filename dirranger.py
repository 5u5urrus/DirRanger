#!/usr/bin/env python3
# dirranger.py — list every entry from directory-listing pages (clean URLs, one per line).
# Author: Vahe Demirkhanyan
# Usage:
#   python3 dirranger.py https://test.com/testing/f1/ --depth 8
#   python3 dirranger.py https://test.com/testing/ --depth 8
# Notes:
#   - Same-origin enforced
#   - Path-scope enforced (only under the starting path)
#   - Parses anchors primarily from listing areas (<tbody> or <pre>) to avoid navbar/breadcrumb noise

import argparse, re, sys, urllib.parse as up, requests
from html.parser import HTMLParser
from collections import deque
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except Exception:  # pragma: no cover
    Retry = None


# ----------------------------
# HTML parsing
# ----------------------------

class AnchorParser(HTMLParser):
    """
    Capture anchors mainly from common directory listing regions:
      - Apache/nginx/lighttpd/dufs style: <pre> listings
      - Custom table UIs: <tbody> rows (like download.owncloud.com)
    Fallback: if nothing found in tbody/pre, return all anchors.
    """
    def __init__(self):
        super().__init__()
        self._in_pre = False
        self._in_tbody = False
        self._all = []
        self._strict = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t == "pre":
            self._in_pre = True
        elif t == "tbody":
            self._in_tbody = True

        if t == "a":
            href = dict(attrs).get("href")
            if not href:
                return
            href = href.strip()
            self._all.append(href)
            if self._in_pre or self._in_tbody:
                self._strict.append(href)

    def handle_endtag(self, tag):
        t = tag.lower()
        if t == "pre":
            self._in_pre = False
        elif t == "tbody":
            self._in_tbody = False

    @property
    def links(self):
        return self._strict if self._strict else self._all


# ----------------------------
# Helpers
# ----------------------------

def normalize(u: str) -> str:
    p = up.urlsplit(u)
    path = re.sub(r"(?<!:)//+", "/", p.path or "/")
    return up.urlunsplit((p.scheme, p.netloc, path, p.query, ""))

def normalize_dir(u: str) -> str:
    """Canonical directory URL: trailing slash, no query/fragment."""
    p = up.urlsplit(u)
    path = re.sub(r"(?<!:)//+", "/", p.path or "/")
    if not path.endswith("/"):
        path += "/"
    return up.urlunsplit((p.scheme, p.netloc, path, "", ""))

def resolve(base: str, href: str) -> str:
    return normalize(up.urljoin(base, href))

def _host_port(u: str):
    p = up.urlsplit(u)
    host = (p.hostname or "").lower()
    port = p.port
    if port is None:
        port = 80 if p.scheme == "http" else 443 if p.scheme == "https" else None
    return host, port

def same_origin(u: str, base_url: str) -> bool:
    return _host_port(u) == _host_port(base_url)

def _norm_path(path: str) -> str:
    if not path:
        return "/"
    path = re.sub(r"//+", "/", path)
    if not path.startswith("/"):
        path = "/" + path
    return path

def within_start_path(u: str, start_path_prefix: str) -> bool:
    p = up.urlsplit(u)
    path = _norm_path(p.path)
    return path.startswith(start_path_prefix)

def parent_of(url: str) -> str:
    return normalize_dir(up.urljoin(url, "../"))

def looks_like_index(html: str) -> bool:
    """
    Heuristics for directory-listing pages:
      - Apache "Index of"
      - nginx/lighttpd/dufs style
      - custom table UIs with Name/Size/Modified columns
    """
    h = html

    if re.search(r"<title>\s*Index of\b", h, re.I):
        return True
    if re.search(r"<h\d[^>]*>\s*Index of\b", h, re.I):
        return True
    if re.search(r"<pre>.*?\bName\s+Last\s+modified\b", h, re.I | re.S):
        return True
    if re.search(r"<th[^>]*>\s*Name\s*</th>", h, re.I):
        return True

    if re.search(r"<table\b", h, re.I):
        has_name = bool(re.search(r"<th[^>]*>.*?\bName\b", h, re.I | re.S))
        has_size = bool(re.search(r"<th[^>]*>.*?\bSize\b", h, re.I | re.S))
        has_modified = bool(re.search(r"<th[^>]*>.*?\bModified\b", h, re.I | re.S))
        if has_name and (has_size or has_modified):
            return True

    return False


# ----------------------------
# Core
# ----------------------------

def crawl(start_url: str, depth: int, timeout: float, quiet: bool, no_dedupe: bool):
    sess = requests.Session()
    sess.headers.update({"User-Agent": "dirranger/1.4"})

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

    start_url = normalize_dir(start_url)
    base_origin = start_url

    # Path-scope prefix: always a directory path ending with "/"
    start_path_prefix = _norm_path(up.urlsplit(start_url).path)
    if not start_path_prefix.endswith("/"):
        start_path_prefix += "/"

    q = deque([(start_url, 0)])
    visited_dirs = set()

    printed = None if no_dedupe else set()

    def say(u: str):
        nonlocal printed
        if printed is None:
            print(u, flush=True)
            return
        if u not in printed:
            print(u, flush=True)
            printed.add(u)

    def log(msg: str):
        if not quiet:
            print(msg, file=sys.stderr, flush=True)

    def in_scope(u: str) -> bool:
        if not same_origin(u, base_origin):
            return False
        if not within_start_path(u, start_path_prefix):
            return False
        return True

    while q:
        url, d = q.popleft()
        url = normalize_dir(url)

        if url in visited_dirs or d > depth:
            continue

        # Enforce scope for queued URLs
        if not in_scope(url):
            continue

        visited_dirs.add(url)

        try:
            r = sess.get(url, timeout=timeout, allow_redirects=True)
            r.raise_for_status()
            r.encoding = r.encoding or "utf-8"

            final_url = normalize(r.url)

            # Enforce scope AFTER redirects
            if not in_scope(final_url):
                continue

            # Landed on a file
            if not final_url.endswith("/"):
                say(final_url)
                continue

            # Only parse HTML-ish content
            ctype = (r.headers.get("Content-Type") or "").lower()
            if ctype and ("text/html" not in ctype and "application/xhtml+xml" not in ctype):
                continue

            html = r.text
            base = normalize_dir(final_url)

        except requests.RequestException as e:
            log(f"[warn] {url} -> {e.__class__.__name__}: {e}")
            continue
        except Exception as e:
            log(f"[warn] {url} -> {e.__class__.__name__}")
            continue

        parser = AnchorParser()
        parser.feed(html)

        if not looks_like_index(html):
            continue

        parent_abs = parent_of(base)

        for href in parser.links:
            if not href:
                continue

            # ignore query/fragment-only links (sort controls, anchors)
            if href.startswith("?") or href.startswith("#"):
                continue

            child = resolve(base, href)

            # avoid self/parent cycles
            if normalize_dir(child) == base or normalize_dir(child) == parent_abs:
                continue

            # scope enforcement (origin + path prefix)
            if not in_scope(child):
                continue

            if child.endswith("/"):
                child_dir = normalize_dir(child)  # drops query
                if not in_scope(child_dir):
                    continue
                say(child_dir)
                if child_dir not in visited_dirs and d + 1 <= depth:
                    q.append((child_dir, d + 1))
            else:
                say(normalize(child))


def main():
    ap = argparse.ArgumentParser(description="Print ALL URLs from directory listings (clean, one per line).")
    ap.add_argument("url", help="Starting directory URL (e.g., https://test.com/testing/)")
    ap.add_argument("--depth", type=int, default=8, help="Max recursion depth (default: 8)")
    ap.add_argument("--timeout", type=float, default=8.0, help="HTTP timeout seconds (default: 8.0)")
    ap.add_argument("--quiet", action="store_true", help="Suppress warnings/debug to stderr")
    ap.add_argument("--no-dedupe", action="store_true", help="Do not deduplicate printed URLs (lower memory)")
    args = ap.parse_args()

    crawl(args.url, args.depth, args.timeout, args.quiet, args.no_dedupe)


if __name__ == "__main__":
    main()
