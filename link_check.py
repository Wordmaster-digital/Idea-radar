"""Verify public HTTP links without the LLM; unchecked links remain plain titles."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

LINK = re.compile(r"\[((?:[^\[\]]|\[[^\]]*\])*)\]\((https?://[^\s)]+)\)")
URL = re.compile(r"https?://[^\s<>\"')]+")
MAX_LINKS = 50
TIMEOUT = 8


def public_url(url, resolver=socket.getaddrinfo):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("invalid public URL")
    addresses = resolver(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("non-public URL")
    return url


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def probe(url):
    public_url(url)
    opener = urllib.request.build_opener(PublicRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; IdeaRadar-LinkCheck/1.0)"})
    with opener.open(req, timeout=TIMEOUT) as response:
        final = response.geturl()
        public_url(final)
        body = response.read(65536)
        title = re.search(rb"<title[^>]*>(.*?)</title", body, re.I | re.S)
        blocked = title and any(word in title[1].lower() for word in
                                (b"access denied", b"just a moment", b"robot verification", b"captcha"))
        consent = urllib.parse.urlsplit(final).hostname == "consent.google.com"
        return bool(200 <= response.status < 300 and body and not blocked and not consent), final, response.status


def check(urls, probe_fn=probe):
    urls = list(dict.fromkeys(urls))
    checked_at = datetime.now(timezone.utc).isoformat()
    results = {url: {"ok": False, "url": "", "status": "상한 초과", "checked_at": checked_at}
               for url in urls}

    def one(url):
        try:
            ok, final, status = probe_fn(url)
            return url, {"ok": bool(ok), "url": final if ok else "", "status": str(status), "checked_at": checked_at}
        except urllib.error.HTTPError as exc:
            return url, {"ok": False, "url": "", "status": f"HTTP {exc.code}", "checked_at": checked_at}
        except Exception:
            return url, {"ok": False, "url": "", "status": "접속 확인 불가", "checked_at": checked_at}

    with ThreadPoolExecutor(max_workers=5) as pool:
        for url, result in pool.map(one, urls[:MAX_LINKS]):
            results[url] = result
    return results


def links(markdown):
    return list(dict.fromkeys([match[1] for match in LINK.findall(markdown)] + URL.findall(markdown)))


def sanitize(markdown, results):
    def replace(match):
        title, url = match.groups()
        result = results.get(url, {})
        if result.get("ok") and result.get("url", "").startswith(("https://", "http://")):
            return f"[{title}]({result['url']})"
        return f"{title} [링크 확인 불가]"
    text = LINK.sub(replace, markdown)
    verified = {r.get("url") for r in results.values() if r.get("ok")}
    return URL.sub(lambda m: m[0] if m[0] in verified else "[링크 확인 불가]", text)
