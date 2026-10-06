"""Explainable career priorities over current and previously discovered notices."""
from collections import defaultdict
from datetime import date, timedelta
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
import state

FORMAT_VERSION = 2
WEIGHTS = {"career": 40, "language": 15, "region": 20, "experience": 15, "evidence": 10}
CAREER = {
    "글로벌마케팅·무역": ("해외마케팅", "글로벌마케팅", "해외 마케팅", "글로벌 마케팅", "해외영업", "해외 영업", "무역", "통상", "수출", "바이어", "해외시장", "해외 시장", "국제마케팅", "시장진출", "시장 진출", "trade", "export", "global marketing"),
    "마케팅·기획·브랜딩": ("마케팅", "시장조사", "시장 조사", "브랜딩", "브랜드", "광고", "홍보", "고객분석", "고객 분석", "상품기획", "상품 기획", "사업기획", "사업 기획", "서비스기획", "서비스 기획", "marketing"),
}
FIELD_TERMS = {
    **CAREER,
    "콘텐츠·디자인": ("콘텐츠", "디자인", "영상", "카피", "글쓰기", "문학", "문예", "사진"),
    "제품·창업·사업기획": ("창업", "사업성", "사업계획", "제품", "아이디어", "해커톤", "메이커톤"),
    "데이터·IT·개발": ("데이터", "인공지능", "ai", "개발", "코딩", "프로그래밍", "해커톤"),
    "공공·사회·지역": ("공공", "사회문제", "환경", "지역", "봉사", "청년정책"),
}
EXPERIENCE = {
    "제품 제작·검증": ("제품", "검증", "프로토타입", "해커톤", "메이커톤", "mvp", "고객", "사용자"),
    "광고·메시지 기획": ("광고", "홍보", "마케팅", "브랜드", "콘텐츠", "디자인", "카피"),
    "아이디어·사업성 검토": ("아이디어", "사업성", "사업계획", "창업", "기획", "시장조사", "시장 조사"),
}
REGION_ALIASES = {"세종": ("세종", "sejong"), "서울": ("서울", "seoul"), "경기": ("경기", "수원", "성남", "판교", "용인", "고양", "화성", "과천", "안양", "gyeonggi"), "인천": ("인천", "송도", "incheon"), "대전": ("대전", "daejeon"), "청주": ("청주", "cheongju"), "공주": ("공주",), "천안": ("천안", "cheonan"), "오송": ("오송",)}
OTHER_REGIONS = ("제주", "부산", "울산", "대구", "광주", "전남", "전북", "경남", "경북", "강원", "창원", "포항", "목포", "춘천", "강릉", "미국", "미주", "일본", "영국", "캐나다", "뉴욕", "busan", "jeju", "united states", "usa", "japan", "canada")


def load_profile(path=None):
    try:
        text = os.environ.get("OPPORTUNITY_PROFILE_JSON")
        profile = json.loads(text) if text else json.loads(Path(path or Path(__file__).with_name("career_profile.json")).read_text(encoding="utf-8"))
        for key in ("target_roles", "preferred_regions", "nearby_regions", "languages", "experience_topics"):
            if not isinstance(profile[key], list) or not all(isinstance(v, str) for v in profile[key]):
                raise ValueError()
        if not profile["target_roles"] or profile.get("version") != 1 or profile.get("weights") != WEIGHTS:
            raise ValueError()
        if not isinstance(profile.get("confirmed_requirements", {}), dict):
            raise ValueError()
        return profile
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError("직무 추천 설정 형식 오류. 개인 설정 내용은 로그에 표시하지 않습니다") from None


def hits(text, terms):
    text = str(text).casefold()
    result = []
    for term in terms:
        word = term.casefold()
        if re.fullmatch(r'[a-z ]+',word):
            match = re.search(r'(?<![a-z0-9])'+re.escape(word)+r'(?![a-z0-9])',text)
        else:
            match = word in text
        if match:
            result.append(term)
    return result


