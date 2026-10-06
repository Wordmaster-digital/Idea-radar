"""Korean report and a single acknowledged Discord message, no extra AI."""
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
    rows = report["selected"]
    good = sum(r.get("ok", False) for r in report["coverage"])
    lines = [f'# 기회 레이더 · {report["date"]}', "",
             f'새 공고·변경·마감 알림 {len(rows)}건 | 출처 조회 {good}/{len(report["coverage"])} 성공',
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
    rows = report["selected"]
    lines = [f'🧭 기회 레이더 · {report["date"]}', f'새 공고·변경·마감 알림 {len(rows)}건']
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
              "heading": ParagraphStyle("heading", fontName="Opportunity", fontSize=13, leading=19, spaceBefore=12, spaceAfter=8, keepWithNext=True)}
    story = []
    for line in text.splitlines():
        if not line:
            continue
        style = "title" if line.startswith("# ") else "heading" if line.startswith("##") else "body"
        escaped = html.escape(re.sub(r'^#{1,3} ', '', line).replace("**", ""))
        # Hyperlinks are restricted to the already checked public http(s) URLs.
        if line.startswith('- 공고: '):
            url = html.escape(line[6:].strip(), quote=True)
            escaped = '공고: <link href="' + url + '">' + url + '</link>'
        story.append(Paragraph(escaped, styles[style]))
    SimpleDocTemplate(str(path), pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40).build(story)


def write_report(root, report):
    root.mkdir(parents=True, exist_ok=True)
    stem = datetime.now().strftime("%Y-%m-%d_%H%M%S")
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


def send(webhook, content, attachment):
    p = urlsplit(webhook)
    if p.scheme != "https" or p.username or p.password or p.port not in (None,443) or p.hostname not in ("discord.com", "discordapp.com") or not re.fullmatch(r'/api/webhooks/\d+/[\w-]+', p.path):
        raise RuntimeError("Discord 발송 주소 형식 오류")
    query = dict(parse_qsl(p.query))
    query["wait"] = "true"
    url = urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), ""))
    boundary = "radar-" + uuid.uuid4().hex
    payload = {"username": "기회 레이더", "content": content, "allowed_mentions": {"parse": []},
               "attachments": [{"id": 0, "filename": attachment.name}]}
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n').encode()
    body += json.dumps(payload, ensure_ascii=False).encode() + b"\r\n"
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="{attachment.name}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
    body += attachment.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    req = Request(url, data=body, headers={"Content-Type": "multipart/form-data; boundary=" + boundary}, method="POST")
    try:
        with urlopen(req, timeout=30) as response:
            result = json.load(response)
    except Exception:
        # No automatic retry on ambiguous failures: a timeout may already have delivered.
        raise RuntimeError("Discord 발송 실패 또는 수신 확인 불가. 로그 확인 후 재실행") from None
    message_id = result.get("id", "") if isinstance(result, dict) else ""
    if not str(message_id).isdigit():
        raise RuntimeError("Discord 메시지 ID를 받지 못했습니다")
    return str(message_id)
