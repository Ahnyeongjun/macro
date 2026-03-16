"""Git 커밋 로그 수집 및 폴더별 포맷"""

import datetime
import re
import subprocess
from collections import Counter, OrderedDict

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

_COMMIT_SEP = "---COMMIT_SEP---"


def collect(repo_path: str, author: str, start: datetime.date, end: datetime.date):
    """저장소에서 특정 작성자의 커밋을 날짜 범위로 수집합니다.
    반환: [(date, message, primary_folder)]"""
    cmd = [
        "git", "log",
        f"--author={author}",
        f"--since={start.isoformat()}",
        f"--until={(end + datetime.timedelta(days=1)).isoformat()}",
        f"--format={_COMMIT_SEP}%ai|%s",
        "--name-only",
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
    for block in result.stdout.split(_COMMIT_SEP):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        header = lines[0]
        files = [l.strip() for l in lines[1:] if l.strip()]

        parts = header.split("|", 1)
        if len(parts) != 2:
            continue
        try:
            d = datetime.datetime.fromisoformat(parts[0].strip()).date()
        except ValueError:
            continue
        msg = parts[1].strip()

        folder = _primary_folder(files)
        commits.append((d, msg, folder))
    return commits


def _primary_folder(files: list[str]) -> str:
    """변경 파일 목록에서 가장 많이 등장하는 최상위 폴더를 반환합니다."""
    if not files:
        return "(기타)"
    dirs = []
    for f in files:
        first = f.split("/")[0] if "/" in f else f.rsplit(".", 1)[0] if "." in f else f
        dirs.append(first)
    counter = Counter(dirs)
    return counter.most_common(1)[0][0]


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
    """커밋을 기능별로 정리 (날짜 없이, 중복 제거). 하위 호환용."""
    if not commits:
        return "x"

    seen = set()
    lines = []
    for item in commits:
        raw_msg = item[1]
        msg = _clean(raw_msg)
        if _should_skip(msg):
            continue
        key = msg.lower()
        if key not in seen:
            seen.add(key)
            lines.append(f"- {msg}")

    return "\n".join(lines) if lines else "x"


def format_by_folder(commits):
    """커밋을 최상위 폴더별로 그룹핑하여 포맷합니다.

    출력 예:
      object-detection
      - class 값 통일
      - k8s 구조에 맞게 변경
      debezium
      - base64로 바이너리 저장되도록 수정
    """
    if not commits:
        return "x"

    groups = OrderedDict()
    for item in commits:
        raw_msg = item[1]
        folder = item[2] if len(item) > 2 else "(기타)"
        msg = _clean(raw_msg)
        if _should_skip(msg):
            continue
        if folder not in groups:
            groups[folder] = []
        groups[folder].append(msg)

    blocks = []
    for folder, messages in groups.items():
        seen = set()
        unique = []
        for m in messages:
            key = m.lower()
            if key not in seen:
                seen.add(key)
                unique.append(m)
        if unique:
            block = [folder]
            for m in unique:
                block.append(f"- {m}")
            blocks.append("\n".join(block))

    return "\n".join(blocks) if blocks else "x"


def format_oneline(commits):
    """커밋을 한 줄 요약으로 포맷합니다 (HRWeb 입력용).

    출력 예: class 값 통일, k8s 구조 변경, base64 저장 수정
    """
    if not commits:
        return ""

    seen = set()
    items = []
    for item in commits:
        msg = _clean(item[1])
        if _should_skip(msg):
            continue
        key = msg.lower()
        if key not in seen:
            seen.add(key)
            items.append(msg)

    return ", ".join(items) if items else ""
