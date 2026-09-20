#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kr_digest.py — 국내 신규 아이템을 모아 그늘로식 보완 아이디어와 함께 디스코드로 보낸다.

실행:
    python kr_digest.py              # 디스코드로 발송
    python kr_digest.py --dry-run    # 화면에만 출력 (기록 저장 안 함)
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import idea_ladder
import idea_development
import development_report
import market_evidence
import link_check
import pdf_report
import report_delivery
import kr_sources
import seen_state
from daily_digest import UA, gather_evidence

KST = timezone(timedelta(hours=9))

MAX_NEWS = 150        # LLM 1에 넘길 뉴스 상한
MAX_SELECTED = 8      # 아이디어를 검토할 카드 수
MAX_FULL_IDEAS = 3    # 전체 형식으로 보낼 아이디어 수
MAX_STOP_IDEAS = 5    # 한 줄로 보낼 탈락 아이디어 수
MAX_LIST = 20         # 목록 섹션 줄 수
CHUNK_LIMIT = 1900    # 디스코드 메시지 길이 상한

# '코인' 단독은 넣지 않는다. 코인세탁·코인노래방 같은 업종 기사가 걸린다.
NOISE = re.compile(r"부고|별세|인사발령|주가|비트코인|가상자산|코인\s*시세|퀴즈|정답|예고|운세|로또")
TRACKERS = ("fbclid", "gclid", "igshid")


def canonical_url(url):
    """추적 파라미터와 조각을 뺀 주소. http(s)가 아니면 빈 문자열."""
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKERS]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path,
                       urlencode(query), ""))


def title_key(title):
    """같은 기사를 묶는 키. 괄호 머리말·공백·문장부호를 지운다."""
    text = re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", title or "")
    return re.sub(r"[\W_]+", "", text.lower())


def group_items(items, state):
    """중복·잡음·이미 보낸 항목을 걸러 묶음 목록으로 만든다."""
    groups = {}
    for item in items:
        url = canonical_url(item["url"])
        if not url or NOISE.search(item["title"]):
            continue
        if seen_state.is_seen(state, "urls", url):
            continue
        app_id = item.get("app_id")
        if app_id and seen_state.is_seen(state, "apps", app_id):
            continue
        key = ("app", app_id) if app_id else ("title", title_key(item["title"]))
        group = groups.get(key)
        if group is None:
            group = groups[key] = {**item, "url": url, "urls": [], "outlets": set()}
        if url not in group["urls"]:
            group["urls"].append(url)
        group["outlets"].add(item["outlet"])
        if item["source"] == "media" and group["source"] != "media":
            group.update(source="media", url=url,
                         desc=item.get("desc") or group.get("desc", ""))
        if item["published"] > group["published"]:
            group["published"] = item["published"]
    news = sorted((g for g in groups.values() if g["source"] != "appstore"),
                  key=lambda g: g["published"], reverse=True)[:MAX_NEWS]
    apps = [g for g in groups.values() if g["source"] == "appstore"]
    return news + apps


def _rank_key(card):
    """목록 표시 순서. 분석 후보는 이 순서로 자르지 않고 전부 비교한다."""
    return (-len(card["outlets"]), not card["traction"], card["chart_rank"] is None,
            card["chart_rank"] or 999, -card["published"].timestamp())


def merge_cards(groups, cards, state):
    """keep 카드를 서비스명으로 병합하고, 이미 보낸 이름을 뺀 뒤 정렬한다."""
    merged = {}
    for card in cards:
        if not card["keep"]:
            continue
        group = groups[card["i"]]
        key = seen_state.name_key(card["name"])
        if not key or seen_state.is_seen(state, "names", card["name"]):
            continue
        item = merged.get(key)
        if item is None:
            merged[key] = {**card, "url": group["url"], "urls": list(group["urls"]),
                           "outlets": set(group["outlets"]), "source": group["source"],
                           "chart_rank": group.get("chart_rank"),
                           "app_id": group.get("app_id"), "published": group["published"],
                           "headline": group["title"], "description": group.get("desc", ""),
                           "source_published": group["published"]}
            continue
        item["outlets"] |= group["outlets"]
        item["urls"] += [u for u in group["urls"] if u not in item["urls"]]
        if len(card["traction"]) > len(item["traction"]):
            item["traction"] = card["traction"]
        rank = group.get("chart_rank")
        if rank and (not item["chart_rank"] or rank < item["chart_rank"]):
            item["chart_rank"], item["app_id"] = rank, group.get("app_id")
        if group["source"] == "media" and item["source"] != "media":
            item["url"], item["source"] = group["url"], "media"
            item.update(headline=group["title"], description=group.get("desc", ""),
                        source_published=group["published"])
        item["published"] = max(item["published"], group["published"])
    return sorted(merged.values(), key=_rank_key)


