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
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import idea_ladder
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
    """보도 매체 수 → 성과 수치 유무 → 차트 앱 → 차트 순위 → 최신 순."""
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
                           "app_id": group.get("app_id"), "published": group["published"]}
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
        item["published"] = max(item["published"], group["published"])
    return sorted(merged.values(), key=_rank_key)


def split_ideas(ideas):
    """GO·보류는 전체 형식으로 최대 3개, STOP은 한 줄로 최대 5개."""
    order = {"GO": 0, "보류": 1}
    full = sorted((i for i in ideas if i["verdict"] in order),
                  key=lambda i: order[i["verdict"]])
    stops = [i for i in ideas if i["verdict"] == "STOP"]
    return full[:MAX_FULL_IDEAS], stops[:MAX_STOP_IDEAS]


NOTE_NO_KEY = "⚠ ANTHROPIC_API_KEY가 없어 아이템 카드와 아이디어를 생략했습니다."
NOTE_CARD_FAIL = "⚠ 아이템 카드 생성에 실패해 아이디어를 생략했습니다."
NOTE_IDEA_FAIL = "⚠ 보완 아이디어 생성에 실패했습니다."


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


def send_discord(webhook, content, opener=urllib.request.urlopen):
    """멘션을 막고 링크 미리보기를 끈 채로 보낸다."""
    body = json.dumps({"content": content, "flags": 4,
                       "allowed_mentions": {"parse": []}}).encode("utf-8")
    request = urllib.request.Request(
        webhook, data=body,
        headers={"Content-Type": "application/json", "User-Agent": UA})
    with opener(request, timeout=25) as response:
        return response.status
