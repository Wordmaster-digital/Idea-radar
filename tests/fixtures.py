"""tests/fixtures.py — 네트워크 없이 쓰는 합성 샘플과 항목 도우미.

실제 기사 문장을 옮기지 않고 전부 지어낸 내용이다.
"""
import json
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 9, 14, 23, 0, tzinfo=timezone.utc)

GNEWS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>구글 뉴스</title>
<item><title>동네 빨래방 예약 앱 '빨래톡' 등장 - 가상일보 - 가상일보</title>
<link>https://news.google.com/rss/articles/AAA?oc=5</link>
<pubDate>Mon, 14 Sep 2026 20:00:00 GMT</pubDate>
<source url="https://example.com">가상일보</source></item>
<item><title>사흘 전 기사 - 가상일보</title>
<link>https://news.google.com/rss/articles/BBB?oc=5</link>
<pubDate>Fri, 11 Sep 2026 20:00:00 GMT</pubDate>
<source url="https://example.com">가상일보</source></item>
</channel></rss>"""

LONG_DESC = "가상의 설명 문장입니다. " * 30

MEDIA_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>가상 매체</title>
<item><title>반려식물 돌봄 구독 '초록집' 출시</title>
<link>https://media.example/1?utm_source=feed</link>
<pubDate>Mon, 14 Sep 2026 10:00:00 +0900</pubDate>
<description><![CDATA[<p>{LONG_DESC}</p>]]></description></item>
<item><title>지난주 기사</title>
<link>https://media.example/2</link>
<pubDate>Mon, 07 Sep 2026 10:00:00 +0900</pubDate>
<description>짧은 설명</description></item>
</channel></rss>"""

APPSTORE_JSON = json.dumps({"feed": {"entry": [
    {"im:name": {"label": "빨래톡"},
     "im:releaseDate": {"label": "2026-08-01T00:00:00-07:00"},
     "id": {"label": "https://apps.apple.com/kr/app/id111", "attributes": {"im:id": "111"}},
     "summary": {"label": "동네 빨래방 예약 앱"}},
    {"im:name": {"label": "오래된앱"},
     "im:releaseDate": {"label": "2020-01-01T00:00:00-07:00"},
     "id": {"label": "https://apps.apple.com/kr/app/id222", "attributes": {"im:id": "222"}},
     "summary": {"label": "오래전에 나온 앱"}},
]}}, ensure_ascii=False)


def news_item(title, url, outlet="가상일보", source="gnews", hours_ago=1, desc=""):
    return {"source": source, "outlet": outlet, "title": title, "desc": desc, "url": url,
            "published": NOW - timedelta(hours=hours_ago), "chart_rank": None, "app_id": None}


def app_item(name="빨래톡", app_id="111", rank=3, days_ago=10):
    return {"source": "appstore", "outlet": f"앱스토어 무료 #{rank}", "title": name,
            "desc": "설명", "url": f"https://apps.apple.com/kr/app/id{app_id}",
            "published": NOW - timedelta(days=days_ago), "chart_rank": rank, "app_id": app_id}


def card(i, name, keep=True, traction="", stage=2, maker="스타트업"):
    return {"i": i, "keep": keep, "drop_reason": "", "name": name, "what": "하는 일 한 줄",
            "who": "대상 사용자", "maker": maker, "stage": stage, "stage_reason": "근거",
            "traction": traction, "kw": ["검색어1", "검색어2"]}


IDEA_TEMPLATE = {
    "title": "가칭", "one_liner": "한 줄", "axis": "축", "decision": "행동",
    "pain_scene": "장면", "first_users": "첫 사용자", "unexpected_users": "예상 밖",
    "data_sources": ["공공데이터 (확인 필요)"], "mvp_2weeks": "범위", "channel": "채널",
    "timing": "지금", "payer": "확인 필요", "incumbent_gap": "못 하는 것", "trap": "함정",
    "kr_duplicate": "불명", "kr_duplicate_basis": "근거 없음",
    "verdict": "GO", "verdict_reason": "이유",
}


def idea(i, verdict="GO", title="가칭"):
    return {**IDEA_TEMPLATE, "i": i, "verdict": verdict, "title": title}


