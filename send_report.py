"""Reformat an existing report; only --send delivers it. Never calls the model."""
import argparse
import json
from pathlib import Path

import kr_digest
import local_runner
import report_delivery


def main():
    parser = argparse.ArgumentParser(description="기존 분석을 PDF로 재정리 (추가 AI 호출 없음)")
    parser.add_argument("report", type=Path)
    parser.add_argument("--send", action="store_true", help="정리한 PDF를 Discord로 발송")
    args = parser.parse_args()
    local_runner.load_settings(local_runner.ROOT / ".env")
    saved = args.report.with_suffix(".json")
    if saved.exists():
        source = json.loads(saved.read_text(encoding="utf-8"))
    else:
        source = {"markdown": args.report.read_text(encoding="utf-8"), "usage": []}
    bundle = report_delivery.prepare(source["markdown"], args.report, usage=source.get("usage", []))
    print(bundle["summary"])
    print("REPORT:", bundle["attachment"])
    if args.send:
        import os
        webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
        if not webhook:
            raise SystemExit("Discord 발송 설정이 없습니다.")
        message_id = kr_digest.send_discord(webhook, bundle["summary"], attachment=bundle["attachment"])
        print("[Discord] message_id=" + message_id)


if __name__ == "__main__":
    main()
