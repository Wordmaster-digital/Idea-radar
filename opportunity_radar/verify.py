"""Separate fetched evidence from model interpretation; uncertain items stay visible."""
from datetime import date
import re
from urllib.parse import urlsplit
import net
from state import norm


def has_date(text, value):
    if not value:
        return False
    y, m, d = map(int, value.split("-"))
    return bool(re.search(rf'(?<!\d){y}\s*[./년-]\s*0?{m}\s*[./월-]\s*0?{d}(?!\d)', text))


def verify(rows):
    valid = []
    for row in rows:
        if not net.public_url(row["url"]):
            continue
        row = {**row, "url": net.canonical(row["url"])}
        valid.append(row)
    pages = net.pages([r["url"] for r in valid])
    for row in valid:
        page = pages[row["url"]]
        evidence = row.get("evidence", "")
        deadline_evidence = row.get("deadline_evidence", "")
        row["verified"] = bool(page["ok"] and len(norm(evidence)) >= 10 and norm(evidence) in norm(page["text"]))
        row["deadline_verified"] = bool(page["ok"] and deadline_evidence and
            norm(deadline_evidence) in norm(page["text"]) and has_date(deadline_evidence, row["deadline"]))
        row["verification"] = "원문 발췌 대조됨" if row["verified"] else page["reason"] + " · 모집 조건 재확인 필요"
        if row["deadline"] and not row["deadline_verified"]:
            row["verification"] += " · 마감일 재확인 필요"
    return valid


def new_boards(rows, registry):
    known = {r["url"] for r in registry}
    proposed = []
    for row in rows:
        url = row.get("board_url", "")
        if not row.get("verified") or not url or url in known or not net.public_url(url):
            continue
        # Never promote an unrelated domain supplied by web text.
        host = urlsplit(url).hostname.removeprefix("www.")
        origin = urlsplit(row["url"]).hostname.removeprefix("www.")
        if host != origin:
            continue
        proposed.append({"name": row["organizer"][:80], "url": net.canonical(url)})
        known.add(url)
    pages = net.pages([r["url"] for r in proposed[:8]])
    return [r for r in proposed[:8] if pages[r["url"]]["ok"]]
