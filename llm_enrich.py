#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm_enrich.py — 수집된 항목을 OpenAI로 보강한다.

daily_digest.py 가 OPENAI_API_KEY 환경변수를 발견하면 자동으로 사용한다.
키가 없으면 이 모듈은 건너뛰고 기존 영어 단어 매칭으로 동작한다.

파이프라인:
  1) translate_batch() — 영어 제목/설명 → 한국어 한 줄 요약 + 한국어 검색 키워드 + 분야
  2) 그 키워드로 한국 앱스토어 + 네이버 검색 (daily_digest 쪽 함수 사용)
  3) judge_batch()     — 검색 결과를 보고 "국내에 이미 있나" 판정 + 근거

국내 다이제스트와 같은 Responses API 클라이언트·모델·재시도 정책을 쓴다.
"""

import sys
import idea_ladder


def has_key():
    return idea_ladder.has_key()


def _call(system, payload, properties, *, require_all=True):
    item_schema = idea_ladder._object({"i": {"type": "integer"}, **properties})
    schema = idea_ladder._object({"rows": {"type": "array", "items": item_schema}})
    with idea_ladder.make_client() as client:
        data = idea_ladder.request_json(client, system, payload, schema, "low")
    return idea_ladder.valid_rows(data["rows"], len(payload), item_schema,
                                  require_all=require_all)


# ------------------------------------------------------------ 1단계: 번역

TRANSLATE_SYS = """너는 해외 제품·프로젝트 목록을 한국 시장 조사용으로 가공한다.

각 항목에 대해 아래를 만들어라.
- ko: 무엇인지 한국어 한 줄. 25자 이내. 명사로 끝내라. 홍보 문구가 아니라 기능 설명.
- kw: 한국에서 이 서비스를 찾을 때 실제로 검색할 한국어 키워드 2개.
      영어 제품명을 음차하지 마라. 기능을 한국어로 표현하라.
      예) "Fastpotify" → ["음악 스트리밍 앱", "스포티파이 클라이언트"]
- cat: 다음 중 하나 — 개발도구 / 생산성 / 소비자앱 / 하드웨어 / 디자인 / 헬스케어 / 금융 / 교육 / 기타

출력은 rows 배열을 가진 JSON 객체만. 설명이나 코드펜스를 붙이지 마라.
입력 순서와 출력 순서를 반드시 일치시켜라.
형식: {"rows": [{"i":0,"ko":"...","kw":["...","..."],"cat":"..."}, ...]}"""


def translate_batch(items):
    """items 각각에 ko / kw / cat 을 채운다. 실패해도 원본을 망가뜨리지 않는다."""
    if not items:
        return items
    payload = [{"i": n, "title": it["title"][:140], "desc": (it.get("extra") or "")[:160]}
               for n, it in enumerate(items)]
    try:
        rows = _call(TRANSLATE_SYS, payload, {
            "ko": idea_ladder.STR, "kw": idea_ladder.STR_LIST, "cat": idea_ladder.STR})
        for row in rows:
            n = row.get("i")
            if isinstance(n, int) and 0 <= n < len(items):
                items[n]["ko"] = (row.get("ko") or "").strip()
                items[n]["kw"] = [k for k in (row.get("kw") or []) if k][:2]
                items[n]["cat"] = (row.get("cat") or "기타").strip()
    except Exception as e:
        print(f"[LLM 번역] 실패: {e}", file=sys.stderr)
    return items


SHORTLIST_SYS = """너는 대학생 창업팀에게 오늘 목록에서 '실제로 만들어볼 만한 것'만 골라준다.

판단 틀 — 3단 계단:
  1단 계산·수집(원천 데이터/기술) → 2단 시각화·검색(정보) → 3단 의사결정(행동)
대부분의 기회는 1·2단은 이미 있는데 3단이 비어 있을 때 생긴다.
참고 사례: 그림자 계산(1단)과 그림자 지도(2단)는 이미 있었고,
"몇 시에 어디로 걸어라"라는 시간축을 더해 3단으로 올린 앱이 시장을 가져갔다.

고를 것:
- 소프트웨어나 데이터만으로 2~4주 안에 최소 버전을 만들 수 있는 것
- 공개 데이터나 이미 존재하는 무료 API로 되는 것
- 한국에서 같은 문제를 겪는 사람이 분명히 있는 것

