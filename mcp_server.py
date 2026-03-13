"""주간업무보고 MCP 서버

Cursor/Claude Desktop에서 자연어로 주간업무보고를 생성할 수 있는 MCP 서버입니다.

실행:
    fastmcp run mcp_server.py
"""

import datetime
import json
import os
from pathlib import Path

from fastmcp import FastMCP

import calendar_client
import email_sender
import excel_writer
import git_collector

SCRIPT_DIR = Path(__file__).parent
DIST_DIR = SCRIPT_DIR / "dist"
WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

mcp = FastMCP(
    name="weekly-report",
    instructions=(
        "주간업무보고 자동화 도구입니다. "
        "Git 커밋 로그를 기능별로 수집하고, Google Calendar 출장 일정을 조회하며, "
        "엑셀 보고서를 생성하고, Gmail로 발송할 수 있습니다. "
        "차주 업무 목표를 추천할 때는 이번 주 커밋 내용과 최근 패턴을 분석하세요."
    ),
)


def _load_config():
    path = DIST_DIR / "config.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _week_info(date_str: str | None = None):
    target = datetime.date.fromisoformat(date_str) if date_str else datetime.date.today()
    monday = target - datetime.timedelta(days=target.weekday())
    friday = monday + datetime.timedelta(days=4)
    week_num = (monday.day - 1) // 7 + 1
    return monday, friday, week_num


def _excel_serial(d: datetime.date) -> int:
    return (d - datetime.date(1899, 12, 30)).days


def _get_cal_service():
    config = _load_config()
    gcal_cfg = config.get("google_calendar", {})
    if not gcal_cfg.get("enabled", False):
        return None, gcal_cfg
    return calendar_client.connect(DIST_DIR, gcal_cfg), gcal_cfg


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool
def list_commits(date: str | None = None, weeks: int = 1) -> str:
    """특정 주의 Git 커밋 로그를 카테고리별로 조회합니다.

    Args:
        date: 대상 날짜 (YYYY-MM-DD). 미입력 시 이번 주.
        weeks: 조회할 주 수 (기본 1, 최대 4). 과거 패턴 분석 시 늘려주세요.
    """
    config = _load_config()
    categories = config.get("categories", [])
    weeks = min(weeks, 4)

    results = []
    for w in range(weeks):
        offset_date = (datetime.date.fromisoformat(date) if date else datetime.date.today())
        offset_date -= datetime.timedelta(weeks=w)
        monday, friday, week_num = _week_info(offset_date.isoformat())

        week_label = f"{monday.year}년 {monday.month}월 {week_num}째주 ({monday} ~ {friday})"
        week_data = [f"## {week_label}"]

        for cat in categories:
            repos = cat.get("repos", [])
            if not repos:
                continue
            name = cat["name"]
            all_commits = []
            for repo in repos:
                if os.path.isdir(repo):
                    all_commits.extend(
                        git_collector.collect(repo, config["author"], monday, friday)
                    )
            formatted = git_collector.format_by_feature(all_commits)
            week_data.append(f"### {name}\n{formatted}")

        results.append("\n".join(week_data))

    return "\n\n---\n\n".join(results)


@mcp.tool
def get_trips(date: str | None = None) -> str:
    """Google Calendar에서 해당 주의 출장 일정을 조회합니다.

    Args:
        date: 대상 날짜 (YYYY-MM-DD). 미입력 시 이번 주.
    """
    service, gcal_cfg = _get_cal_service()
    if service is None:
        return "Google Calendar가 연결되어 있지 않습니다. dist/config.json에서 enabled=true로 설정하세요."

    monday, friday, _ = _week_info(date)
    cal_id = gcal_cfg.get("calendar_id", "primary")
    keyword = gcal_cfg.get("trip_keyword", "출장")
    events = calendar_client.fetch_trips(service, cal_id, monday, friday, keyword)
    return calendar_client.format_trips(events)


