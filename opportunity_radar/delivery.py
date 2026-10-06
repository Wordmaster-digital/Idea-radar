"""Korean activity lists and ranked work reports, one message per group."""
from datetime import datetime
import html
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import uuid


def clean(text, limit=500):
    return " ".join(str(text).split()).replace("@", "＠")[:limit]


def markdown(report):
    if report.get('briefing'):
        return ranked_markdown(report)
    rows = report["selected"]
    good = sum(r.get("ok", False) for r in report["coverage"])
    label = report.get('label', '')
    count_label = '현재 공고·형식 전환 안내' if report.get('format_transition') else '새 공고·변경·마감 알림'
    lines = [f'# 기회 레이더{(" · " + label) if label else ""} · {report["date"]}', "",
             f'{count_label} {len(rows)}건 | 출처 조회 {good}/{len(report["coverage"])} 성공',
             f'오늘 추가 탐색: {report["plan"]["region"]} / {report["plan"]["field"]}', "",
             "검색 범위는 매일 순환합니다. 결과 없음·접속 실패는 기회가 없다는 뜻이 아닙니다.", ""]
    if report.get("degraded"):
        lines += ["**검색 축소 상태: " + clean(report["degraded"]) + "**", ""]
    for heading, group in (("원문과 발췌를 대조한 기회", [r for r in rows if r.get("verified")]),
                           ("조건을 추가로 확인할 발견 후보", [r for r in rows if not r.get("verified")])):
        lines += [f'## {heading} · {len(group)}건', ""]
        for row in group:
            lines += [f'### {clean(row["title"], 180)}', "",
                f'{row["notice"]} · {row["category"]} · {clean(row["organizer"], 100)}',
                f'- 내용: {clean(row["summary"])}',
                f'- 대상: {clean(row["eligibility"])}',
                f'- 지역·진행: {clean(row["region"])}',
                f'- 혜택·경험: {clean(row["benefit"])}',
                f'- 마감: {clean(row["deadline_text"] or "확인 필요")} ({"날짜 원문 대조" if row.get("deadline_verified") else "직접 재확인 필요"})',
                f'- 근거 상태: {clean(row.get("verification", "본문·모집 조건 재확인 필요"))}',
                f'- 공고: {row["url"]}', ""]
    if not rows:
        lines += ["오늘 확인한 범위에서 새로 알릴 공고나 마감 알림을 찾지 못했습니다.", ""]
    lines += ["## 수집 범위와 실패한 출처", ""]
    for source in report["coverage"]:
        lines.append(f'- {clean(source["source"])}: {"조회 성공" if source.get("ok") else clean(source.get("reason", "확인 불가"))}')
    for query in report.get("search_coverage", []):
        if isinstance(query, dict):
            lines.append(f'- 검색 {clean(query.get("query", ""))}: {clean(query.get("result", ""))}')
    lines += ["", f'새 수집 게시판 {len(report["new_sources"])}개 · 웹 검색 도구 완료 {len(report["searches"])}회',
              "첨부·이미지·로그인 페이지의 조건은 직접 확인해야 합니다. 상시·마감 불명확 공고에는 자동 마감 알림을 붙이지 않습니다.",
              "참여 전 원문의 자격·모집 상태·마감 시각을 확인하세요."]
    if report.get('cloud_stats'):
        stats = report['cloud_stats']
        lines += ['',f'서버 웹 검색: {stats["provider"]} · 검색 조회 {stats["successful_queries"]}/{len(stats["queries"])} · 발견 출처 도메인 {stats["discovered_domains"]}개',
                  '본문의 표시 조건은 원문에서 코드로 추출한 내용입니다. AI 해석·완전한 자격 검증을 수행한 결과는 아닙니다.']
    return "\n".join(lines)


def summary(report):
    if report.get('briefing'):
        return ranked_summary(report)
    rows = report["selected"]
    label = report.get('label', '')
    count_label = '현재 공고·형식 전환 안내' if report.get('format_transition') else '새 공고·변경·마감 알림'
    lines = [f'🧭 기회 레이더{(" · " + label) if label else ""} · {report["date"]}', f'{count_label} {len(rows)}건']
    if report.get("degraded"):
        lines.append("⚠ 웹 전체 탐색을 완료하지 못했습니다. 발견 후보와 수집 상태를 첨부합니다.")
    for row in rows[:7]:
        deadline = row["deadline"] if row.get("deadline_verified") else "마감 재확인"
        line = f'\n[{row["category"]} · {row["notice"]}] {clean(row["title"], 80)}\n{deadline} · {clean(row["region"], 40)}'
        if row.get("verified"):
            line += "\n" + row["url"]
        else:
            line += " · 조건 확인 필요"
        if len("\n".join(lines)) + len(line) > 1650:
            break
        lines.append(line)
    if not rows:
        lines.append("오늘 확인한 범위에서 새 알림을 찾지 못했습니다. 첨부에 조회 범위를 남겼습니다.")
    lines.append("\n전체 공고·지원 조건·수집 상태는 첨부 보고서에서 확인하세요.")
    if report.get('pdf_fallback'):
        lines.append('PDF 변환을 완료하지 못해 Markdown 원문을 첨부합니다.')
    return "\n".join(lines)[:1900]