절대 고르지 말 것:
- 하드웨어 제조, 신소재, 임상시험, 의료기기 인허가가 필요한 것
- 대규모 자본이나 영업 조직이 있어야 하는 것 (엔터프라이즈 인프라 등)
- 개발자만 쓰는 도구 (지불 의사가 없다)
- 아이디어가 아니라 그냥 뉴스인 것 (인수합병, 실적, 인사, 제품 출시 소식)

각 후보마다:
- what: 원본이 무엇인지 한국어 한 줄, 30자 이내
- axis: 3단으로 올리려면 어떤 축을 더해야 하는지. 한 문장. 구체적으로.
- who: 한국에서 돈을 낼 사람. "세입자" 같은 뭉뚱그린 답 금지. 구체적 집단.
- risk: 가장 큰 약점 한 줄. 솔직하게. 약점이 치명적이면 애초에 고르지 마라.

기준을 통과하는 게 없으면 {"rows": []}을 반환하라.
억지로 채우지 마라. 0개나 1개가 정상이다. 최대 3개.

출력은 rows 배열을 가진 JSON 객체만. 형식:
{"rows": [{"i":0,"what":"...","axis":"...","who":"...","risk":"..."}]}"""


def shortlist(items, limit=3):
    """오늘 목록에서 학생 팀이 디벨롭할 만한 것만 골라낸다. 없으면 빈 리스트."""
    if not items:
        return []
    payload = [{"i": n,
                "title": it["title"][:120],
                "what": it.get("ko") or (it.get("extra") or "")[:120],
                "cat": it.get("cat", ""),
                "verdict": it.get("verdict", "")}
               for n, it in enumerate(items)]
    try:
        rows = _call(SHORTLIST_SYS, payload,
                     {key: idea_ladder.STR for key in ("what", "axis", "who", "risk")},
                     require_all=False)
    except Exception as e:
        print(f"[LLM 후보선정] 실패: {e}", file=sys.stderr)
        return []

    out = []
    for row in rows[:limit]:
        n = row.get("i")
        if not isinstance(n, int) or not (0 <= n < len(items)):
            continue
        src = items[n]
        out.append({
            "title": src["title"],
            "url": src["url"],
            "what": (row.get("what") or "").strip(),
            "axis": (row.get("axis") or "").strip(),
            "who": (row.get("who") or "").strip(),
            "risk": (row.get("risk") or "").strip(),
        })
    return out


# ------------------------------------------------------------ 2단계: 판정

JUDGE_SYS = """너는 해외 아이디어가 한국에 이미 존재하는지 판정한다.

각 항목에는 한국어 검색 키워드로 조회한 한국 앱스토어와 네이버 결과가 붙어 있다.
그 근거만 보고 판정하라. 모르는 것을 지어내지 마라.

판정 기준:
- "있음": 같은 문제를 같은 방식으로 푸는 한국 서비스가 검색 결과에 명확히 보인다
- "유사": 인접하지만 핵심 기능이나 대상이 다른 것만 보인다
- "없음": 검색 결과에 대응하는 한국 서비스가 없다
- "불명": 근거가 부족해 판단할 수 없다

이름이 비슷하다는 이유로 "있음"을 주지 마라. 하는 일이 같아야 한다.
why 는 15자 이내 한국어. 근거가 된 서비스명이 있으면 그것을 써라.

출력은 rows 배열을 가진 JSON 객체만. 형식: {"rows": [{"i":0,"v":"없음","why":"..."}, ...]}"""


def judge_batch(items):
    """items 각각에 verdict / why 를 채운다. evidence 키가 있어야 한다."""
    if not items:
        return items
    payload = []
    for n, it in enumerate(items):
        payload.append({
            "i": n,
            "what": it.get("ko") or it["title"][:100],
            "kw": it.get("kw", []),
            "evidence": (it.get("evidence") or [])[:8],
        })
    try:
        rows = _call(JUDGE_SYS, payload, {
            "v": {"type": "string", "enum": ["있음", "유사", "없음", "불명"]},
            "why": idea_ladder.STR})
        for row in rows:
            n = row.get("i")
            if isinstance(n, int) and 0 <= n < len(items):
                items[n]["verdict"] = (row.get("v") or "불명").strip()
                items[n]["why"] = (row.get("why") or "").strip()
    except Exception as e:
        print(f"[LLM 판정] 실패: {e}", file=sys.stderr)
    return items
