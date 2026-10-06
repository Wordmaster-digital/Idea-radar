"""Bounded public-page retrieval. Secrets are never sent to discovered sites."""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.robotparser import RobotFileParser
import json

AGENT = "OpportunityRadar/1.0"
MAX_BYTES = 700_000


def public_url(url):
    try:
        p = urlsplit(url)
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            return False
        if p.port not in (None, 80, 443):
            return False
        addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
        return bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses)
    except (ValueError, OSError):
        return False


def canonical(url):
    p = urlsplit(url.strip())
    # Preserve event identifiers and query order. Remove only common tracking keys.
    query = "&".join(x for x in p.query.split("&") if x and not
                     x.split("=", 1)[0].lower().startswith(("utm_", "fbclid", "gclid")))
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", query, ""))


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public_url(newurl):
            raise ValueError("비공개 주소로 이동")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def get(url, headers=None):
    if not public_url(url):
        raise ValueError("공개 웹 주소 아님")
    request = Request(url, headers={"User-Agent": AGENT, **(headers or {})})
    with build_opener(PublicRedirect()).open(request, timeout=12) as response:
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            data = data[:MAX_BYTES]
        charset = response.headers.get_content_charset()
        if not charset:
            match = re.search(rb'charset\s*=\s*["\x27]?([\w-]+)', data[:5000], re.I)
            charset = match[1].decode("ascii") if match else "utf-8"
        try:
            body = data.decode(charset, errors="replace")
        except LookupError:
            body = data.decode("utf-8", errors="replace")
        return {"url": canonical(response.url), "body": body,
                "type": response.headers.get_content_type()}


class PageText(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.parts, self.links, self.hidden = base, [], [], 0
        self.anchor = None
        self.structured, self.json_script, self.script_parts = [], False, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("head", "script", "style", "noscript"):
            self.hidden += 1
        if tag == 'script' and attrs.get('type') == 'application/ld+json':
            self.json_script, self.script_parts = True, []
        if tag == "a" and attrs.get("href") and not self.hidden:
            self.anchor = [urljoin(self.base, attrs["href"]), []]

    def handle_endtag(self, tag):
        if tag == 'script' and self.json_script:
            try:
                self.structured.append(json.loads(''.join(self.script_parts)))
            except ValueError:
                pass
            self.json_script = False
        if tag in ("head", "script", "style", "noscript") and self.hidden:
            self.hidden -= 1
        if tag == "a" and self.anchor:
            self.links.append({"url": self.anchor[0], "title": " ".join(self.anchor[1]).strip()})
            self.anchor = None

    def handle_data(self, text):
        if self.json_script:
            self.script_parts.append(text)
        if not self.hidden and text.strip():
            self.parts.append(text.strip())
            if self.anchor:
                self.anchor[1].append(text.strip())


def page(url, *, robots=True):
    """Honor explicit crawler exclusion; never bypass login/captcha or render scripts."""
    try:
        if robots:
            p = urlsplit(url)
            robots_url = urlunsplit((p.scheme, p.netloc, "/robots.txt", "", ""))
            try:
                policy = RobotFileParser()
                policy.parse(get(robots_url)["body"].splitlines())
                if not policy.can_fetch(AGENT, url):
                    return {"ok": False, "reason": "자동 수집 제한", "url": url, "text": "", "links": []}
            except Exception:
                pass  # Unavailable robots does not override a site's access controls.
        response = get(url)
        if response["type"] not in ("text/html", "text/plain", "application/xhtml+xml"):
            return {"ok": False, "reason": "첨부·본문 별도 확인 필요", "url": response["url"], "text": "", "links": []}
        parser = PageText(response["url"])
        parser.feed(response["body"])
        text = " ".join(parser.parts)
        if len(text) < 40 or any(x in text[:1200].lower() for x in ("verify you are human", "access denied", "just a moment")):
            return {"ok": False, "reason": "본문 접근 제한", "url": url, "text": "", "links": []}
        return {"ok": True, "reason": "본문 조회됨", "url": response["url"], "text": text, "links": parser.links, "structured": parser.structured}
    except Exception:
        return {"ok": False, "reason": "접속 확인 불가", "url": url, "text": "", "links": []}


def pages(urls):
    urls = list(dict.fromkeys(urls))[:60]
    with ThreadPoolExecutor(max_workers=5) as pool:
        return dict(zip(urls, pool.map(page, urls)))
