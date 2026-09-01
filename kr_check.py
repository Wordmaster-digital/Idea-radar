#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kr_check.py — 한국 시장 중복 검사기

키워드를 넣으면 아래를 동시에 조회해서 마크다운 리포트를 출력한다.
  1) 애플 앱스토어 (한국) — iTunes Search API, 인증키 불필요
  2) 네이버 블로그/카페/뉴스/웹 — 네이버 검색 API, 키 필요 (없으면 자동 생략)

출력 결과를 Claude/ChatGPT 대화창에 붙여넣어 아이디어 계단 탐색기와 함께 사용.

사용법:
    python kr_check.py "노령견 위탁" "시니어펫 호텔" "강아지 맡길 곳"
    python kr_check.py --out report.md "침수 이력"

네이버 API 키 (선택):
    https://developers.naver.com/apps 에서 '검색' API 등록 후
    export NAVER_CLIENT_ID=xxx
    export NAVER_CLIENT_SECRET=yyy
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

TIMEOUT = 15
UA = "Mozilla/5.0 (compatible; kr-market-check/1.0)"

NAVER_ID = os.environ.get("NAVER_CLIENT_ID", "").strip()
NAVER_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "").strip()

TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(s):
    """네이버 API는 <b> 태그로 검색어를 감싸서 돌려준다."""
    if not s:
        return ""
    s = TAG_RE.sub("", s)
    return (s.replace("&lt;", "<").replace("&gt;", ">")
             .replace("&amp;", "&").replace("&quot;", '"')
             .replace("&#39;", "'").strip())


def get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------- App Store

def search_appstore(term, limit=15):
    """iTunes Search API. 인증 불필요. 한국 스토어 기준."""
    qs = urllib.parse.urlencode({
        "term": term, "country": "kr", "entity": "software",
        "limit": limit, "lang": "ko_kr",
    })
    try:
        data = get_json("https://itunes.apple.com/search?" + qs)
    except Exception as e:
        return {"error": str(e), "items": []}

    items = []
    for a in data.get("results", []):
        items.append({
            "name": a.get("trackName", ""),
            "seller": a.get("sellerName", ""),
            "released": (a.get("releaseDate") or "")[:10],
            "updated": (a.get("currentVersionReleaseDate") or "")[:10],
            "rating_count": a.get("userRatingCount", 0) or 0,
            "rating": a.get("averageUserRating"),
            "genre": a.get("primaryGenreName", ""),
            "url": a.get("trackViewUrl", ""),
            "desc": (a.get("description") or "").replace("\n", " ")[:160],
        })
    return {"error": None, "items": items}


# -------------------------------------------------------------------- Naver

NAVER_TARGETS = [
    ("blog", "블로그"),
    ("cafearticle", "카페"),
    ("news", "뉴스"),
    ("webkr", "웹문서"),
]


def search_naver(term, target, display=8):
    if not (NAVER_ID and NAVER_SECRET):
        return {"error": "NO_KEY", "total": None, "items": []}
    qs = urllib.parse.urlencode({"query": term, "display": display, "sort": "sim"})
    url = f"https://openapi.naver.com/v1/search/{target}.json?{qs}"
    headers = {
        "X-Naver-Client-Id": NAVER_ID,
        "X-Naver-Client-Secret": NAVER_SECRET,
        "User-Agent": UA,
    }
    try:
        data = get_json(url, headers)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "total": None, "items": []}
    except Exception as e:
        return {"error": str(e), "total": None, "items": []}

    items = []
    for it in data.get("items", []):
        items.append({
            "title": strip_tags(it.get("title")),
            "desc": strip_tags(it.get("description"))[:140],
            "link": it.get("link", ""),
            "date": it.get("postdate") or it.get("pubDate") or "",
        })
    return {"error": None, "total": data.get("total"), "items": items}


# ------------------------------------------------------------------- Report

