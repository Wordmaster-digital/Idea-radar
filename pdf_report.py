"""Deterministic Korean briefing and PDF from an already generated report."""
import os
from pathlib import Path
import re
from xml.sax.saxutils import escape, quoteattr

import link_check


def sections(markdown):
    out = {"intro": [], "comparison": [], "variants": [], "plans": [], "sources": []}
    section = "intro"
    for line in markdown.splitlines():
        if line.startswith("**1. 수요·트렌드"):
            section = "comparison"
        elif line.startswith("**2. 피벗·파생"):
            section = "variants"
        elif line.startswith("**3. 최종안"):
            section = "plans"
        elif line.startswith("**출처와"):
            section = "sources"
        elif line.startswith("## 🇰🇷") and out["comparison"]:
            break  # Repeated news list is already represented by comparison and sources.
        out[section].append(line)
    return out


def plans(markdown):
    result = []
    for line in sections(markdown)["plans"][1:]:
        if re.match(r"^\*\*\d+\. .+ — .+\*\*$", line):
            result.append({"title": re.sub(r"^\*\*\d+\. |\*\*$", "", line), "fields": {}})
        elif result and ": " in line:
            key, value = line.strip().split(": ", 1)
            result[-1]["fields"][key] = value
    return result


def plain(text):
    text = link_check.LINK.sub(lambda m: m[1], text)
    return text.replace("**", "").strip()


