"""Rotating source scan plus unrestricted-domain discovery queries."""
from datetime import date
import json
from pathlib import Path
from urllib.parse import quote, urlsplit
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
import net

CATEGORIES = ["해커톤", "공모전", "대외활동", "인턴", "일경험", "신입·체험형 채용"]
REGIONS = ["서울 경기 인천", "부산 울산 경남", "대구 경북", "대전 세종 충청", "광주 전남 전북", "강원 제주", "전국 온라인"]
FIELDS = ["AI 개발 데이터", "디자인 콘텐츠 영상", "환경 사회문제 로컬", "창업 기획 마케팅", "문화 예술 글쓰기", "연구 과학 공학", "분야 제한 없는 청년 대학생"]
KEYWORDS = ("해커톤", "공모", "경진", "모집", "인턴", "일경험", "체험형", "현장실습", "서포터즈", "대외활동", "채용", "봉사", "hackathon")


def plan(today):
    offset = today.toordinal() % 7
    queries = [f'{today.year} {term} 모집 접수 마감' for term in CATEGORIES]
    queries += [f'{today.year} {REGIONS[offset]} 청년 대학생 참가자 모집',
                f'{today.year} {FIELDS[offset]} 공모전 해커톤 인턴 모집',
                f'{today.year} 대학 산학협력단 현장실습 교외 공모전 모집',
                f'{today.year} 재단 협회 연구원 참가자 서포터즈 모집',
                f'{today.year} 기업 채용 홈페이지 인턴 체험형 모집',
                f'{today.year} 공모전 참가자 모집 filetype:pdf']
    return {"queries": queries, "region": REGIONS[offset], "field": FIELDS[offset],
            "scope": "국내 전 분야 + 한국에서 참여 가능한 해외·온라인 기회"}


def collect(today, registry):
    seeds = json.loads(Path(__file__).with_name("sources.json").read_text(encoding="utf-8"))
    # Aggregators checked daily; institution scans rotate. New boards join the rotation.
    extra = [{"name": r["name"], "url": r["url"], "kind": "새로 발견"} for r in registry[-60:]]
    boards = seeds[:8] + [r for i, r in enumerate(seeds[8:] + extra) if i % 3 == today.toordinal() % 3][:14]
    fetched = net.pages([r["url"] for r in boards])
    candidates, coverage = [], []
    for board in boards:
        page = fetched[board["url"]]
        coverage.append({"source": board["name"], "url": board["url"], "ok": page["ok"], "reason": page["reason"]})
        for link in page["links"]:
            path = urlsplit(link["url"]).path
            if net.canonical(link["url"]) == net.canonical(board["url"]) or path.startswith('/list/'):
                continue
            if len(link["title"]) >= 8 and any(k in link["title"].lower() for k in KEYWORDS):
                candidates.append({"title": link["title"][:180], "url": link["url"], "source": board["name"]})
    search_plan = plan(today)

    def news(query):
        url = f'https://news.google.com/rss/search?q={quote(query + " when:14d")}&hl=ko&gl=KR&ceid=KR:ko'
        try:
            root = ET.fromstring(net.get(url)["body"])
            rows = [{"title": r.findtext("title", "")[:180], "url": r.findtext("link", ""),
                     "source": r.findtext("source", "뉴스 검색"), "published": r.findtext("pubDate", "")}
                    for r in root.findall("./channel/item")[:12]]
            return rows, {"source": "뉴스 보조 검색: " + query, "ok": True, "count": len(rows)}
        except Exception:
            return [], {"source": "뉴스 보조 검색: " + query, "ok": False, "reason": "피드 조회 실패"}

    with ThreadPoolExecutor(max_workers=5) as pool:
        for rows, status in pool.map(news, search_plan["queries"][:6]):
            candidates.extend(rows)
            coverage.append(status)
    unique = {net.canonical(r["url"]): r for r in candidates if r["url"].startswith(("https://", "http://"))}
    return list(unique.values())[:160], coverage, search_plan
