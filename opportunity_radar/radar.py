"""Independent opportunity radar. KST dates; isolated delivery state and lock."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone, date
import json
import os
from pathlib import Path
import sys
import codex_client
import delivery
import discovery
import sources
import state
import verify
import cloud_discovery
import ranking
import streams

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
SETTINGS = {"DISCORD_WEBHOOK_URL", "OPPORTUNITY_MODEL", "OPPORTUNITY_CODEX_BIN", "OPPORTUNITY_REPORT_FONT", "OPPORTUNITY_LLM_MODE", "OPPORTUNITY_PROFILE_JSON", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"}


def settings(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or key not in SETTINGS:
            raise RuntimeError(".env에 지원하지 않는 설정 항목이 있습니다")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value:
            os.environ.setdefault(key, value)


@contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            acquire = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(handle, fcntl.LOCK_UN)
        try:
            acquire()
        except OSError:
            raise RuntimeError("기회 레이더가 이미 실행 중입니다") from None
        try:
            yield
        finally:
            handle.seek(0)
            release()


def fallback(candidates):
    rows = []
    for item in candidates[:50]:
        category = next((c for c in ("해커톤", "공모전", "대외활동", "인턴", "일경험", "채용") if c in item["title"]), "대외활동")
        row = {k: "" for k in discovery.FIELDS}
        row.update(title=item["title"], url=item["url"], category=category, organizer="확인 필요",
                   summary="수집한 제목입니다. 실제 모집 여부·지원 조건은 원문 확인이 필요합니다.",
                   eligibility="확인 필요", region="확인 필요", benefit="확인 필요", status="unknown")
        rows.append(row)
    return rows


def commit_delivery(path, saved, selected, updates, today, message_id, boards, briefing=None):
    # Called only after the single Discord message (including attachment) is acknowledged.
    state.acknowledge(saved, selected, updates, today, message_id, boards)
    saved['digest_version'] = ranking.FORMAT_VERSION
    if briefing:
        saved['recommendations'] = [{'id':r['id'],'rank':r['rank'],'score':r['fit_score']} for r in briefing['top10']]
    state.save(path, saved)


def run(args):
    today = datetime.now(KST).date()
    data_root = Path(args.data_dir).resolve() if args.data_dir else ROOT
    state_path = data_root / "state" / "seen.json"
    saved = state.load(state_path)
    if not args.dry_run and not args.force and streams.complete(saved, today):
        print("오늘의 기회 레이더는 이미 발송했습니다")
        return 0
    candidates, coverage, search_plan = sources.collect(today, saved["sources"])
    profile = ranking.load_profile()
    degraded, search_coverage, usage, searches, cloud_stats = "", [], [], [], {}
    if args.cloud:
        rows, web_coverage, cloud_stats = cloud_discovery.discover(today,candidates,search_plan,profile)
        coverage.extend(web_coverage)
        if not cloud_stats['web_search_ok']:
            degraded = '웹 검색 채널 조회 실패 · 직접 공지·뉴스 수집 범위만 포함'
    elif args.no_llm or os.environ.get("OPPORTUNITY_LLM_MODE") == "off":
        rows, degraded = fallback(candidates), "AI 웹 전체 탐색 비활성화; 제목 수집 후보만 표시"
    else:
        try:
            rows, search_coverage, usage, searches = discovery.discover(today, candidates, search_plan)
        except codex_client.CodexError as error:
            rows, degraded = fallback(candidates), str(error)
    if not args.cloud:
        rows = verify.verify(rows)
    # Refresh old reminder links. Failed rechecks disable automated reminders.
    keys = {state.identity(r) for r in rows}
    due = []
    for previous in saved["items"].values():
        row = previous["data"]
        if row.get("deadline_verified") and row.get("deadline") and state.identity(row) not in keys:
            days = (date.fromisoformat(row["deadline"]) - today).days
            if days in (7, 3, 1):
                due.append(row)
    if args.cloud:
        previous = ranking.refresh_candidates(saved,rows,profile,today)
        previous = state.deduplicate(due+previous)[:30]
        rows.extend(cloud_discovery.refresh(previous,today))
    else:
        rows.extend(verify.verify(due))
    selected, updates = state.select(rows, saved, today)
    boards = verify.new_boards(rows, saved["sources"])
    report = {"date": today.isoformat(), "selected": selected, "all_discovered": rows,
              "coverage": coverage, "plan": search_plan, "search_coverage": search_coverage,
              "usage": usage, "searches": searches, "new_sources": boards, "degraded": degraded,
              "cloud_stats": cloud_stats,
              "delivery": "미리보기 · 발송하지 않음" if args.dry_run else "발송 대기"}
    print(f'발견 {len(rows)}건 / 알림 {len(selected)}건 / 새 게시판 {len(boards)}개')
    if args.require_discovery and degraded:
        raise RuntimeError("전체 웹 검색을 완료하지 못했습니다: " + degraded)
    return send_groups(args, data_root, state_path, saved, report, updates, selected, profile, today, boards)


def send_groups(args, data_root, state_path, saved, report, updates, selected, profile, today, boards):
    failures = []
    for group, part, changed in streams.reports(report, saved, updates, selected, profile, today):
        already_sent = not args.dry_run and not args.force and streams.delivered(saved, group, today)
        if already_sent:
            part.update(delivery='오늘 이미 발송', message_id=saved['deliveries'][group]['message_id'])
        raw, attachment = delivery.write_report(data_root / 'reports', part)
        print(f'{streams.LABELS[group]} 보고서: {attachment}')
        if args.dry_run or already_sent:
            continue
        try:
            message_id = delivery.send(os.environ.get('DISCORD_WEBHOOK_URL', '').strip(),
                                       delivery.summary(part), attachment, embeds=delivery.embeds(part))
            streams.acknowledge(state_path, saved, part, changed, today, message_id, boards)
            part.update(delivery='Discord 수신 확인', message_id=message_id)
            print(f'Discord 발송 확인 [{group}]: {message_id}')
        except RuntimeError as error:
            part.update(delivery='발송 또는 수신 확인 실패', error=str(error))
            failures.append(streams.LABELS[group] + ': ' + str(error))
        raw.write_text(json.dumps(part, ensure_ascii=False, indent=2), encoding='utf-8')
    if failures:
        raise RuntimeError(' / '.join(failures))
    if args.dry_run:
        print('미리보기 완료: 발송·발송 기록 변경 없음')
    return 0


def send_saved(args):
    today = datetime.now(KST).date()
    data_root = Path(args.data_dir).resolve() if args.data_dir else ROOT
    state_path = data_root / 'state' / 'seen.json'
    saved = state.load(state_path)
    if not args.force and streams.complete(saved, today):
        print('오늘의 기회 레이더는 이미 발송했습니다')
        return 0
    report = json.loads(Path(args.send_report).read_text(encoding='utf-8'))
    if report.get('date') != today.isoformat() or report.get('degraded'):
        raise RuntimeError('오늘 전체 검색을 완료한 미리보기 보고서가 필요합니다')
    collected = report.get('collection', report['all_discovered'])
    rows = (cloud_discovery.refresh(collected,today)
            if report.get('cloud_stats') else verify.verify(collected))
    selected, updates = state.select(rows, saved, today)
    boards = verify.new_boards(rows, saved['sources'])
    for key in ('briefing','delivery_group','format_transition','label','pool_count','pdf_fallback'):
        report.pop(key, None)
    report.update(selected=selected, all_discovered=rows, new_sources=boards, delivery='발송 대기')
    return send_groups(args, data_root, state_path, saved, report, updates, selected, ranking.load_profile(), today, boards)


def main(argv=None):
    parser = argparse.ArgumentParser(description="기회 레이더")
    parser.add_argument("--check", action="store_true", help="설정·ChatGPT 로그인 확인; 검색·발송 없음")
    parser.add_argument("--dry-run", action="store_true", help="보고서 미리보기; 발송·기록 변경 없음")
    parser.add_argument("--no-llm", action="store_true", help="AI 사용 없이 제한된 제목 수집")
    parser.add_argument("--force", action="store_true", help="같은 날 추가 발송 허용; 공고 중복 제거 유지")
    parser.add_argument("--data-dir", help="보고서·발송 기록 폴더; 기본은 설치 폴더")
    parser.add_argument("--require-discovery", action="store_true", help="전체 검색 실패 시 오류로 종료")
    parser.add_argument("--send-report", help="오늘 만든 전체 검색 보고서를 AI 재호출 없이 발송")
    parser.add_argument("--cloud", action="store_true", help="GitHub 서버용 공개 웹 검색·원문 추출; PC·구독 로그인 불필요")
    args = parser.parse_args(argv)
    if args.dry_run and args.send_report:
        parser.error('--dry-run과 --send-report는 함께 사용할 수 없습니다')
    try:
        settings(ROOT / ".env")
        if not args.dry_run and not os.environ.get("DISCORD_WEBHOOK_URL", "").strip():
            raise RuntimeError("Discord 발송 설정이 필요합니다")
        if args.check:
            if args.cloud:
                print('준비 완료: Discord 설정 있음 / 서버 수집 모드 / 검색·발송 없음')
            else:
                codex_client.CodexClient()
                print("준비 완료: Discord 설정 있음 / ChatGPT 구독 로그인 확인 / 검색·발송 없음")
            return 0
        data_root = Path(args.data_dir).resolve() if args.data_dir else ROOT
        with lock(data_root / "state" / "run.lock"):
            return send_saved(args) if args.send_report else run(args)
    except (OSError, ValueError, RuntimeError) as error:
        message = str(error) if isinstance(error, (RuntimeError, codex_client.CodexError)) else type(error).__name__
        print("기회 레이더 실행 실패: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