def short(text, limit=150):
    text = " ".join(plain(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def usage_totals(usage):
    return {key: sum(row.get(key, 0) for row in usage or [])
            for key in ("input_tokens", "output_tokens", "cached_input_tokens")}


def summarize(markdown, *, usage=None, link_results=None):
    lines = ["🧭 오늘의 아이디어 브리핑"]
    counts = re.findall(r"\*\*[123]\. .*?\((\d+)개\)\*\*", markdown)
    if len(counts) == 3:
        lines.append(f"후보 {counts[0]}개 비교 → 파생안 {counts[1]}개 → 상세 계획 {counts[2]}개")
    for i, plan in enumerate(plans(markdown)[:2], 1):
        fields = plan["fields"]
        lines += ["", f"{i}. {short(plan['title'], 80)}",
                  "• 핵심: " + short(fields.get("핵심 가설", "상세 PDF 참고")),
                  "• 첫 검증: " + short(fields.get("수요 검증 실험", "확인 필요")),
                  "• 통과 기준: " + short(fields.get("통과 기준(제안)", "확인 필요"))]
    if not plans(markdown):
        notices = [plain(line) for line in markdown.splitlines() if line.startswith("⚠")]
        lines += ["", short(notices[0] if notices else "심화할 안이 없습니다. 상세 보고서의 후보와 판단 이유를 확인하세요.")]
    if any("단계 실패" in line for line in markdown.splitlines()):
        lines.append("⚠ 일부 분석을 완료하지 못해 완료된 결과만 포함했습니다.")
    if link_results is not None:
        good = sum(bool(r.get("ok")) for r in link_results.values())
        lines.append(f"\n링크 접속 확인 {good}/{len(link_results)}개. 확인 불가 주소는 PDF에서 비활성화했습니다.")
    if usage:
        counts = usage_totals(usage)
        lines.append(f"분석 토큰 {counts['input_tokens'] + counts['output_tokens']:,} (PDF 변환·링크 점검은 추가 AI 호출 없음)")
    lines.append("점수·전망은 검증 전 가설이며, GO도 수요 검증 실험의 우선순위를 뜻합니다.")
    return "\n".join(lines)[:1850]


def font_paths():
    configured = os.environ.get("IDEA_REPORT_FONT")
    regular = Path(configured) if configured else Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf"
    if not regular.is_file():
        raise RuntimeError("Korean TTF font required: set IDEA_REPORT_FONT")
    bold = regular.with_name("malgunbd.ttf")
    return regular, bold if bold.is_file() else regular


def write(path, markdown, *, usage=None, link_results=None):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle

    regular, bold = font_paths()
    pdfmetrics.registerFont(TTFont("Radar", str(regular)))
    pdfmetrics.registerFont(TTFont("RadarBold", str(bold)))
    navy, blue, gray = colors.HexColor("#132D46"), colors.HexColor("#24718E"), colors.HexColor("#526170")
    base = dict(fontName="Radar", fontSize=9.2, leading=14.5, wordWrap="CJK", textColor=navy, alignment=TA_LEFT)
    styles = {
        "body": ParagraphStyle("body", **base, spaceAfter=5),
        "label": ParagraphStyle("label", **{**base, "fontName": "RadarBold", "fontSize": 8.6, "textColor": blue}),
        "title": ParagraphStyle("title", fontName="RadarBold", fontSize=26, leading=35, textColor=navy, spaceAfter=14),
        "h1": ParagraphStyle("h1", fontName="RadarBold", fontSize=17, leading=25, textColor=navy, spaceAfter=12, keepWithNext=True),
        "h2": ParagraphStyle("h2", fontName="RadarBold", fontSize=11, leading=17, textColor=blue, spaceBefore=10, spaceAfter=6, keepWithNext=True),
        "muted": ParagraphStyle("muted", **{**base, "fontSize": 8.2, "textColor": gray}, spaceAfter=5),
        "cell": ParagraphStyle("cell", **{**base, "fontSize": 8.1, "leading": 12.5}),
    }

    def html(text):
        text = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\ufe0f]", "", text).replace("**", "")
        pieces, pos = [], 0
        for match in link_check.LINK.finditer(text):
            pieces += [escape(text[pos:match.start()]),
                       f'<link href={quoteattr(match[2])} color="#24718E"><u>{escape(match[1])}</u></link>']
            pos = match.end()
        pieces.append(escape(text[pos:]))
        return "".join(pieces)

    def para(text, style="body"):
        return Paragraph(html(text), styles[style])

    def field(label, value):
        table = Table([[para(label, "label"), para(value)]], colWidths=[91, 420], hAlign="LEFT")
        table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 6),
                                   ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                                   ("TOPPADDING", (0, 0), (-1, -1), 5),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                                   ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EEF4F7")),
                                   ("LINEBELOW", (0, 0), (-1, -1), .35, colors.HexColor("#DCE5EB"))]))
        return table

    date = (re.search(r"\d{4}-\d{2}-\d{2}", markdown) or ["오늘"])[0]
    story = [para("IDEA RADAR", "muted"), para("오늘의 아이디어 브리핑", "title"),
             para(date + " · 수요와 트렌드에서 다음 실험까지", "muted"), Spacer(1, 12)]
    summary = summarize(markdown, usage=usage, link_results=link_results)
    for line in summary.splitlines()[1:]:
        if not line:
            story.append(Spacer(1, 6))
        elif re.match(r"^[12]\. ", line):
            story.append(para(line, "h2"))
        else:
            story.append(para(line.lstrip("• ")))
    story += [Spacer(1, 10), para("읽는 순서: 최종안 실행 계획 → 후보 비교 → 파생안 검토 → 출처", "muted")]
    parts = sections(markdown)

    def blocks(lines, start):
        rows = []
        for line in lines:
            if re.match(start, line):
                rows.append({"title": line, "fields": {}})
            elif rows and line.startswith("　") and ": " in line:
                key, value = line.strip().split(": ", 1)
                rows[-1]["fields"][key] = value
        return rows

    def comparison_table(key):
        if key == "comparison":
            headers = ["후보 · 점수", "수요층 · 불편", "트렌드 가설", "비교 판단"]
            values = [[short(row["title"], 95), short(row["fields"].get("수요층", ""), 90),
                       short(row["fields"].get("추세 가설", ""), 90), short(row["fields"].get("비교 판단", ""), 105)]
                      for row in blocks(parts[key], r"^\d+\. ")]
        else:
            headers = ["파생안 · 판정", "원본 · 수요층", "추가 축 → 행동", "재평가 · 우선 확인"]
            values = [[short(row["title"], 95), short(row["fields"].get("원본", ""), 85),
                       short(row["fields"].get("추가할 축", ""), 100),
                       short(row["fields"].get("재평가", ""), 100) + " / 확인: " + short(row["fields"].get("우선 확인", ""), 65)]
                      for row in blocks(parts[key], r"^· \[")]
        table = Table([[para(h, "label") for h in headers]] + [[para(v, "cell") for v in row] for row in values],
                      colWidths=[108, 121, 128, 154], repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5EFF4")),
                                   ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7FAFC")]),
                                   ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#DCE5EB")),
                                   ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
        return table
    order = [("plans", "01 / 최종안 실행 계획"), ("comparison", "02 / 후보 비교"),
             ("variants", "03 / 피벗·파생안 검토"), ("sources", "04 / 출처와 링크 점검")]
    for key, title in order:
        lines = parts[key]
        if not lines:
            continue
        story += ([Spacer(1, 18)] if key == "sources" else [PageBreak()]) + [para(title, "h1")]
        if key in ("comparison", "variants"):
            story += [para("비교하기 쉽도록 핵심 항목을 발췌했습니다. 최종안의 상세 실행·중단 기준은 앞부분에 있습니다.", "muted"), comparison_table(key)]
            continue
        if key == "sources":
            story.append(para("발송 직전 공개 주소의 HTTP 응답과 리디렉션을 점검했습니다. 로그인·차단 등으로 확인하지 못한 주소는 클릭 링크에서 제외했습니다. 접속 가능 여부는 이후 바뀔 수 있습니다.", "muted"))
        for line in lines[1:]:
            if not line.strip():
                story.append(Spacer(1, 4))
            elif line.startswith("**") or line.startswith("· [") or re.match(r"^\d+\. ", line):
                story.append(para(line, "h2"))
            elif line.startswith("　") and ": " in line:
                label, value = line.strip().split(": ", 1)
                story.append(field(label, value))
            else:
                story.append(para(line))
    if not any(parts[key] for key, _ in order):
        story += [PageBreak(), para("수집 목록", "h1")]
        story += [para(line) for line in markdown.splitlines() if line.strip()]

    def footer(canvas, doc):
        canvas.setStrokeColor(colors.HexColor("#DCE5EB"))
        canvas.line(42, 34, 553, 34)
        canvas.setFont("Radar", 8)
        canvas.setFillColor(gray)
        canvas.drawString(42, 21, "IDEA RADAR  |  " + date)
        canvas.drawRightString(553, 21, str(doc.page))

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=42, leftMargin=42,
                            topMargin=40, bottomMargin=46, title="Idea Radar - " + date, author="Idea Radar")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return path