def evidence_text(row):
    # Full portal navigation and search snippets must not masquerade as job duties.
    title = row.get("title", "")
    if not row.get("verified"):
        return title
    return " ".join(str(row.get(k, "")) for k in ("title", "context", "summary", "evidence"))


def discover_priority(candidate, profile):
    text = candidate.get("title", "") + " " + candidate.get("description", "")
    return min(30, len(hits(text, CAREER["글로벌마케팅·무역"])) * 6 + len(hits(text, CAREER["마케팅·기획·브랜딩"])) * 3)


def organization(row):
    value = " ".join(row.get("organizer", "").split())
    if value in ("", "미상", "확인 필요"):
        return "주관기관 미확인 · " + (urlsplit(row["url"]).hostname or "출처 미확인").removeprefix("www.")
    if state.norm(value) in ("kotra", "대한무역투자진흥공사"):
        return "KOTRA(대한무역투자진흥공사)"
    return value[:100]


def field(row):
    text = evidence_text(row)
    return next((label for label, terms in FIELD_TERMS.items() if hits(text, terms)), "기타·분야 확인 필요")


def location(row, profile):
    raw = row.get("region", "")
    if not raw or raw == "확인 필요":
        return 4, "지역 확인 필요", False
    if hits(raw, ("온라인", "원격", "재택", "전국", "telecommute", "remote")):
        return 18, "전국·온라인 참여 가능 표시", False
    for place in profile["preferred_regions"] + profile["nearby_regions"]:
        if hits(raw, REGION_ALIASES.get(place, (place,))):
            return 20, "선호 지역 연결: " + place, False
    if hits(raw, ("수도권",)):
        return 20, "선호 지역 연결: 수도권", False
    if hits(raw, OTHER_REGIONS):
        return 0, "선호 지역 밖의 대면 활동", True
    return 4, "상세 위치 확인 필요", False


def evaluate(row, profile, today):
    text = evidence_text(row)
    title = row["title"]
    trade = hits(text, CAREER["글로벌마케팅·무역"])
    marketing = hits(text, CAREER["마케팅·기획·브랜딩"])
    title_trade = hits(title, CAREER["글로벌마케팅·무역"])
    title_marketing = hits(title, CAREER["마케팅·기획·브랜딩"])
    career = min(40, (22 if trade else 0) + (12 if marketing else 0) + (6 if title_trade or title_marketing else 0))
    reasons = []
    if trade:
        reasons.append("무역·해외마케팅 연결: " + ", ".join(trade[:2]))
    if marketing:
        reasons.append("마케팅 기획 연결: " + ", ".join(marketing[:2]))
    if "KOTRA" in profile["target_roles"][0].upper() and hits(row.get("organizer", "") + " " + title, ("KOTRA", "대한무역투자진흥공사")):
        career = min(40, career + 4)
        reasons.append("관심 기관 KOTRA 공고")
    language = 0
    if "영어" in profile["languages"] and hits(text, ("영어", "영문", "english", "영어권")):
        language = 15
        reasons.append("원문에 영어·영문 활용 단서")
    region, region_reason, outside = location(row, profile)
    if region >= 18:
        reasons.append(region_reason)
    connections = [topic for topic in profile["experience_topics"] if hits(text, EXPERIENCE.get(topic, (topic,)))]
    experience = min(15, len(connections) * 5)
    if connections:
        reasons.append("기존 활동 연결: " + " / ".join(connections[:2]))
    evidence = (5 if row.get("verified") else 0) + (3 if row.get("deadline_verified") else 0) + (2 if row.get("category") in ("인턴", "일경험", "채용") else 0)
    warnings = []
    if not row.get("deadline_verified"):
        warnings.append("접수 마감 재확인")
    if region < 18:
        warnings.append(region_reason)
    if row.get("eligibility") in (None, "", "확인 필요"):
        warnings.append("지원 자격 원문 확인")
    else:
        warnings.append("자격 조건과 본인 학년·학력·어학 점수 대조 필요")
    senior = re.search(r'(?:경력\s*(\d+)\s*년\s*(?:이상|이상자)|(?:minimum|at least)\s+(\d+)\s+years)', text + " " + row.get("eligibility", ""), re.I)
    senior_unconfirmed = bool(senior and int(senior[1] or senior[2]) >= 3 and not profile.get("confirmed_requirements", {}).get("experience_years"))
    if senior_unconfirmed:
        warnings.append("요구 경력 충족 여부 미확인")
    if not row.get("verified"):
        warnings.append("원문 확인 전 추천 순위 보류")
    eligible = bool(row.get("verified") and career >= 18 and not outside and not senior_unconfirmed)
    scores = {"career": career, "language": language, "region": region, "experience": experience, "evidence": evidence}
    return {**row, "fit_score": sum(scores.values()), "fit_breakdown": scores, "fit_reasons": reasons or ["희망 직무와의 직접 연결 단서가 부족합니다"],
            "fit_warnings": warnings, "top_eligible": eligible, "field": field(row), "institution": organization(row), "region_match": region_reason}