def ranked_markdown(report):
    briefing = report['briefing']
    profile = briefing['profile']
    lines = [f'# 기회 레이더{(" · " + report["label"]) if report.get("label") else ""} · {report["date"]}', '',
             f'누적 유효 후보 {briefing["pool_count"]}건 · 오늘 새 공고/변경/마감 알림 {len(report["selected"])}건',
             '추천 직무: ' + clean(' / '.join(profile['target_roles'])),
             '우선 지역: ' + clean(' / '.join(profile['preferred_regions'] + profile['nearby_regions'])) + ' · 전국/온라인 포함',
             '연결할 활동: ' + clean(' / '.join(profile['experience_topics'])),
             '추천 기준: 직무 40 + 영어 활용 15 + 지역 20 + 기존 활동 15 + 원문 근거/실무 유형 10 = 100점',
             clean(briefing['ranking_note']), '', '## 먼저 검토할 TOP 10', '']
    if report.get('degraded'):
        lines += ['검색 축소 상태: ' + clean(report['degraded']), '']
    if not briefing['top10']:
        lines += ['현재 확인한 원문 중 추천 기준을 충족한 공고가 없습니다. 아래 분야별 후보에서 조건을 추가로 확인하세요.', '']
    for row in briefing['top10']:
        scores = row['fit_breakdown']
        closing = row['deadline'] if row.get('deadline_verified') else '마감 재확인'
        lines += [f'### {row["rank"]}위 · {clean(row["title"],180)}',
                  f'{row["fit_score"]}/100 · {row["rank_change"]} · {row["notice"]}',
                  f'{row["category"]} · {clean(row["institution"],100)} · {row["field"]}',
                  '추천 근거: ' + clean(' / '.join(row['fit_reasons']),500),
                  f'점수 내역: 직무 {scores["career"]}/40 · 영어 {scores["language"]}/15 · 지역 {scores["region"]}/20 · 경험 {scores["experience"]}/15 · 근거 {scores["evidence"]}/10',
                  '대상: ' + clean(row.get('eligibility','확인 필요'),180),
                  '지역·진행: ' + clean(row.get('region','확인 필요'),120) + ' · ' + row['region_match'],
                  '혜택·경험: ' + clean(row.get('benefit','확인 필요'),120),
                  '마감: ' + clean(closing) + ' · ' + clean(row.get('deadline_text',''),120),
                  '지원 전 확인: ' + clean(' / '.join(row['fit_warnings']),300),
                  '- 공고: ' + row['url'], '']
    lines += [f'## 그 외 후보 · 분야별 {len(briefing["remaining"])}건', '',
              '추천 10개에서 제외한 나머지를 한 번씩 정리합니다. 추천 보류가 지원 불가를 뜻하지는 않습니다.', '']
    for group in briefing['by_field']:
        lines += [f'### {group["name"]} · {len(group["items"])}건', '']
        for row in group['items']:
            closing = row['deadline'] if row.get('deadline_verified') else '마감 확인 필요'
            lines += [f'{clean(row["title"],160)} · 비교 점수 {row["fit_score"]}/100',
                      f'{row["category"]} · {clean(row["institution"],100)} · {row["notice"]}',
                      '지역: ' + clean(row.get('region','확인 필요'),80) + ' · 마감: ' + closing,
                      '대상·혜택: ' + clean(row.get('eligibility','확인 필요'),120) + ' / ' + clean(row.get('benefit','확인 필요'),100),
                      '확인할 점: ' + clean(' / '.join(row['fit_warnings']),180),
                      ('- 공고: ' if row.get('verified') else '출처 주소(본문 확인 필요): ') + row['url'], '']
    lines += ['## 그 외 후보 · 주관기관별 색인', '']
    for group in briefing['by_institution']:
        lines += [f'### {clean(group["name"],120)} · {len(group["items"])}건']
        for row in group['items']:
            lines.append('- ' + clean(row['title'],160) + ' · ' + row['field'] + ' · ' + row['category'])
    lines += ['', '## 그 외 후보 · 기회 유형별 색인', '']
    for group in briefing['by_type']:
        lines += [f'### {group["name"]} · {len(group["items"])}건']
        lines.extend('- ' + clean(row['title'],160) + ' · ' + clean(row['institution'],80) for row in group['items'])
    lines += ['', '## 오늘의 수집 범위와 확인 상태', '']
    for source in report['coverage']:
        lines.append('- ' + clean(source['source']) + ': ' + ('조회 성공' if source.get('ok') else clean(source.get('reason','확인 불가'))))
    stats = report.get('cloud_stats') or {}
    if stats:
        lines += ['',f'웹 검색 {stats["successful_queries"]}/{len(stats["queries"])}회 성공 · 발견 출처 도메인 {stats["discovered_domains"]}개 · {stats["provider"]}']
    lines += ['', '누적 공고 중 우선순위가 높은 이전 후보를 원문 재조회합니다. 접속 실패는 확인 필요로 낮추고, 확인된 마감 종료 공고는 현재 추천에서 제외합니다.',
              '조건과 직무 연결은 원문에서 코드로 추출한 단서입니다. AI 심사·합격 가능성 예측을 수행한 결과는 아닙니다.',
              '학년·학력·어학 점수·경력 연수는 확인된 개인 자료가 없어 자격 충족으로 판단하지 않습니다. 이미지·첨부·로그인 페이지의 조건은 직접 확인하세요.']
    return '\n'.join(lines)


