#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seen_state.py — 이미 발송한 URL·앱·서비스명을 기록해 다음 실행에서 거른다.

기록은 state/seen.json 한 파일이고 종류마다 보관 기간이 다르다.
GitHub Actions에서는 이 파일을 캐시로 실행 사이에 넘긴다.
"""

import json
import os
import re
import sys
from datetime import date, timedelta

TTL_DAYS = {"urls": 7, "names": 30, "apps": 120}
KINDS = tuple(TTL_DAYS)


def name_key(name):
    """서비스명 비교용 키. 소문자로 바꾸고 공백·문장부호를 지운다."""
    return re.sub(r"[\W_]+", "", (name or "").lower())


def empty_state():
    return {"version": 1, "urls": {}, "apps": {}, "names": {}}


def load(path, today=None):
    """기록을 읽고 만료분을 지운다. 파일이 없거나 깨졌으면 빈 기록."""
    today = today or date.today()
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return empty_state()
    except (OSError, ValueError) as e:
        print(f"[기록] 읽기 실패, 빈 기록으로 시작: {e}", file=sys.stderr)
        return empty_state()
    if not isinstance(raw, dict):
        print("[기록] 형식이 올바르지 않아 빈 기록으로 시작", file=sys.stderr)
        return empty_state()

    state = empty_state()
    for kind in KINDS:
        entries = raw.get(kind)
        if not isinstance(entries, dict):
            continue
        cutoff = today - timedelta(days=TTL_DAYS[kind])
        for key, seen_on in entries.items():
            try:
                if date.fromisoformat(seen_on) > cutoff:
                    state[kind][key] = seen_on
            except (TypeError, ValueError):
                continue
    return state


def is_seen(state, kind, key):
    if kind == "names":
        key = name_key(key)
    return bool(key) and key in state[kind]


def mark(state, kind, keys, today=None):
    stamp = (today or date.today()).isoformat()
    for key in keys:
        if kind == "names":
            key = name_key(key)
        if key:
            state[kind][key] = stamp


def save(path, state):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)
