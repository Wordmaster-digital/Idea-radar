"""Local scheduled entry point. Load literal settings and serialize state writes."""

from contextlib import contextmanager
from datetime import datetime
import os
from pathlib import Path
import sys

import idea_ladder
import kr_digest

ROOT = Path(__file__).resolve().parent
SETTINGS = {"DISCORD_WEBHOOK_URL", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET",
            "IDEA_MODEL", "IDEA_CODEX_BIN", "IDEA_LLM_MODE", "IDEA_REPORT_FONT"}


def load_settings(path):
    """Read KEY=value as literal text, never as PowerShell/Python code."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or key not in SETTINGS:
            raise ValueError(".env에 지원하지 않는 설정이 있습니다")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value:
            os.environ.setdefault(key, value)


@contextmanager
def run_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            lock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            lock = lambda: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            unlock = lambda: fcntl.flock(handle, fcntl.LOCK_UN)
        try:
            lock()
        except OSError:
            raise RuntimeError("Idea-radar가 이미 실행 중입니다") from None
        try:
            yield
        finally:
            handle.seek(0)
            unlock()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        load_settings(ROOT / ".env")
        if args == ["--check"]:
            if not os.environ.get("DISCORD_WEBHOOK_URL", "").strip():
                raise ValueError(".env에 DISCORD_WEBHOOK_URL을 설정해야 합니다")
            idea_ladder.make_client()
            print("준비 완료: Discord 설정 있음, ChatGPT 구독 로그인 확인됨 (발송·LLM 호출 없음)")
            return 0
        with run_lock(ROOT / "state" / "local.lock"):
            report = ROOT / "reports" / (datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".md")
            return kr_digest.main(["--state", str(ROOT / "state" / "seen.json"), "--report", str(report), *args])
    except (OSError, ValueError, RuntimeError) as error:
        # Do not expose OS paths/URLs from arbitrary exceptions.
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        print(f"로컬 실행 실패: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