def ranked_summary(report):
    briefing = report['briefing']
    lines = [f'🧭 기회 레이더{(" · " + report["label"]) if report.get("label") else ""} · {report["date"]}',
             f'직무 추천 TOP {len(briefing["top10"])} · 그 외 후보 {len(briefing["remaining"])}건',
             'KOTRA·무역·글로벌마케팅·마케팅 기획 / 영어 활용 / 세종 인근·수도권·경기 우선',
             f'누적 유효 후보 {briefing["pool_count"]}건 중 비교 · 오늘 새 공고/변경/마감 알림 {len(report["selected"])}건', '',
             '분야별 나머지: ' + ' / '.join(g['name'] + ' ' + str(len(g['items'])) + '건' for g in briefing['by_field']),
             '주관기관별·기회 유형별 목록과 상세 추천 근거는 첨부 보고서에 정리했습니다.',
             '점수는 비교 우선순위입니다. 지원 자격·마감 시각은 원문에서 확인하세요.']
    if report.get('degraded'):
        lines.append('⚠ 일부 웹 검색을 완료하지 못했습니다. 첨부에 조회 상태를 남겼습니다.')
    if report.get('pdf_fallback'):
        lines.append('PDF 변환을 완료하지 못해 Markdown 원문을 첨부합니다.')
    return '\n'.join(lines)[:1900]


def embeds(report):
    if not report.get('briefing'):
        return []
    briefing = report['briefing']
    fields = []
    for row in briefing['top10']:
        closing = row['deadline'] if row.get('deadline_verified') else '마감 재확인'
        value = f'{row["fit_score"]}/100 · {row["category"]} · {clean(row["institution"],55)}\n'
        value += clean(' / '.join(row['fit_reasons'][:3]),180) + '\n'
        value += clean(row.get('region','확인 필요'),45) + ' · ' + closing + '\n'
        value += clean(' / '.join(row['fit_warnings'][:2]),100)
        link = '\n[공고 원문](' + row['url'] + ')' if len(row['url']) <= 220 else '\n공고 원문은 첨부 보고서에서 확인'
        fields.append({'name':clean(f'{row["rank"]}위 · {row["title"]}',120), 'value':value[:420-len(link)]+link, 'inline':False})
    title = '내 직무와 연결되는 인턴·일경험 TOP 10' if report.get('delivery_group') == 'career' else '내 직무와 연결되는 TOP 10'
    return [{'title':title, 'description':f'누적 유효 후보 {briefing["pool_count"]}건에서 최대 10개 선정. 상세 근거·조건·나머지 분야별/기관별 목록은 PDF에 있습니다.',
             'color':0x2563EB, 'fields':fields,
             'footer':{'text':'직무 40 · 영어 15 · 지역 20 · 경험 15 · 원문/실무 10 | 합격 확률·자격 판정이 아닙니다'}}]