def split_ideas(ideas):
    """GO·보류는 전체 형식으로 최대 3개, STOP은 한 줄로 최대 5개."""
    order = {"GO": 0, "보류": 1}
    full = sorted((i for i in ideas if i["verdict"] in order),
                  key=lambda i: order[i["verdict"]])
    stops = [i for i in ideas if i["verdict"] == "STOP"]
    return full[:MAX_FULL_IDEAS], stops[:MAX_STOP_IDEAS]


NOTE_NO_LLM = "⚠ Codex 분석이 비활성화되어 아이템 카드와 아이디어를 생략했습니다."
NOTE_CLIENT_FAIL = "⚠ ChatGPT 구독 로그인 확인에 실패해 아이템 카드와 아이디어를 생략했습니다."
NOTE_CARD_FAIL = "⚠ 아이템 카드 생성에 실패해 아이디어를 생략했습니다."
NOTE_IDEA_FAIL = "⚠ 아이디어 개발을 끝까지 완료하지 못했습니다. 완료된 분석과 아이템 목록을 표시합니다."


def _idea_block(n, idea, source):
    """아이디어 하나를 줄 목록으로 만든다. 들여쓰기는 전각 공백."""
    data = ", ".join(idea["data_sources"]) or "확인 필요"
    return [
        "",
        f"**{n}. {idea['title']}** — {idea['verdict']}",
        f"　원본: [{source['name']}]({source['url']}) · {source['stage']}단 · {source['what']}",
        f"　한 줄: {idea['one_liner']}",
        f"　추가할 축: {idea['axis']} → 정해주는 행동: {idea['decision']}",
        f"　불편 장면: {idea['pain_scene']}",
        f"　첫 사용자: {idea['first_users']} / 예상 밖: {idea['unexpected_users']}",
        f"　공개 데이터: {data}",
        f"　2주 MVP: {idea['mvp_2weeks']} · 채널: {idea['channel']}",
        f"　타이밍: {idea['timing']}",
        f"　지불자: {idea['payer']}",
        f"　기존 서비스가 못 하는 것: {idea['incumbent_gap']}",
        f"　함정: {idea['trap']}",
        f"　국내 중복: {idea['kr_duplicate']} — {idea['kr_duplicate_basis']}",
        f"　판정 이유: {idea['verdict_reason']}",
    ]


def render_ideas(date_str, full, stops, selected):
    lines = [f"## 🧗 {date_str} 보완 아이디어"]
    if not full and not stops:
        lines.append("기준을 통과한 아이디어가 없습니다.")
        return lines
    if not full:
        lines.append("오늘은 GO·보류 판정 아이디어가 없습니다.")
    for n, idea in enumerate(full, 1):
        lines += _idea_block(n, idea, selected[idea["i"]])
    if stops:
        lines += ["", "**❌ 탈락한 아이디어**"]
        lines += [f"· {x['title']} ← {selected[x['i']]['name']}: {x['verdict_reason']}"
                  for x in stops]
    return lines


def _overflow(lines, total):
    if total > MAX_LIST:
        lines.append(f"외 {total - MAX_LIST}건")
    return lines


def render_cards(hours, cards):
    lines = ["", f"## 🇰🇷 지난 {hours}시간 국내 신규 아이템 ({len(cards)}건)"]
    for card in cards[:MAX_LIST]:
        bits = [f"· [{card['name']}]({card['url']}) {card['what']}", f"{card['stage']}단"]
        outlets = [o for o in card["outlets"] if not o.startswith("앱스토어")]
        if outlets:
            bits.append(f"매체 {len(outlets)}곳")
        if card["traction"]:
            bits.append(card["traction"])
        if card["chart_rank"]:
            bits.append(f"앱스토어 #{card['chart_rank']}")
        lines.append(" · ".join(bits))
    return _overflow(lines, len(cards))


