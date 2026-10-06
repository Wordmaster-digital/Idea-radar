"""Conservative event deduplication, delivery acknowledgement and reminder state."""
from datetime import date, timedelta
import hashlib
import json
import os
import re
import unicodedata
import net


def norm(text):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).casefold())


def identity(row):
    title = re.sub(r"(참가자|참여자|모집공고|모집안내|접수안내|모집)", "", row["title"])
    organizer = row.get("organizer", "")
    if organizer in ("", "확인 필요", "미상"):
        key = net.canonical(row["url"]) + "|" + edition_key(row)
        return hashlib.sha256(key.encode()).hexdigest()[:24]
    key = "|".join([norm(title), norm(organizer), edition_key(row)])
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def edition_key(row):
    text = row.get("edition", "")
    numbers = re.findall(r'\d+', text)
    if not numbers:
        numbers = re.findall(r'20\d{2}', row.get("title", ""))
    return ":".join(sorted(numbers)) if numbers else norm(text)


def load(path):
    if not path.exists():
        return {"version": 1, "items": {}, "sources": [], "last_delivery": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("items"), dict) or not isinstance(data.get("sources"), list):
            raise ValueError()
        for row in data['items'].values():
            if not isinstance(row, dict) or not isinstance(row.get('data'), dict) or not isinstance(row.get('alerts'), list):
                raise ValueError()
            if not all(isinstance(row['data'].get(k), str) for k in ('title', 'url', 'organizer', 'category', 'deadline')):
                raise ValueError()
            date.fromisoformat(row['first_sent'])
            if row['data']['deadline']:
                date.fromisoformat(row['data']['deadline'])
        if not all(isinstance(r,dict) and isinstance(r.get('name'),str) and isinstance(r.get('url'),str) for r in data['sources']):
            raise ValueError()
        receipts = data.get('deliveries', {})
        if not isinstance(receipts, dict):
            raise ValueError()
        for group, receipt in receipts.items():
            if group not in ('activities', 'career') or not isinstance(receipt, dict):
                raise ValueError()
            date.fromisoformat(receipt['date'])
            if not str(receipt.get('message_id', '')).isdigit() or receipt.get('version') != 3:
                raise ValueError()
        return data
    except (ValueError, OSError, AttributeError, KeyError, TypeError):
        raise RuntimeError("발송 기록이 손상되었습니다. 기록을 보존한 채 복구해야 합니다") from None


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def deduplicate(rows):
    result, urls = {}, set()
    for row in sorted(rows, key=lambda r: r.get("verified", False), reverse=True):
        key, url = identity(row), (net.canonical(row["url"]), edition_key(row))
        if url in urls or key in result:
            continue
        urls.add(url)
        result[key] = {**row, "id": key}
    return list(result.values())


def select(rows, saved_state, today):
    known_urls = {(net.canonical(r["data"]["url"]), edition_key(r["data"])): k for k, r in saved_state["items"].items()}
    updates, selected = {}, []
    for row in deduplicate(rows):
        key = known_urls.get((net.canonical(row["url"]), edition_key(row)), row["id"])
        row = {**row, "id": key}
        previous = saved_state["items"].get(key)
        updates[key] = row
        if row.get("status") in ("closed", "expired") or row.get("deadline_verified") and row["deadline"] < today.isoformat():
            continue
        if not previous:
            selected.append({**row, "notice": "새 공고"})
        elif row.get("deadline_verified") and row["deadline"] != previous["data"].get("deadline"):
            selected.append({**row, "notice": "마감일 변경"})
        if selected and selected[-1]["id"] == key and row.get("deadline_verified"):
            remaining = (date.fromisoformat(row["deadline"]) - today).days
            if remaining in (7, 3, 1):
                selected[-1]["alert"] = f'{row["deadline"]}:D-{remaining}'
    # Saved events remain eligible for reminders even if absent from today's searches.
    for key, previous in saved_state["items"].items():
        row = updates.get(key, previous["data"])
        if row.get("status") in ("closed", "expired"):
            continue
        if not row.get("deadline_verified") or not row.get("deadline"):
            continue
        remaining = (date.fromisoformat(row["deadline"]) - today).days
        if remaining not in (7, 3, 1):
            continue
        alert = f'{row["deadline"]}:D-{remaining}'
        if alert not in previous.get("alerts", []) and not any(r["id"] == key for r in selected):
            selected.append({**row, "notice": f"D-{remaining} 마감 알림", "alert": alert})
    selected.sort(key=lambda r: (not r.get("verified", False), r.get("deadline") or "9999", r["category"]))
    return selected, updates


def acknowledge(saved_state, selected, updates, today, message_id, new_sources):
    if not str(message_id).isdigit():
        raise RuntimeError("Discord 수신 확인이 없습니다")
    for key, row in updates.items():
        if key in saved_state["items"]:
            saved_state["items"][key]["data"] = row
            saved_state["items"][key]["last_seen"] = today.isoformat()
    for row in selected:
        saved = saved_state["items"].setdefault(row["id"], {"data": row, "first_sent": today.isoformat(), "alerts": []})
        saved.update(data={k: v for k, v in row.items() if k not in ("notice", "alert")}, last_sent=today.isoformat(), last_seen=today.isoformat())
        if row.get("alert") and row["alert"] not in saved["alerts"]:
            saved["alerts"].append(row["alert"])
    cutoff = (today - timedelta(days=240)).isoformat()
    saved_state["items"] = {k: v for k, v in saved_state["items"].items() if v["first_sent"] >= cutoff}
    known = {r["url"] for r in saved_state["sources"]}
    for row in new_sources:
        if row["url"] not in known:
            saved_state["sources"].append(row)
            known.add(row["url"])
    saved_state["sources"] = saved_state["sources"][-60:]
    saved_state["last_delivery"] = today.isoformat()
    saved_state["message_id"] = str(message_id)