def pool(saved, updates, today):
    current = {}
    cutoff = (today - timedelta(days=30)).isoformat()
    for key, previous in saved["items"].items():
        row = previous["data"]
        last_seen = previous.get("last_seen", previous["first_sent"])
        if row.get("deadline") or last_seen >= cutoff:
            current[key] = {**row, "id": key, "known_since": previous["first_sent"]}
    for key, row in updates.items():
        current[key] = {**row, "id": key, "known_since": saved["items"].get(key, {}).get("first_sent", today.isoformat())}
    return [row for row in current.values() if row.get("status") not in ("closed", "expired") and (not row.get("deadline") or row["deadline"] >= today.isoformat())]


def refresh_candidates(saved, fresh, profile, today, limit=20):
    seen = {(state.net.canonical(r["url"]), state.edition_key(r)) for r in fresh}
    candidates = pool(saved, {}, today)
    candidates = [r for r in candidates if (state.net.canonical(r["url"]), state.edition_key(r)) not in seen]
    candidates.sort(key=lambda r: (-evaluate(r, profile, today)["fit_score"], r["id"]))
    return candidates[:limit]


def grouped(rows, key):
    buckets = defaultdict(list)
    for row in rows:
        buckets[row.get(key) or "확인 필요"].append(row)
    return [{"name": name, "items": buckets[name]} for name in sorted(buckets)]


def briefing(saved, updates, selected, profile, today):
    notices = {r["id"]: r["notice"] for r in selected}
    rows = [evaluate({**r, "notice": notices.get(r["id"], "누적 공고")}, profile, today) for r in pool(saved, updates, today)]
    rows.sort(key=lambda r: (-r["fit_score"], -r["fit_breakdown"]["career"], -r["fit_breakdown"]["language"], r.get("deadline") or "9999", r["id"]))
    previous = {r["id"]: r["rank"] for r in saved.get("recommendations", [])}
    top = []
    for row in rows:
        if not row["top_eligible"] or len(top) == 10:
            continue
        rank = len(top) + 1
        old = previous.get(row["id"])
        change = "신규 추천" if old is None else "순위 유지" if old == rank else ("순위 상승 " + str(old-rank) if old > rank else "순위 하락 " + str(rank-old))
        top.append({**row, "rank": rank, "rank_change": change})
    top_ids = {r["id"] for r in top}
    others = [r for r in rows if r["id"] not in top_ids]
    return {"format_version": FORMAT_VERSION, "profile": {k: profile[k] for k in ("target_roles", "preferred_regions", "nearby_regions", "languages", "experience_topics")},
            "weights": WEIGHTS, "top10": top, "remaining": others, "pool_count": len(rows), "new_count": len(selected),
            "by_field": grouped(others, "field"), "by_institution": grouped(others, "institution"), "by_type": grouped(others, "category"),
            "ranking_note": "직무·영어 활용·지역·기존 활동·원문 근거를 합산한 비교 우선순위입니다. 합격 확률이나 자격 충족 판정이 아닙니다. 원문 확인과 직무 연결 단서가 있는 공고만 최대 10개 추천하며, 부족하면 억지로 채우지 않습니다."}