def render_raw(hours, groups, note):
    """LLM 없이 규칙만으로 거른 목록."""
    lines = [f"## 🇰🇷 지난 {hours}시간 국내 신규 아이템 ({len(groups)}건)", note]
    for group in groups[:MAX_LIST]:
        outlets = ", ".join(sorted(group["outlets"]))
        lines.append(f"· [{group['title']}]({group['url']}) · {outlets}")
    return _overflow(lines, len(groups))


def render_empty(date_str, hours):
    return [f"## 🇰🇷 {date_str} 국내 아이템 레이더",
            f"지난 {hours}시간 조건에 맞는 신규 아이템이 없습니다."]


def chunk_lines(lines, limit=CHUNK_LIMIT):
    """줄 단위로 모아 limit 이하 덩어리로 나눈다."""
    chunks, current = [], ""
    for line in lines:
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_discord(webhook, content, opener=urllib.request.urlopen, attachment=None):
    """멘션을 막고 링크 미리보기를 끈 채로 보낸다."""
    payload = {"content": content, "flags": 4, "allowed_mentions": {"parse": []}}
    content_type = "application/json"
    if attachment is None:
        body = json.dumps(payload).encode("utf-8")
    else:
        attachment = Path(attachment)
        data = attachment.read_bytes()
        if len(data) > 8_000_000:
            raise ValueError("Report attachment exceeds the 8 MB delivery limit")
        filename = "idea-radar" + attachment.suffix
        payload["attachments"] = [{"id": 0, "filename": filename}]
        boundary = "IdeaRadar" + uuid.uuid4().hex
        mime = "application/pdf" if attachment.suffix == ".pdf" else "text/markdown; charset=utf-8"
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n'.encode()
                + json.dumps(payload, ensure_ascii=False).encode("utf-8")
                + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
                + data + f"\r\n--{boundary}--\r\n".encode())
        content_type = "multipart/form-data; boundary=" + boundary
    parts = urlsplit(webhook)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "wait"]
    webhook = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query + [("wait", "true")]), ""))
    request = urllib.request.Request(
        webhook, data=body,
        headers={"Content-Type": content_type, "User-Agent": UA})
    with opener(request, timeout=25) as response:
        if response.status != 200:
            raise ValueError("Discord did not confirm message creation")
        message = json.loads(response.read().decode("utf-8"))
        if not isinstance(message, dict) or not isinstance(message.get("id"), str) or not message["id"]:
            raise ValueError("Discord message receipt missing")
        return message["id"]


def _record(groups, names):
    """이번 실행에서 처리한 URL·앱 ID와 병합 카드 이름을 모은다."""
    urls, apps = [], []
    for group in groups:
        for url in group["urls"]:
            if url not in urls:
                urls.append(url)
        app_id = group.get("app_id")
        if app_id and app_id not in apps:
            apps.append(app_id)
    return {"urls": urls, "apps": apps, "names": list(names)}


def build_report(items, state, *, hours, today, client, evidence_fn=gather_evidence,
                 no_client_note=NOTE_NO_LLM, research_fn=market_evidence.collect, full_report=None):
    """수집 항목으로 발송할 줄 목록과 기록할 키를 만든다.

    저하 모드(Codex 미설정·LLM 실패)에서는 새 키를 기록하지 않는다. 설정을 고친 뒤
    같은 날 다시 실행해도 같은 항목으로 아이디어를 만들 수 있게 하기 위해서다.
    """
    empty = {"urls": [], "apps": [], "names": []}
    groups = group_items(items, state)
    date_str = today.isoformat()
    if not groups:
        return render_empty(date_str, hours), empty
    if client is None:
        return render_raw(hours, groups, no_client_note), empty
    try:
        cards = idea_ladder.make_cards(client, groups)
    except idea_ladder.LadderError as e:
        print(f"[카드] 실패: {e}", file=sys.stderr)
        return render_raw(hours, groups, NOTE_CARD_FAIL), empty

    merged = merge_cards(groups, cards, state)
    development = idea_development.run(client, merged, date_str,
                                       research_fn=research_fn, evidence_fn=evidence_fn)
    lines = development_report.render(development, date_str)
    if not development["complete"]:
        lines.insert(0, NOTE_IDEA_FAIL)
    record = _record(groups, [card["name"] for card in merged]) if development["complete"] else empty
    if full_report is not None:
        full_report.extend(development_report.render(development, date_str, full=True) + render_cards(hours, merged))
    lines += render_cards(hours, merged)
    return lines, record