@mcp.tool
def create_calendar_event(
    title: str,
    start_date: str,
    end_date: str | None = None,
    description: str = "",
    location: str = "",
) -> str:
    """Google Calendar에 새 일정을 생성합니다.

    Args:
        title: 일정 제목 (예: "관평동 출장 - 서버 점검")
        start_date: 시작 날짜 (YYYY-MM-DD)
        end_date: 종료 날짜 (YYYY-MM-DD). 미입력 시 당일.
        description: 일정 설명 (선택)
        location: 장소 (선택)
    """
    service, gcal_cfg = _get_cal_service()
    if service is None:
        return "Google Calendar가 연결되어 있지 않습니다."

    cal_id = gcal_cfg.get("calendar_id", "primary")
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date) if end_date else None

    link = calendar_client.create_event(service, cal_id, title, start, end, description, location)
    if link:
        return f"일정이 생성되었습니다: {link}"
    return "일정 생성에 실패했습니다."


@mcp.tool
def generate_report(date: str | None = None) -> str:
    """주간업무보고 엑셀 파일을 생성합니다. 차주 목표는 비워둡니다.

    Args:
        date: 대상 날짜 (YYYY-MM-DD). 미입력 시 이번 주.
    """
    config = _load_config()
    monday, friday, week_num = _week_info(date)

    template_path = DIST_DIR / config.get("template", "template.xlsx")
    if not template_path.exists():
        return f"템플릿 파일 없음: {template_path}"

    cell_map = excel_writer.detect_category_cells(str(template_path))
    categories = excel_writer.resolve_cell_refs(config.get("categories", []), cell_map)

    service, gcal_cfg = _get_cal_service()
    cal_id = gcal_cfg.get("calendar_id", "primary")
    trip_kw = gcal_cfg.get("trip_keyword", "출장")

    updates = {"B1": _excel_serial(friday)}

    report_summary = []
    for cat in categories:
        name = cat["name"]
        repos = cat.get("repos", [])

        if repos:
            all_commits = []
            for repo in repos:
                if os.path.isdir(repo):
                    all_commits.extend(
                        git_collector.collect(repo, config["author"], monday, friday)
                    )
            content = git_collector.format_by_feature(all_commits)
        elif cat.get("use_calendar") and service:
            events = calendar_client.fetch_trips(service, cal_id, monday, friday, trip_kw)
            content = calendar_client.format_trips(events)
        elif "default_this_week" in cat:
            content = cat["default_this_week"]
        else:
            content = "x"

        updates[cat["this_week_cell"]] = content
        updates[cat["next_week_cell"]] = "x"
        if content != "x":
            report_summary.append(f"[{name}]\n{content}")

    filename = f"{monday.year}_{monday.month:02d}_{week_num}째주 {config['name']} 주간업무보고.xlsx"
    output_path = DIST_DIR / filename
    DIST_DIR.mkdir(exist_ok=True)

    excel_writer.update(str(template_path), str(output_path), updates)

    summary = "\n\n".join(report_summary)
    return (
        f"보고서 생성 완료: {filename}\n"
        f"경로: {output_path}\n\n"
        f"--- 금주 업무 내용 ---\n{summary}\n\n"
        f"--- 차주 업무 목표 ---\n(비어있음 - 추천을 요청하세요)"
    )


