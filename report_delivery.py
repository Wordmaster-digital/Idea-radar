"""Prepare checked links, a concise message and a reusable PDF without model calls."""
import json
from pathlib import Path
import sys

import link_check
import pdf_report


def prepare(markdown, path, *, usage=None, checker=link_check.check, writer=pdf_report.write):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    urls = link_check.links(markdown)
    try:
        results = checker(urls)
    except Exception:
        results = {}
    results = {url: results.get(url, {"ok": False, "url": "", "status": "접속 확인 불가"}) for url in urls}
    # Preserve the original references for regeneration; do not rerun the model.
    bundle = {"markdown": markdown, "usage": usage or [], "links": results}
    path.with_suffix(".json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    checked = link_check.sanitize(markdown, results)
    path.write_text(checked + "\n", encoding="utf-8")
    summary = pdf_report.summarize(checked, usage=usage, link_results=results)
    try:
        attachment = writer(path.with_suffix(".pdf"), checked, usage=usage, link_results=results)
        if not Path(attachment).is_file():
            raise OSError("PDF output missing")
    except Exception as exc:
        print(f"PDF 생성 실패 ({type(exc).__name__}) — Markdown 파일로 대체합니다.", file=sys.stderr)
        attachment = path
        summary += "\n⚠ PDF 생성에 실패해 상세 Markdown 파일을 첨부했습니다."
    return {"summary": summary[:1900], "attachment": Path(attachment), "links": results}