def pdf(path, text):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.pagesizes import A4
    font = Path(os.environ.get("OPPORTUNITY_REPORT_FONT", "C:/Windows/Fonts/malgun.ttf"))
    pdfmetrics.registerFont(TTFont("Opportunity", str(font)))
    styles = {"body": ParagraphStyle("body", fontName="Opportunity", fontSize=9, leading=14, wordWrap="CJK", spaceAfter=5),
              "title": ParagraphStyle("title", fontName="Opportunity", fontSize=22, leading=30, spaceAfter=16),
              "heading": ParagraphStyle("heading", fontName="Opportunity", fontSize=13, leading=19, spaceBefore=12, spaceAfter=8, keepWithNext=True),
              "subheading": ParagraphStyle("subheading", fontName="Opportunity", fontSize=11, leading=17, spaceBefore=10, spaceAfter=6, keepWithNext=True)}
    story = []
    for line in text.splitlines():
        if not line:
            continue
        style = "title" if line.startswith("# ") else "subheading" if line.startswith("### ") else "heading" if line.startswith("## ") else "body"
        escaped = html.escape(re.sub(r'^#{1,3} ', '', line).replace("**", ""))
        # Hyperlinks are restricted to the already checked public http(s) URLs.
        if line.startswith('- 공고: '):
            url = html.escape(line[6:].strip(), quote=True)
            escaped = '공고: <link href="' + url + '">' + url + '</link>'
        story.append(Paragraph(escaped, styles[style]))
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Opportunity',8)
        canvas.setFillColorRGB(0.4,0.4,0.4)
        canvas.drawString(40,24,'기회 레이더 · 공고와 지원 조건')
        canvas.drawRightString(A4[0]-40,24,str(doc.page))
        canvas.restoreState()
    SimpleDocTemplate(str(path), pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40).build(story,onFirstPage=footer,onLaterPages=footer)


def write_report(root, report):
    root.mkdir(parents=True, exist_ok=True)
    stem = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    if report.get('delivery_group'):
        stem += '_' + report['delivery_group']
    text = markdown(report)
    md = root / (stem + ".md")
    md.write_text(text, encoding="utf-8")
    raw = root / (stem + ".json")
    attachment = root / (stem + ".pdf")
    try:
        pdf(attachment, text)
    except Exception:
        attachment = md
        report["pdf_fallback"] = True
    raw.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return raw, attachment


def send(webhook, content, attachment, *, embeds=None):
    p = urlsplit(webhook)
    if p.scheme != "https" or p.username or p.password or p.port not in (None,443) or p.hostname not in ("discord.com", "discordapp.com") or not re.fullmatch(r'/api/webhooks/\d+/[\w-]+', p.path):
        raise RuntimeError("Discord 발송 주소 형식 오류")
    query = dict(parse_qsl(p.query))
    query["wait"] = "true"
    url = urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), ""))
    boundary = "radar-" + uuid.uuid4().hex
    payload = {"username": "기회 레이더", "content": content, "allowed_mentions": {"parse": []},
               "attachments": [{"id": 0, "filename": attachment.name}]}
    if embeds:
        count = sum(len(e.get('title',''))+len(e.get('description',''))+len(e.get('footer',{}).get('text',''))+
                    sum(len(f['name'])+len(f['value']) for f in e.get('fields',[])) for e in embeds)
        if len(embeds) > 10 or count > 6000:
            raise RuntimeError('Discord 추천 카드가 표시 제한을 초과했습니다')
        payload['embeds'] = embeds
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n').encode()
    body += json.dumps(payload, ensure_ascii=False).encode() + b"\r\n"
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="{attachment.name}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
    body += attachment.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    req = Request(url, data=body, headers={
        "Content-Type": "multipart/form-data; boundary=" + boundary,
        "User-Agent": "OpportunityRadar/1.0 (+https://github.com/Wordmaster-digital/Idea-radar)",
    }, method="POST")
    try:
        with urlopen(req, timeout=30) as response:
            result = json.load(response)
    except HTTPError as error:
        # Report safe status codes, never the credential-bearing URL or raw body.
        detail = ""
        try:
            payload = json.loads(error.read(4096))
            code = payload.get("code") if isinstance(payload, dict) else None
            if isinstance(code, int) and not isinstance(code, bool):
                detail = f" · Discord 오류 코드 {code}"
        except Exception:
            pass
        raise RuntimeError(f"Discord 발송 오류: HTTP {error.code}{detail}") from None
    except Exception:
        # No automatic retry on ambiguous failures: a timeout may already have delivered.
        raise RuntimeError("Discord 발송 실패 또는 수신 확인 불가. 로그 확인 후 재실행") from None
    message_id = result.get("id", "") if isinstance(result, dict) else ""
    if not str(message_id).isdigit():
        raise RuntimeError("Discord 메시지 ID를 받지 못했습니다")
    return str(message_id)