@mcp.tool
def generate_report_with_next_week(
    date: str | None = None,
    next_week_goals: str = "",
) -> str:
    """차주 업무 목표를 포함하여 주간업무보고 엑셀을 생성합니다.

    Args:
        date: 대상 날짜 (YYYY-MM-DD). 미입력 시 이번 주.
        next_week_goals: 차주 목표 JSON. {"카테고리명": "내용"} 형식.
            예: {"APISS": "- 객체탐지 모델 고도화\\n- API 테스트"}
    """
    config = _load_config()
    monday, friday, week_num = _week_info(date)

    template_path = DIST_DIR / config.get("template", "template.xlsx")
    if not template_path.exists():
        return f"템플릿 파일 없음: {template_path}"

    cell_map = excel_writer.detect_category_cells(str(template_path))
    categories = excel_writer.resolve_cell_refs(config.get("categories", []), cell_map)

    goals = json.loads(next_week_goals) if next_week_goals else {}

    service, gcal_cfg = _get_cal_service()
    cal_id = gcal_cfg.get("calendar_id", "primary")
    trip_kw = gcal_cfg.get("trip_keyword", "출장")
    next_monday = monday + datetime.timedelta(days=7)
    next_friday = friday + datetime.timedelta(days=7)

    updates = {"B1": _excel_serial(friday)}

    for cat in categories:
        name = cat["name"]
        repos = cat.get("repos", [])

        if repos:
            all_commits = []
            for repo in repos:
                if os.path.isdir(repo):
                    all_commits.extend(
                        git_collector.collect(repo, config["author"], monday, friday)
                    )
            content = git_collector.format_by_feature(all_commits)
        elif cat.get("use_calendar") and service:
            events = calendar_client.fetch_trips(service, cal_id, monday, friday, trip_kw)
            content = calendar_client.format_trips(events)
        elif "default_this_week" in cat:
            content = cat["default_this_week"]
        else:
            content = "x"
        updates[cat["this_week_cell"]] = content

        if name in goals:
            updates[cat["next_week_cell"]] = goals[name]
        elif cat.get("use_calendar") and service:
            events = calendar_client.fetch_trips(service, cal_id, next_monday, next_friday, trip_kw)
            updates[cat["next_week_cell"]] = calendar_client.format_trips(events)
        elif "default_next_week" in cat:
            updates[cat["next_week_cell"]] = cat["default_next_week"]
        else:
            updates[cat["next_week_cell"]] = "x"

    filename = f"{monday.year}_{monday.month:02d}_{week_num}째주 {config['name']} 주간업무보고.xlsx"
    output_path = DIST_DIR / filename
    DIST_DIR.mkdir(exist_ok=True)

    excel_writer.update(str(template_path), str(output_path), updates)

    return f"보고서 생성 완료 (차주 목표 포함): {filename}\n경로: {output_path}"


@mcp.tool
def send_report(date: str | None = None) -> str:
    """가장 최근 생성된 주간업무보고를 Gmail로 발송합니다.

    Args:
        date: 대상 날짜 (YYYY-MM-DD). 미입력 시 이번 주.
    """
    config = _load_config()
    monday, _, week_num = _week_info(date)

    filename = f"{monday.year}_{monday.month:02d}_{week_num}째주 {config['name']} 주간업무보고.xlsx"
    output_path = DIST_DIR / filename

    if not output_path.exists():
        return f"보고서 파일이 없습니다: {filename}\n먼저 generate_report로 생성해주세요."

    email_cfg = config.get("email", {})
    sender = email_cfg.get("sender", "")
    app_pw = email_cfg.get("app_password", "")
    recipients = email_cfg.get("recipients", [])

    if not sender or sender == "your.email@gmail.com":
        return "이메일이 설정되지 않았습니다. dist/config.json의 email 섹션을 확인하세요."
    if not recipients:
        return "수신자가 설정되지 않았습니다."

    subject = filename.replace(".xlsx", "")
    body = f"{config['name']} {monday.year}년 {monday.month}월 {week_num}째주 주간업무보고입니다."

    try:
        email_sender.send(sender, app_pw, recipients, subject, body, str(output_path))
        return f"메일 발송 완료!\n제목: {subject}\n수신자: {', '.join(recipients)}"
    except Exception as e:
        return f"메일 발송 실패: {e}"


# ---------------------------------------------------------------------------
# MCP Resources
# ---------------------------------------------------------------------------

@mcp.resource("config://report")
def get_config() -> str:
    """현재 주간업무보고 설정을 반환합니다."""
    config = _load_config()
    safe = {k: v for k, v in config.items() if k != "email"}
    safe["email"] = {"sender": config.get("email", {}).get("sender", ""), "recipients": config.get("email", {}).get("recipients", [])}
    return json.dumps(safe, ensure_ascii=False, indent=2)