def research(today, cards=()):
    return ([{"kind": kind, "title": f"합성 {kind} 신호", "url": f"https://example.com/{kind}",
              "date": today, "outlet": "가상일보", "scope": "테스트용 가상 자료"}
             for kind in ("demand", "trend")], [])


def merged_cards(count=4):
    return [{**card(i, f"소재{i}"), "url": f"https://example.com/{i}",
             "published": NOW, "outlets": {"가상일보"}, "description": "가상 소재 설명"}
            for i in range(count)]


class PipelineClient:
    """Network-free full responses, with optional stage mutation for failure tests."""
    def __init__(self, mutate=None, fail=None):
        self.calls, self.mutate, self.fail = [], mutate, fail

    def generate(self, system, payload, schema, effort):
        from idea_ladder import LadderError
        name = next(iter(schema["properties"]))
        self.calls.append({"name": name, "payload": payload, "effort": effort})
        if name == self.fail:
            raise LadderError("합성 실패")
        refs = ([s["id"] for s in payload.get("sources", []) if s["kind"] in ("demand", "trend")]
                if isinstance(payload, dict) else [])
        scores = dict(demand=3, trend=3, gap=3, feasibility=4, payment=2)
        if name == "cards":
            rows = [card(i, f"소재{i}") for i in range(len(payload))]
        elif name == "assessments":
            rows = [dict(i=c["i"], **scores, target_users="동네 통학생", pain="반복되는 우회 이동",
                         trend_hypothesis="폭염 단서 → 더운 날 증가 가설 → 보행 경로 수요 가설",
                         reason="다른 소재보다 직접 모집이 쉬움", unknowns=["실제 반복 사용 확인 필요"],
                         evidence_ids=refs) for c in payload["cards"]]
        elif name == "variants":
            rows = []
            for c in payload["shortlisted"]:
                for direction in ("수요층 전환", "상황·시간축", "의사결정 확장"):
                    i = len(rows)
                    rows.append(dict(i=i, parent_i=c["i"], direction=direction, title=f"파생안{i}",
                                     target_users=f"보행자 그룹{i}", problem="더운 이동 경로",
                                     added_axis=f"이동 제약{i}", decision=f"경로 선택{i}",
                                     difference="정보 표시에서 이동 선택으로", demand_case="불편 빈도 확인 필요",
                                     trend_case="폭염 영향 가설", payer="학교 시설팀 확인 필요",
                                     mvp_scope="수동 조사한 경로 세 개 비교", falsifier="불편이 반복되지 않으면 중단",
                                     assumptions=["지도 데이터 확인 필요"], evidence_ids=refs))
        elif name == "reviews":
            rows = [dict(i=v["i"], **scores, verdict="GO", reason="다른 안보다 실험이 작음",
                         strongest_risk="반복 사용 부족", counterargument="지도 앱으로 충분할 수 있음",
                         compared_with="상황별 대안과 비교해 수동 검증이 쉬움", required_check="주 2회 이상 불편한가",
                         evidence_ids=refs) for v in payload["variants"]]
        elif name == "plans":
            rows = [dict(i=v["i"], **{k: v[k] for k in ("title", "target_users", "added_axis", "decision")},
                         core_hypothesis="정보 → 이동 제약 → 경로 결정이 반복 사용으로 이어질 가설",
                         problem="더운 이동 경로", difference="선택 행동 지원", trend_link="폭염 변화 가설",
                         payer="학교 시설팀 확인 필요", week1="통학생 인터뷰와 세 경로 조사",
                         week2="수동 추천 화면 실험", first10_users="학교 게시판에서 통학생 10명 모집",
                         experiment="10명에게 세 경로를 보여주고 2주 동안 재사용 관찰",
                         success_metric="제안: 2주 내 10명 중 4명 재사용", kill_criterion="제안: 2주 내 재사용 2명 미만",
                         next_pivot="재사용 부족 시 보호자 동행 이동으로 대상 전환",
                         data_requirements=["지도 이용 조건 확인 필요; 동의받은 현장 수동 조사 대안"],
                         exclude_scope=["자동 길찾기"], risks=["위치정보 수집 최소화"],
                         unknowns=["지불 의사 확인 필요"], evidence_ids=refs) for v in payload["winners"]]
        else:
            raise AssertionError(name)
        if self.mutate:
            self.mutate(name, rows)
        return {name: rows}