MANUAL_CHECKS = """### 수동 확인 (스크립트로 안 되는 것)

| 채널 | 확인 방법 |
|---|---|
| 구글플레이 | play.google.com 에서 키워드 검색 (공식 API 없음) |
| 인스타그램 | 해시태그 검색 — 개인 사업자의 주 채널이라 중요 |
| 혁신의숲 | innoforest.co.kr — 국내 스타트업 매출·투자·MAU |
| THE VC / 넥스트유니콘 | 투자 이력 |
| KIPRIS | kipris.or.kr — 특허·상표 선점 여부 (무료) |
| 공공데이터포털 활용사례 | data.go.kr — 같은 데이터로 누가 뭘 만들었는지 |
| 규제샌드박스 | sandbox.kiat.or.kr — 법적 회색지대를 이미 뚫은 곳 |
"""


def build_report(terms):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    out = [f"# 한국 시장 중복 검사\n", f"검색 시각: {now}", f"키워드: {', '.join(terms)}\n"]

    # ---- App Store
    out.append("## 1. 애플 앱스토어 (한국)\n")
    any_app = False
    for t in terms:
        r = search_appstore(t)
        out.append(f"### `{t}`\n")
        if r["error"]:
            out.append(f"조회 실패: {r['error']}\n")
            continue
        if not r["items"]:
            out.append("검색 결과 없음\n")
            continue
        any_app = True
        out.append("| 앱 | 개발사 | 출시 | 최종 업데이트 | 평가수 |")
        out.append("|---|---|---|---|---|")
        for a in r["items"]:
            out.append(
                f"| [{a['name']}]({a['url']}) | {a['seller']} | {a['released']} "
                f"| {a['updated']} | {a['rating_count']} |"
            )
        out.append("")
        # 상위 3개는 설명까지
        for a in r["items"][:3]:
            if a["desc"]:
                out.append(f"- **{a['name']}**: {a['desc']}")
        out.append("")

    if not any_app:
        out.append("> 앱스토어에 관련 앱 없음. 다만 구글플레이 전용일 수 있으니 수동 확인 필요.\n")

    # ---- Naver
    out.append("## 2. 네이버 검색\n")
    if not (NAVER_ID and NAVER_SECRET):
        out.append(
            "> 네이버 API 키가 없어 생략됨.\n"
            "> developers.naver.com/apps 에서 '검색' API 등록 후\n"
            "> `export NAVER_CLIENT_ID=...` / `export NAVER_CLIENT_SECRET=...`\n"
        )
    else:
        for t in terms:
            out.append(f"### `{t}`\n")
            for target, label in NAVER_TARGETS:
                r = search_naver(t, target)
                if r["error"]:
                    out.append(f"**{label}** — 조회 실패 ({r['error']})\n")
                    continue
                total = r["total"]
                out.append(f"**{label}** (총 {total:,}건)" if total is not None else f"**{label}**")
                if not r["items"]:
                    out.append("- 결과 없음\n")
                    continue
                for it in r["items"][:5]:
                    d = f" · {it['date'][:10]}" if it["date"] else ""
                    out.append(f"- [{it['title']}]({it['link']}){d}")
                    if it["desc"]:
                        out.append(f"  - {it['desc']}")
                out.append("")

    out.append(MANUAL_CHECKS)
    out.append("""
### 다음 단계

이 결과를 아이디어 계단 탐색기 프롬프트에 붙여넣고 이렇게 말할 것:

> 아래는 앱스토어·네이버 검색 결과다. STEP 3의 1·2번은 이걸로 대체하고 3~6번부터 진행해라.
""")
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser(description="한국 시장 중복 검사기")
    p.add_argument("terms", nargs="+", help="검색 키워드 (여러 개 가능)")
    p.add_argument("--out", "-o", help="결과를 저장할 파일 (.md)")
    args = p.parse_args()

    report = build_report(args.terms)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"저장 완료: {args.out}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
