"""Git 커밋 로그 수집 및 기능별 포맷"""

import datetime
import re
import subprocess

COMMIT_PREFIX_RE = re.compile(
    r"^(fix|feat|chore|refactor|style|docs|test|perf|ci|build|init|revert)\s*[:(]\s*",
    re.IGNORECASE,
)
SKIP_PATTERNS = [
    re.compile(r"^\.+$"),
    re.compile(r"^임시", re.IGNORECASE),
    re.compile(r"^wip$", re.IGNORECASE),
    re.compile(r"^test$", re.IGNORECASE),
    re.compile(r"^커밋\s*테스트", re.IGNORECASE),
    re.compile(r"^Merge\s+(branch|commit|pull)", re.IGNORECASE),
]


def collect(repo_path: str, author: str, start: datetime.date, end: datetime.date):
    """저장소에서 특정 작성자의 커밋을 날짜 범위로 수집합니다."""
    cmd = [
        "git", "log",
        f"--author={author}",
        f"--since={start.isoformat()}",
        f"--until={(end + datetime.timedelta(days=1)).isoformat()}",
        "--format=%ai|%s",
        "--no-merges",
        "--all",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=repo_path, encoding="utf-8", timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"  경고: git log 실패 ({repo_path}): {e}")
        return []

    if result.returncode != 0:
        print(f"  경고: git log 실패 ({repo_path}): {result.stderr.strip()}")
        return []

    commits = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|", 1)
        if len(parts) == 2:
            try:
                d = datetime.datetime.fromisoformat(parts[0].strip()).date()
                commits.append((d, parts[1].strip()))
            except ValueError:
                continue
    return commits


def _clean(msg: str) -> str:
    msg = COMMIT_PREFIX_RE.sub("", msg).strip()
    if msg and msg[0] == ")":
        msg = msg[1:].strip()
    return msg


def _should_skip(msg: str) -> bool:
    if len(msg) <= 1:
        return True
    return any(p.search(msg) for p in SKIP_PATTERNS)


def format_by_feature(commits):
    """커밋을 기능별로 정리 (날짜 없이, 중복 제거)."""
    if not commits:
        return "x"

    seen = set()
    lines = []
    for _, raw_msg in commits:
        msg = _clean(raw_msg)
        if _should_skip(msg):
            continue
        key = msg.lower()
        if key not in seen:
            seen.add(key)
            lines.append(f"- {msg}")

    return "\n".join(lines) if lines else "x"
