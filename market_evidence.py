"""Bounded, key-free news research. Headlines are signals, not verified demand."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import urllib.parse
import urllib.request

from daily_digest import UA, clean, parse_xml
from kr_sources import GNEWS_URL, parse_date, strip_outlet

MAX_RESULTS = 4
LOOKBACK_DAYS = 90
GENERAL_QUERIES = (
    ("demand", "(소비자 OR 이용자) (불편 OR 수요 OR 설문)"),
    ("trend", "(인구 OR 생활 OR 소비) (변화 OR 전망)"),
    ("trend", "(제도 OR 정책) (시행 OR 개정)"),
)


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read(2_000_000).decode("utf-8", errors="replace")


def collect(today, cards=(), fetcher=fetch):
    """Three broad queries, or two queries per shortlisted card (max three)."""
    queries = []
    if cards:
        for card in cards[:3]:
            terms = card.get("kw", [])[:2] or [card["name"]]
            term = "(" + " OR ".join('"' + clean(t, 60).replace('"', '') + '"' for t in terms) + ")"
            queries += [("demand", f"{term} (불편 OR 수요 OR 설문)"),
                        ("trend", f"{term} (전망 OR 변화 OR 증가)")]
    else:
        queries = list(GENERAL_QUERIES)
    queries = list(dict.fromkeys(queries))
    end = datetime.fromisoformat(today).replace(tzinfo=timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=LOOKBACK_DAYS)

    def search(pair):
        kind, query = pair
        url = GNEWS_URL.format(q=urllib.parse.quote(f"{query} when:{LOOKBACK_DAYS}d"))
        try:
            root = parse_xml(fetcher(url))
            records = []
            for node in root.findall(".//item"):
                published = parse_date(node.findtext("pubDate"))
                link = (node.findtext("link") or "").strip()
                try:
                    parts = urllib.parse.urlsplit(link)
                except ValueError:
                    continue
                title = strip_outlet(clean(node.findtext("title"), 220), node.findtext("source"))
                if (not title or not published or not start <= published < end
                        or parts.scheme not in ("http", "https") or not parts.netloc):
                    continue
                records.append({"kind": kind, "title": title, "url": link,
                                "date": published.date().isoformat(),
                                "outlet": clean(node.findtext("source"), 80),
                                "query": query, "scope": "뉴스 제목만 확인; 수요·전망의 직접 검증 아님"})
            records.sort(key=lambda row: row["date"], reverse=True)
            return records[:MAX_RESULTS], None
        except Exception:
            return [], f"검색 실패: {query}"

    records, warnings, seen = [], [], set()
    with ThreadPoolExecutor(max_workers=3) as pool:
        for found, warning in pool.map(search, queries):
            if warning:
                warnings.append(warning)
            for row in found:
                # A story matching two query types remains one source.
                key = row["url"]
                if key not in seen:
                    seen.add(key)
                    records.append(row)
    if not records:
        scope = "선정 후보 관련" if cards else "전체 비교용"
        warnings.append(f"{scope} 수요·트렌드 뉴스 근거를 확보하지 못했습니다. 전망은 검증 전 가설입니다.")
    return records, warnings