def parse_args(argv):
    parser = argparse.ArgumentParser(description="국내 신규 아이템 레이더와 보완 아이디어")
    parser.add_argument("--dry-run", action="store_true",
                        help="디스코드로 보내지 않고 화면에 출력한다 (기록 저장 안 함)")
    parser.add_argument("--no-llm", action="store_true", help="구독 사용량 없이 목록만 만든다")
    parser.add_argument("--hours", type=int, default=24, help="수집 기간(시간). 기본 24")
    parser.add_argument("--state", default=os.path.join("state", "seen.json"),
                        help="발송 기록 파일 경로")
    parser.add_argument("--report", help="전체 후보 비교와 개발 보고서를 저장할 Markdown 경로")
    args = parser.parse_args(argv)
    if args.hours < 1:
        parser.error("--hours는 1 이상이어야 합니다")
    return args


def main(argv=None, *, now=None, fetcher=kr_sources.fetch, client_factory=None,
         opener=urllib.request.urlopen, sleep=time.sleep, evidence_fn=gather_evidence,
         research_fn=market_evidence.collect, link_checker=link_check.check, pdf_writer=pdf_report.write):
    args = parse_args(argv)
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(KST).date()

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not args.dry_run and not webhook:
        print("DISCORD_WEBHOOK_URL 환경변수가 없습니다.", file=sys.stderr)
        return 1

    items, errors = kr_sources.collect(now, args.hours, fetcher)
    if len(errors) == kr_sources.SOURCE_COUNT:
        print("모든 소스 조회 실패 — 발송하지 않습니다.", file=sys.stderr)
        return 1
    print(f"수집 {len(items)}건, 실패한 소스 {len(errors)}개", file=sys.stderr)

    state = seen_state.load(args.state, today)
    client, no_client_note = None, NOTE_NO_LLM
    if not args.no_llm and idea_ladder.is_available():
        try:
            client = (client_factory or idea_ladder.make_client)()
        except idea_ladder.LadderError as e:
            print(f"[LLM] 초기화 실패: {e}", file=sys.stderr)
            no_client_note = NOTE_CLIENT_FAIL
    full_report = []
    lines, record = build_report(items, state, hours=args.hours, today=today,
                                 client=client, evidence_fn=evidence_fn,
                                 no_client_note=no_client_note, research_fn=research_fn, full_report=full_report)
    attachment = None
    if args.report:
        try:
            report_path = Path(args.report)
            status = "미리보기 — 발송하지 않음" if args.dry_run else "발송 전 분석 보고서 — 발송 성공 여부는 실행 로그 참고"
            bundle = report_delivery.prepare(status + "\n\n" + "\n".join(full_report or lines), report_path,
                                              usage=getattr(client, "usage", []), checker=link_checker, writer=pdf_writer)
            lines = bundle["summary"].splitlines()
            attachment = bundle["attachment"]
        except OSError:
            print("전체 보고서 저장 실패 — Discord 출력은 계속합니다.", file=sys.stderr)
    if attachment is None:
        markdown = "\n".join(lines)
        try:
            results = link_checker(link_check.links(markdown))
        except Exception:
            results = {}
        lines = link_check.sanitize(markdown, results).splitlines()
    chunks = chunk_lines(lines)

    if args.dry_run:
        print("\n\n".join(chunks))
        return 0

    try:
        for n, chunk in enumerate(chunks):
            if n:
                sleep(1)
            message_id = send_discord(webhook, chunk, opener, attachment=attachment if n == 0 else None)
            print(f"[Discord] message_id={message_id}", file=sys.stderr)
    except Exception as e:
        print(f"디스코드 발송 실패: {type(e).__name__}", file=sys.stderr)
        return 1

    for kind, keys in record.items():
        seen_state.mark(state, kind, keys, today)
    seen_state.save(args.state, state)
    print(f"발송 완료 ({len(chunks)} 메시지)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
