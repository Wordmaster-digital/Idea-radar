"""Separate activity lists and career-ranked work opportunities with independent receipts."""
import ranking
import state

VERSION = 3
GROUPS = {
    'activities': {'해커톤', '공모전', '대외활동'},
    'career': {'인턴', '일경험', '채용'},
}
LABELS = {'activities': '공모전·대외활동', 'career': '인턴·일경험·채용'}


def belongs(row, group):
    return row.get('category') in GROUPS[group]


def delivered(saved, group, today):
    receipt = saved.get('deliveries', {}).get(group, {})
    return (receipt.get('date') == today.isoformat() and receipt.get('version') == VERSION
            and str(receipt.get('message_id', '')).isdigit())


def complete(saved, today):
    return all(delivered(saved, group, today) for group in GROUPS)


def scoped_state(saved, group):
    items = {key: value for key, value in saved['items'].items() if belongs(value['data'], group)}
    history = saved.get('deliveries', {}).get(group, {}).get('recommendations', saved.get('recommendations', []))
    return {**saved, 'items': items, 'recommendations': [r for r in history if r['id'] in items]}


def reports(common, saved, updates, selected, profile, today):
    result = []
    for group in GROUPS:
        scoped = scoped_state(saved, group)
        changed = {key: row for key, row in updates.items() if belongs(row, group)}
        notices = [row for row in selected if belongs(row, group)]
        current = ranking.pool(scoped, changed, today)
        report = {**common, 'delivery_group': group, 'label': LABELS[group],
                  'collection': common['all_discovered'],
                  'all_discovered': [r for r in common['all_discovered'] if belongs(r, group)],
                  'selected': notices, 'pool_count': len(current)}
        if group == 'career':
            report['briefing'] = ranking.briefing(scoped, changed, notices, profile, today)
        else:
            report.pop('briefing', None)
            # On the first split-format delivery, introduce the current activity list.
            # Subsequent days retain the original new/changed/deadline notice behavior.
            if saved.get('deliveries', {}).get(group, {}).get('version') != VERSION:
                known = {r['id'] for r in notices}
                report['selected'] = notices + [{**r, 'notice': '누적 공고·형식 전환'} for r in current if r['id'] not in known]
                report['format_transition'] = True
        result.append((group, report, changed))
    return result


def acknowledge(path, saved, report, updates, today, message_id, boards):
    group = report['delivery_group']
    if group not in GROUPS or any(not belongs(r, group) for r in report['selected']) or any(not belongs(r, group) for r in updates.values()):
        raise RuntimeError('기회 유형별 발송 목록이 섞였습니다')
    last_delivery, last_message = saved.get('last_delivery', ''), saved.get('message_id', '')
    state.acknowledge(saved, report['selected'], updates, today, message_id, boards)
    briefing = report.get('briefing', {})
    receipt = {'date': today.isoformat(), 'version': VERSION, 'message_id': str(message_id),
               'format': 'ranked' if group == 'career' else 'text',
               'pool_count': report['pool_count'], 'notice_count': len(report['selected']),
               'top_count': len(briefing.get('top10', [])), 'remaining_count': len(briefing.get('remaining', []))}
    if group == 'career':
        receipt['recommendations'] = [{'id': r['id'], 'rank': r['rank'], 'score': r['fit_score']} for r in briefing['top10']]
        saved['recommendations'] = receipt['recommendations']
    saved.setdefault('deliveries', {})[group] = receipt
    if complete(saved, today):
        saved['digest_version'] = VERSION
    else:
        # A partially delivered batch must never be mistaken for a complete day.
        saved['last_delivery'], saved['message_id'] = last_delivery, last_message
    state.save(path, saved)
