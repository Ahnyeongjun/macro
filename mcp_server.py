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
import hrweb_uploader

SCRIPT_DIR = Path(__file__).parent
DIST_DIR = SCRIPT_DIR / "dist"
WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

mcp = FastMCP(
    name="weekly-report",
    instructions=(
        "주간업무보고 자동화 도구입니다. "
        "Git 커밋 로그를 폴더별로 수집하고, Google Calendar 출장 일정을 조회하며, "
        "엑셀 보고서를 생성하고, Gmail로 발송할 수 있습니다. "
        "커밋 로그는 list_commits로 조회한 뒤, 당신이 직접 정리/요약하여 "
        "generate_report_with_content에 전달하면 깔끔한 보고서가 만들어집니다. "
        "차주 업무 목표를 추천할 때는 이번 주 커밋 내용과 최근 패턴을 분석하세요.\n\n"
        "## HRWeb 시간 입력 워크플로우\n"
        "1. preview_hrweb를 호출하여 커밋 현황을 확인합니다.\n"
        "2. 커밋이 없는 날(empty_dates)에 대해, 주변 커밋 패턴과 프로젝트를 분석하여 "
        "자연스러운 업무 설명을 생성합니다. "
        "예: 코드 리뷰, 설계 검토, 문서 작성, 테스트, 회의 등 개발자가 하는 일반적인 업무.\n"
        "3. upload_hrweb에 daily_entries JSON을 전달하여 실제 입력합니다.\n"
        "daily_entries 형식: {\"2026-03-02\": [{\"project\": \"프로젝트명\", "
        "\"description\": \"업무 내용\", \"minutes\": 480}]}"
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


def _collect_all(config, monday, friday):
    """모든 카테고리의 커밋을 폴더별로 수집하여 반환합니다."""
    categories = config.get("categories", [])
    result = {}
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
        result[name] = all_commits
    return result


def _build_report_data(config, categories, monday, friday, cal_service, gcal_cfg,
                       this_week_override=None, next_week_override=None):
    """보고서 데이터를 구성합니다. main.py 로직과 동일."""
    cal_id = gcal_cfg.get("calendar_id", "primary")
    trip_kw = gcal_cfg.get("trip_keyword", "출장")
    next_monday = monday + datetime.timedelta(days=7)
    next_friday = friday + datetime.timedelta(days=7)

    cat_names = [c["name"] for c in categories]
    this_week_trips = {}
    next_week_trips = {}
    unmatched_this = []
    unmatched_next = []

    if cal_service:
        raw_this = calendar_client.fetch_trips(cal_service, cal_id, monday, friday, trip_kw)
        raw_next = calendar_client.fetch_trips(cal_service, cal_id, next_monday, next_friday, trip_kw)
        cat_this = calendar_client.categorize_trips(raw_this, cat_names)
        cat_next = calendar_client.categorize_trips(raw_next, cat_names)
        for name in cat_names:
            this_week_trips[name] = cat_this.get(name, [])
            next_week_trips[name] = cat_next.get(name, [])
        unmatched_this = cat_this.get(None, [])
        unmatched_next = cat_next.get(None, [])

    cat_commits = _collect_all(config, monday, friday)
    this_override = this_week_override or {}
    next_override = next_week_override or {}

    updates = {"B1": _excel_serial(friday)}
    summary_parts = []

    for cat in categories:
        name = cat["name"]

        # --- 금주 ---
        if name in this_override:
            tw_content = this_override[name]
        else:
            parts = []
            if "default_this_week" in cat:
                parts.append(cat["default_this_week"])
            if name in cat_commits and cat_commits[name]:
                parts.append(git_collector.format_by_folder(cat_commits[name]))
            if this_week_trips.get(name):
                parts.append(calendar_client.format_trips_as_headers(this_week_trips[name]))
            if cat.get("use_calendar") and cal_service:
                all_events = this_week_trips.get(name, []) + unmatched_this
                if all_events:
                    parts.append(calendar_client.format_trips_as_headers(all_events))
            if parts:
                tw_content = "\n".join(p for p in parts if p and p != "x") or "x"
            else:
                tw_content = "x"
        updates[cat["this_week_cell"]] = tw_content

        # --- 차주 ---
        if name in next_override:
            nw_content = next_override[name]
        else:
            parts = []
            if "default_next_week" in cat:
                parts.append(cat["default_next_week"])
            if next_week_trips.get(name):
                parts.append(calendar_client.format_trips_as_headers(next_week_trips[name]))
            if cat.get("use_calendar") and cal_service:
                all_events = next_week_trips.get(name, []) + unmatched_next
                if all_events:
                    parts.append(calendar_client.format_trips_as_headers(all_events))
            if parts:
                nw_content = "\n".join(p for p in parts if p and p != "x") or "x"
            else:
                nw_content = "x"
        updates[cat["next_week_cell"]] = nw_content

        if tw_content != "x":
            summary_parts.append(f"[{name} - 금주]\n{tw_content}")
        if nw_content != "x":
            summary_parts.append(f"[{name} - 차주]\n{nw_content}")

    return updates, "\n\n".join(summary_parts)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool
def list_commits(date: str | None = None, weeks: int = 1) -> str:
    """특정 주의 Git 커밋 로그를 카테고리별, 폴더별로 조회합니다.
    AI가 이 결과를 읽고 정리/요약하여 generate_report_with_content에 전달하면
    깔끔한 보고서를 생성할 수 있습니다.

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
            formatted = git_collector.format_by_folder(all_commits)
            week_data.append(f"### {name}\n{formatted}")

        results.append("\n".join(week_data))

    return "\n\n---\n\n".join(results)


@mcp.tool
def get_trips(date: str | None = None) -> str:
    """Google Calendar에서 해당 주의 출장 일정을 조회합니다.
    카테고리 태그([APISS] 등)가 있으면 해당 카테고리로 분류됩니다.

    Args:
        date: 대상 날짜 (YYYY-MM-DD). 미입력 시 이번 주.
    """
    service, gcal_cfg = _get_cal_service()
    if service is None:
        return "Google Calendar가 연결되어 있지 않습니다. dist/config.json에서 enabled=true로 설정하세요."

    config = _load_config()
    monday, friday, _ = _week_info(date)
    cal_id = gcal_cfg.get("calendar_id", "primary")
    keyword = gcal_cfg.get("trip_keyword", "출장")
    events = calendar_client.fetch_trips(service, cal_id, monday, friday, keyword)

    cat_names = [c["name"] for c in config.get("categories", [])]
    categorized = calendar_client.categorize_trips(events, cat_names)

    lines = []
    for name in cat_names:
        if categorized.get(name):
            lines.append(f"### {name}")
            lines.append(calendar_client.format_trips_as_headers(categorized[name]))
    if categorized.get(None):
        lines.append("### 미분류")
        lines.append(calendar_client.format_trips_as_headers(categorized[None]))

    return "\n".join(lines) if lines else "출장 일정이 없습니다."


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
        title: 일정 제목 (예: "[APISS] 항우연 출장")
        start_date: 시작 날짜 (YYYY-MM-DD)
        end_date: 종료 날짜 (YYYY-MM-DD). 미입력 시 당일.
        description: 일정 설명 (선택). 줄바꿈으로 여러 항목 입력 가능.
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
    """주간업무보고 엑셀 파일을 자동 생성합니다.
    Git 커밋은 폴더별로 그룹핑되고, 캘린더 출장은 대카테고리로 표시됩니다.

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

    cal_service, gcal_cfg = _get_cal_service()
    updates, summary = _build_report_data(config, categories, monday, friday, cal_service, gcal_cfg)

    filename = f"{monday.year}_{monday.month:02d}_{week_num}째주 {config['name']} 주간업무보고.xlsx"
    output_path = DIST_DIR / filename
    DIST_DIR.mkdir(exist_ok=True)

    excel_writer.update(str(template_path), str(output_path), updates)

    return (
        f"보고서 생성 완료: {filename}\n"
        f"경로: {output_path}\n\n"
        f"{summary}\n\n"
        f"💡 커밋 내용을 AI가 정리하려면 list_commits로 조회 후 "
        f"generate_report_with_content를 사용하세요."
    )


@mcp.tool
def generate_report_with_content(
    date: str | None = None,
    this_week: str = "",
    next_week: str = "",
) -> str:
    """AI가 정리한 내용으로 주간업무보고 엑셀을 생성합니다.
    list_commits로 조회한 커밋을 AI가 요약한 뒤 이 도구에 전달하세요.

    Args:
        date: 대상 날짜 (YYYY-MM-DD). 미입력 시 이번 주.
        this_week: 금주 업무 JSON. {"카테고리명": "내용"} 형식.
            예: {"APISS": "object-detection\\n- 모델 클래스 값 통일\\n- k8s 배포 구조 변경"}
            지정하지 않은 카테고리는 자동 수집됩니다.
        next_week: 차주 목표 JSON. {"카테고리명": "내용"} 형식.
            예: {"APISS": "object-detection\\n- 모델 고도화\\n- API 테스트"}
            지정하지 않은 카테고리는 기본값/캘린더에서 가져옵니다.
    """
    config = _load_config()
    monday, friday, week_num = _week_info(date)

    template_path = DIST_DIR / config.get("template", "template.xlsx")
    if not template_path.exists():
        return f"템플릿 파일 없음: {template_path}"

    cell_map = excel_writer.detect_category_cells(str(template_path))
    categories = excel_writer.resolve_cell_refs(config.get("categories", []), cell_map)

    tw_override = json.loads(this_week) if this_week else None
    nw_override = json.loads(next_week) if next_week else None

    cal_service, gcal_cfg = _get_cal_service()
    updates, summary = _build_report_data(
        config, categories, monday, friday, cal_service, gcal_cfg,
        this_week_override=tw_override, next_week_override=nw_override,
    )

    filename = f"{monday.year}_{monday.month:02d}_{week_num}째주 {config['name']} 주간업무보고.xlsx"
    output_path = DIST_DIR / filename
    DIST_DIR.mkdir(exist_ok=True)

    excel_writer.update(str(template_path), str(output_path), updates)

    return f"보고서 생성 완료: {filename}\n경로: {output_path}\n\n{summary}"


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


@mcp.tool
def preview_hrweb(
    year: int | None = None,
    month: int | None = None,
) -> str:
    """HRWeb 시간 입력 미리보기. 커밋 있는 날과 없는 날을 구분하여 보여줍니다.
    AI는 이 결과를 보고 커밋 없는 날(empty_dates)의 업무 설명을 생성한 뒤
    upload_hrweb의 daily_entries에 전달하면 됩니다.

    Args:
        year: 대상 연도. 미입력 시 올해.
        month: 대상 월. 미입력 시 이번 달.
    """
    import datetime as dt

    config = _load_config()
    hrweb_config = config.get("hrweb", {})
    if not hrweb_config:
        return "오류: dist/config.json에 hrweb 설정이 없습니다."

    today = dt.date.today()
    y = year or today.year
    m = month or today.month

    weekdays = hrweb_uploader.get_weekdays(y, m)
    weekdays = [d for d in weekdays if d <= today]

    if not weekdays:
        return f"{y}년 {m}월에 입력할 평일이 없습니다."

    project_map = hrweb_config.get("project_map", {})
    default_project = hrweb_config.get("default_project", "공통(common)")
    default_minutes = hrweb_config.get("default_minutes_per_day", 480)

    lines = [f"## {y}년 {m}월 HRWeb 미리보기"]
    lines.append(f"프로젝트 매핑: {json.dumps(project_map, ensure_ascii=False)}")
    lines.append(f"기본 프로젝트: {default_project}")
    lines.append(f"기본 시간: {default_minutes}분/일\n")

    filled_dates = []
    empty_dates = []

    for d in weekdays:
        commits = hrweb_uploader.collect_daily_commits(config, d)
        day_str = d.isoformat()
        weekday = WEEKDAY_KR[d.weekday()]

        if commits:
            entries = hrweb_uploader.build_daily_entries(commits, hrweb_config)
            commit_count = sum(len(c) for c in commits.values())
            entry_parts = []
            for e in entries:
                entry_parts.append(
                    f"  - {e['project']}: {e['description']} ({e['minutes']}분)"
                )
            filled_dates.append(f"- **{day_str} ({weekday})** 커밋 {commit_count}개\n" + "\n".join(entry_parts))
        else:
            empty_dates.append(f"- {day_str} ({weekday})")

    lines.append(f"### 커밋 있는 날 ({len(filled_dates)}일) - 자동 입력")
    lines.extend(filled_dates)
    lines.append("")
    lines.append(f"### 커밋 없는 날 ({len(empty_dates)}일) - AI가 채워주세요")
    lines.extend(empty_dates)
    lines.append("")
    lines.append("💡 커밋 없는 날의 업무를 생성한 뒤 upload_hrweb의 daily_entries에 전달하세요.")
    lines.append(f"사용 가능한 프로젝트: {list(project_map.values())} + [{default_project}]")

    return "\n".join(lines)


@mcp.tool
def upload_hrweb(
    year: int | None = None,
    month: int | None = None,
    daily_entries: str = "",
    skip_existing: bool = True,
) -> str:
    """HRWeb에 시간 데이터를 자동 입력합니다.
    Playwright 브라우저를 열어 월~금 평일에 대해 입력합니다.

    워크플로우: preview_hrweb로 미리보기 → AI가 빈 날짜 채움 → 이 도구로 입력

    Args:
        year: 대상 연도. 미입력 시 올해.
        month: 대상 월. 미입력 시 이번 달.
        daily_entries: AI가 생성한 날짜별 입력 데이터 JSON.
            형식: {"2026-03-02": [{"project": "프로젝트명", "description": "업무 내용", "minutes": 480}]}
            지정하지 않은 날짜는 Git 커밋 기반으로 자동 생성됩니다.
        skip_existing: True면 이미 입력된 날은 건너뜁니다.
    """
    import datetime as dt

    config = _load_config()
    hrweb_config = config.get("hrweb", {})
    if not hrweb_config:
        return "오류: dist/config.json에 hrweb 설정이 없습니다."

    today = dt.date.today()
    y = year or today.year
    m = month or today.month

    weekdays = hrweb_uploader.get_weekdays(y, m)
    weekdays = [d for d in weekdays if d <= today]

    if not weekdays:
        return f"{y}년 {m}월에 입력할 평일이 없습니다."

    overrides = json.loads(daily_entries) if daily_entries else {}

    lines = [f"## {y}년 {m}월 HRWeb 시간 입력"]
    lines.append(f"대상: {len(weekdays)}일 (월~금)\n")

    daily_data = {}
    for d in weekdays:
        day_str = d.isoformat()
        if day_str in overrides:
            daily_data[d] = overrides[day_str]
        else:
            commits = hrweb_uploader.collect_daily_commits(config, d)
            daily_data[d] = hrweb_uploader.build_daily_entries(commits, hrweb_config)

        entry_parts = [f"{e['project'][:20]}({e['minutes']}분)" for e in daily_data[d]]
        src = "AI" if day_str in overrides else "Git"
        lines.append(f"- {d} ({WEEKDAY_KR[d.weekday()]}): [{src}] {' + '.join(entry_parts)}")

    url = hrweb_config["url"]
    uid = hrweb_config["user_id"]
    pw = hrweb_config["password"]

    with hrweb_uploader.HRWebUploader(url, uid, pw) as uploader:
        uploader.login()
        uploader.navigate_to_month(y, m)

        success = 0
        skipped = 0
        for d in weekdays:
            result = uploader.upload_day(
                d.day, daily_data[d], skip_existing=skip_existing
            )
            if result:
                success += 1
            else:
                skipped += 1

    lines.append(f"\n입력 완료: {success}일, 건너뜀: {skipped}일")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP Resources
# ---------------------------------------------------------------------------

@mcp.resource("config://report")
def get_config() -> str:
    """현재 주간업무보고 설정을 반환합니다."""
    config = _load_config()
    safe = {k: v for k, v in config.items() if k != "email"}
    safe["email"] = {
        "sender": config.get("email", {}).get("sender", ""),
        "recipients": config.get("email", {}).get("recipients", []),
    }
    return json.dumps(safe, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
