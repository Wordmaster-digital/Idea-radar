#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_digest.py — 아이디어 자동 수집 + 한국 중복 대조 + 디스코드 발송

수집 소스 (전부 무료, 인증 불필요, 실제 점수 포함):
  - Hacker News   : Algolia API      → points
  - GitHub        : 공식 Search API  → stars (최근 생성 저장소)
  - Lobsters      : hottest.json     → score
  - Dev.to        : 공식 API         → reactions
  - Product Hunt  : 공식 RSS         → 점수 없음 (GraphQL은 토큰 필요)

  ※ Reddit은 제외. 클라우드 IP를 403으로 차단한다.
    쓰려면 OAuth 앱 등록이 필요하고, 그마저 정책 변경에 취약하다.

대조:
  - 각 항목에서 키워드를 뽑아 한국 앱스토어(iTunes Search API)에 조회
  - 유사 앱이 있으면 🔴, 없으면 🟢
  - 한계: 영어 단어 매칭이라 오탐/누락이 있다. 1차 필터일 뿐 판정기가 아니다.

사용법:
    export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
    python3 daily_digest.py
    python3 daily_digest.py --dry-run          # 화면 출력만
    python3 daily_digest.py --top 25           # 상위 N건만
    python3 daily_digest.py --only-gaps        # 🟢만
    python3 daily_digest.py --sources hn,gh    # 소스 선택
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

TIMEOUT = 25
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

ITUNES = "https://itunes.apple.com/search"

NOISE = re.compile(
    r"\b(who is hiring|hiring|ask hn:|tell hn:|died|dies|obituary|rip\b|"
    r"lawsuit|acquires|acquisition|funding round|raises \$|series [abc]\b)",
    re.I,
)
STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "with", "your", "you", "our", "that",
    "this", "from", "into", "using", "use", "how", "why", "what", "new", "open",
    "source", "free", "best", "just", "get", "make", "made", "build", "built",
    "show", "hn", "ask", "app", "tool", "api", "web", "all", "one", "can", "now",
    "its", "his", "her", "was", "are", "has", "have", "not", "but", "out", "via",
    "when", "who", "where", "about", "more", "than", "over", "some", "any",
}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def fetch_json(url):
    return json.loads(fetch(url))


def clean(s, n=150):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:n]


# =============================================================== 수집 소스

def src_hn(limit=15, min_points=150, days=2):
    """Hacker News — Algolia API. 요청 1회로 끝나고 points가 들어온다."""
    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    nf = urllib.parse.quote(f"points>{min_points},created_at_i>{since}")
    url = ("https://hn.algolia.com/api/v1/search_by_date?tags=story"
           f"&numericFilters={nf}&hitsPerPage={limit * 3}")
    try:
        hits = fetch_json(url).get("hits", [])
    except Exception as e:
        print(f"[HN] 실패: {e}", file=sys.stderr)
        return []
    out = []
    for h in hits:
        title = h.get("title") or ""
        if not title or NOISE.search(title):
            continue
        out.append({
            "source": "HN",
            "title": title,
            "score": h.get("points", 0),
            "unit": "pt",
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
            "extra": f"{h.get('num_comments', 0)} comments",
        })
        if len(out) >= limit:
            break
    return out


