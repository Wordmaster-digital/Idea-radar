"""Human-readable comparison, alternatives, and concrete validation plans."""


def _text(value):
    return " ".join(str(value).split())


def _refs(row):
    return " · 근거 " + ", ".join(row["evidence_ids"]) if row["evidence_ids"] else " · 근거 미확보"


def _scores(row):
    return (f"{row['score']}/100 (수요 {row['demand']} · 추세 {row['trend']} · "
            f"차별 {row['gap']} · 구현 {row['feasibility']} · 지불 {row['payment']})")


def render(result, today, *, full=False):
    lines = [f"## 🧭 {today} 아이디어 개발 보고서",
             "점수는 검증 전 가설의 비교용입니다. GO는 수요 검증 실험 우선 진행을 뜻합니다."]
    lines += [f"⚠ {_text(warning)}" for warning in result["warnings"]]
    cards, sources = result["cards"], result["sources"]
    cited = set()

    def cite(row):
        cited.update(row["evidence_ids"])
        return _refs(row)

    lines += ["", f"**1. 수요·트렌드 후보 비교 ({len(result['comparisons'])}개)**"]
    comparisons = result["comparisons"] if full else result["comparisons"][:12]
    for rank, row in enumerate(comparisons, 1):
        selected = " → 파생안 검토" if row["i"] in result["selected"] else ""
        lines += [f"{rank}. {_text(cards[row['i']]['name'])} — {_scores(row)}{selected}",
                  f"　수요층: {_text(row['target_users'])} / 불편: {_text(row['pain'])}",
                  f"　추세 가설: {_text(row['trend_hypothesis'])}{cite(row)}",
                  f"　비교 판단: {_text(row['reason'])}"]
        if row["unknowns"]:
            lines.append("　미확인: " + " / ".join(map(_text, row["unknowns"])))
    if len(result["comparisons"]) > len(comparisons):
        lines.append(f"외 {len(result['comparisons']) - len(comparisons)}개도 비교했습니다. 전체 내용은 로컬 보고서에 보관합니다.")
    if not result["selected"] and result["complete"]:
        lines.append("수요·구현 가능성 기준을 통과한 후보가 없어 피벗을 억지로 만들지 않았습니다.")

    reviews = {row["i"]: row for row in result["reviews"]}
    lines += ["", f"**2. 피벗·파생안 비교 ({len(result['variants'])}개)**"]
    for variant in result["variants"]:
        review = reviews.get(variant["i"])
        status = review["verdict"] if review else "재검토 미완료"
        picked = " · 심화 대상" if variant["i"] in result["winners"] else ""
        lines += [f"· [{status}{picked}] {_text(variant['title'])} — {variant['direction']}",
                  f"　원본: {_text(cards[variant['parent_i']]['name'])} → 수요층: {_text(variant['target_users'])}",
                  f"　추가할 축: {_text(variant['added_axis'])} → 행동: {_text(variant['decision'])}",
                  f"　원본과 차이: {_text(variant['difference'])}{cite(variant)}"]
        if review:
            lines += [f"　재평가: {_scores(review)} · {_text(review['reason'])}{cite(review)}",
                      f"　다른 안과 비교: {_text(review['compared_with'])}",
                      f"　반론: {_text(review['counterargument'])}",
                      f"　핵심 위험: {_text(review['strongest_risk'])}",
                      f"　우선 확인: {_text(review['required_check'])}"]
        if full:
            lines += [f"　수요 가설: {_text(variant['demand_case'])} / 추세 가설: {_text(variant['trend_case'])}",
                      f"　지불자: {_text(variant['payer'])} / MVP: {_text(variant['mvp_scope'])}",
                      f"　반증 조건: {_text(variant['falsifier'])}",
                      "　전제: " + " / ".join(map(_text, variant["assumptions"]))]

    lines += ["", f"**3. 최종안 심화 ({len(result['plans'])}개)**"]
    if not result["plans"]:
        lines.append("심화를 완료한 안이 없습니다." if not result["complete"] else "통과한 대안이 없어 개발을 권하지 않습니다.")
    for rank, plan in enumerate(result["plans"], 1):
        variant = result["variants"][plan["i"]]
        card = cards[variant["parent_i"]]
        cited.add(f"C{variant['parent_i']}")
        lines += ["", f"**{rank}. {_text(plan['title'])} — {reviews[plan['i']]['verdict']}**",
                  f"　원본: [{_text(card['name'])}]({card['url']}) → {variant['direction']}",
                  f"　핵심 가설: {_text(plan['core_hypothesis'])}",
                  f"　수요층: {_text(plan['target_users'])} / 문제: {_text(plan['problem'])}",
                  f"　추가 축: {_text(plan['added_axis'])} → 결정할 행동: {_text(plan['decision'])}",
                  f"　차별점: {_text(plan['difference'])}",
                  f"　미래 추세와 연결: {_text(plan['trend_link'])}{cite(plan)}",
                  f"　지불자 가설: {_text(plan['payer'])}",
                  "　데이터 조건: " + " / ".join(map(_text, plan["data_requirements"])),
                  f"　1주차: {_text(plan['week1'])}", f"　2주차: {_text(plan['week2'])}",
                  "　이번에 제외: " + " / ".join(map(_text, plan["exclude_scope"])),
                  f"　첫 10명 모집: {_text(plan['first10_users'])}",
                  f"　수요 검증 실험: {_text(plan['experiment'])}",
                  f"　통과 기준(제안): {_text(plan['success_metric'])}",
                  f"　중단 기준(제안): {_text(plan['kill_criterion'])}",
                  f"　실패 시 다음 피벗: {_text(plan['next_pivot'])}",
                  "　위험: " + " / ".join(map(_text, plan["risks"])),
                  "　미확인: " + " / ".join(map(_text, plan["unknowns"]))]
    if cited:
        lines += ["", "**출처와 확인 범위**", "뉴스 검색은 제목 단서이며 본문·실제 수요를 검증한 것이 아닙니다."]
        for key in sorted(cited):
            source = sources[key]
            lines.append(f"· {key}: [{_text(source['title'])}]({source['url']}) — {source['date']} · {_text(source.get('outlet', ''))}")
    return lines