def src_github(limit=15, days=14, min_stars=200):
    """GitHub — 공식 Search API. 최근 생성된 저장소를 별 순으로."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    q = urllib.parse.quote(f"created:>{since} stars:>{min_stars}")
    url = (f"https://api.github.com/search/repositories?q={q}"
           f"&sort=stars&order=desc&per_page={limit}")
    try:
        items = fetch_json(url).get("items", [])
    except Exception as e:
        print(f"[GitHub] 실패: {e}", file=sys.stderr)
        return []
    return [{
        "source": "GitHub",
        "title": f"{i['full_name']} — {clean(i.get('description'), 80)}",
        "score": i.get("stargazers_count", 0),
        "unit": "★",
        "url": i.get("html_url", ""),
        "extra": clean(i.get("description"), 120),
    } for i in items]


def src_lobsters(limit=10, min_score=15):
    """Lobsters — hottest.json. HN보다 기술 편중이지만 노이즈가 적다."""
    try:
        items = fetch_json("https://lobste.rs/hottest.json")
    except Exception as e:
        print(f"[Lobsters] 실패: {e}", file=sys.stderr)
        return []
    out = []
    for i in items:
        if i.get("score", 0) < min_score or NOISE.search(i.get("title", "")):
            continue
        out.append({
            "source": "Lobsters",
            "title": i.get("title", ""),
            "score": i.get("score", 0),
            "unit": "pt",
            "url": i.get("url") or i.get("comments_url", ""),
            "extra": ", ".join(i.get("tags", [])[:3]),
        })
        if len(out) >= limit:
            break
    return out


def src_devto(limit=10, min_reactions=30):
    """Dev.to — 공식 API. 개발자가 실제로 만든 것 위주."""
    try:
        items = fetch_json(f"https://dev.to/api/articles?top=2&per_page={limit * 3}")
    except Exception as e:
        print(f"[Dev.to] 실패: {e}", file=sys.stderr)
        return []
    out = []
    for i in items:
        r = i.get("positive_reactions_count", 0)
        if r < min_reactions:
            continue
        out.append({
            "source": "Dev.to",
            "title": i.get("title", ""),
            "score": r,
            "unit": "♥",
            "url": i.get("url", ""),
            "extra": ", ".join(i.get("tag_list", [])[:3]),
        })
        if len(out) >= limit:
            break
    return out


def src_producthunt(limit=10):
    """Product Hunt — 공식 RSS. 투표 수는 안 들어온다(GraphQL은 토큰 필요)."""
    try:
        root = ET.fromstring(fetch("https://www.producthunt.com/feed"))
    except Exception as e:
        print(f"[ProductHunt] 실패: {e}", file=sys.stderr)
        return []
    out = []
    for it in root.findall(".//item")[:limit]:
        out.append({
            "source": "PH",
            "title": (it.findtext("title") or "").strip(),
            "score": None,
            "unit": "",
            "url": (it.findtext("link") or "").strip(),
            "extra": clean(it.findtext("description"), 120),
        })
    return out


SOURCES = {
    "hn": src_hn,
    "gh": src_github,
    "lob": src_lobsters,
    "dev": src_devto,
    "ph": src_producthunt,
}

# 소스마다 점수 체계가 달라서 그대로 섞으면 GitHub 별 수가 다 이긴다.
# 소스별 가중치로 대략 맞춰 정렬한다.
WEIGHT = {"HN": 1.0, "GitHub": 0.10, "Lobsters": 4.0, "Dev.to": 2.0, "PH": 0.0}


# =============================================================== 한국 대조

def keywords(text, n=3):
    text = re.split(r"[—\-–|:]", text)[0]      # GitHub 제목의 설명 부분 제거
    words = re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", text.lower())
    seen, out = set(), []
    for w in words:
        if w in STOPWORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= n:
            break
    return out


def kr_appstore(term, limit=5):
    qs = urllib.parse.urlencode({
        "term": term, "country": "kr", "entity": "software",
        "limit": limit, "lang": "ko_kr",
    })
    try:
        data = fetch_json(f"{ITUNES}?{qs}")
    except Exception:
        return []
    return [{"name": a.get("trackName", ""), "ratings": a.get("userRatingCount", 0) or 0}
            for a in data.get("results", [])]


def annotate_kr(item):
    kws = keywords(item["title"])
    if not kws:
        item["kr"], item["kr_apps"] = "판정불가", []
        return item
    hits = []
    for kw in kws[:2]:
        for app in kr_appstore(kw):
            # 오탐 방지: 앱 이름에 키워드가 실제로 있고 실사용 흔적이 있을 때만
            if app["ratings"] >= 10 and kw in app["name"].lower():
                hits.append(app)
        time.sleep(0.12)
    uniq = {a["name"]: a for a in hits}
    item["kr_apps"] = sorted(uniq.values(), key=lambda x: -x["ratings"])[:3]
    item["kr"] = "KR 유사" if item["kr_apps"] else "KR 공백"
    return item


# =================================================================== 출력

def to_blocks(items, header):
    lines, blocks = [header], []
    for it in items:
        mark = {"KR 공백": "🟢", "KR 유사": "🔴"}.get(it["kr"], "⚪")
        sc = f" `{it['score']:,}{it['unit']}`" if it["score"] else ""
        line = f"{mark} **[{it['source']}]** [{it['title'][:105]}]({it['url']}){sc}"
        if it["kr_apps"]:
            line += "\n　↳ 국내 유사: " + ", ".join(a["name"][:22] for a in it["kr_apps"])
        elif it["extra"]:
            line += f"\n　↳ {it['extra'][:105]}"
        if sum(len(x) for x in lines) + len(line) > 1800:
            blocks.append("\n".join(lines))
            lines = []
        lines.append(line)
    if lines:
        blocks.append("\n".join(lines))
    return blocks


def post_discord(webhook, content):
    payload = json.dumps({"content": content, "flags": 4}).encode()
    req = urllib.request.Request(
        webhook, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--top", type=int, default=30, help="상위 N건만 발송")
    p.add_argument("--only-gaps", action="store_true", help="🟢만 발송")
    p.add_argument("--sources", default="hn,gh,lob,dev,ph",
                   help="쉼표로 구분: hn,gh,lob,dev,ph")
    args = p.parse_args()

    items = []
    for key in [s.strip() for s in args.sources.split(",")]:
        fn = SOURCES.get(key)
        if not fn:
            print(f"알 수 없는 소스: {key}", file=sys.stderr)
            continue
        got = fn()
        print(f"  {key}: {len(got)}건", file=sys.stderr)
        items += got

    if not items:
        print("수집된 항목 없음", file=sys.stderr)
        return 1

    # 소스별 가중치로 정규화해 정렬
    items.sort(key=lambda x: (x["score"] or 0) * WEIGHT.get(x["source"], 1), reverse=True)
    items = items[:args.top]

    print(f"{len(items)}건 → 한국 대조 중...", file=sys.stderr)
    items = [annotate_kr(it) for it in items]

    if args.only_gaps:
        items = [it for it in items if it["kr"] == "KR 공백"]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    by_src = {}
    for it in items:
        by_src[it["source"]] = by_src.get(it["source"], 0) + 1
    gaps = sum(1 for it in items if it["kr"] == "KR 공백")
    header = (f"## 🗓 {today} 아이디어 다이제스트\n"
              f"{len(items)}건 · 🟢 {gaps} / 🔴 {len(items) - gaps} · "
              + " ".join(f"{k} {v}" for k, v in by_src.items()) + "\n")

    blocks = to_blocks(items, header)

    if args.dry_run:
        print("\n\n".join(blocks))
        return 0

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        print("DISCORD_WEBHOOK_URL 환경변수가 없습니다.", file=sys.stderr)
        return 1
    for b in blocks:
        post_discord(webhook, b)
        time.sleep(1)
    print(f"발송 완료 ({len(blocks)} 메시지)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
