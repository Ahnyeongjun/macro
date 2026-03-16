"""HRWeb 시간 입력 자동화

Playwright를 사용하여 HRWeb(Blazor Server)에 Git 커밋 기반 시간 데이터를 자동 입력합니다.

사용법:
    python hrweb_uploader.py                        # 이번 달 월~금 입력
    python hrweb_uploader.py --year 2026 --month 3  # 특정 월
    python hrweb_uploader.py --dry-run               # 실제 입력 없이 미리보기
"""

import argparse
import calendar
import datetime
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, expect

import calendar_client
import git_collector

SCRIPT_DIR = Path(__file__).parent
DIST_DIR = SCRIPT_DIR / "dist"
WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def load_config():
    path = DIST_DIR / "config.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_weekdays(year: int, month: int) -> list[datetime.date]:
    """해당 월의 월~금 날짜 목록을 반환합니다."""
    cal = calendar.Calendar()
    return [
        d for d in cal.itermonthdates(year, month)
        if d.month == month and d.weekday() < 5
    ]


def collect_daily_commits(config, target_date: datetime.date) -> dict:
    """특정 날짜의 커밋을 카테고리별로 수집합니다."""
    categories = config.get("categories", [])
    result = {}
    for cat in categories:
        repos = cat.get("repos", [])
        if not repos:
            continue
        name = cat["name"]
        commits = []
        for repo in repos:
            if Path(repo).is_dir():
                day_commits = git_collector.collect(
                    repo, config["author"], target_date, target_date
                )
                commits.extend(day_commits)
        if commits:
            result[name] = commits
    return result


def fetch_month_events(config, year: int, month: int) -> dict:
    """Google Calendar에서 해당 월의 출장 일정을 날짜별로 수집합니다.
    반환: {date: "출장 요약"}"""
    gcal_cfg = config.get("google_calendar", {})
    if not gcal_cfg.get("enabled", False):
        return {}

    service = calendar_client.connect(DIST_DIR, gcal_cfg)
    if not service:
        return {}

    cal_id = gcal_cfg.get("calendar_id", "primary")
    keyword = gcal_cfg.get("trip_keyword", "출장")

    start = datetime.date(year, month, 1)
    if month == 12:
        end = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
    else:
        end = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)

    events = calendar_client.fetch_trips(service, cal_id, start, end, keyword)

    trip_map = {}
    for ev in events:
        d, summary = ev[0], ev[1]
        clean = summary.replace("출장", "").strip().strip("-").strip()
        trip_map[d] = f"출장({clean})" if clean else "출장"

    return trip_map


def collect_month_commits(config, year: int, month: int) -> dict:
    """월 전체의 커밋을 날짜별로 수집합니다.
    반환: {date: {category: [commits]}}"""
    weekdays = get_weekdays(year, month)
    today = datetime.date.today()
    weekdays = [d for d in weekdays if d <= today]

    monthly = {}
    for d in weekdays:
        daily = collect_daily_commits(config, d)
        monthly[d] = daily
    return monthly


def _infer_description(target_date: datetime.date, monthly: dict,
                       hrweb_config: dict) -> list[dict]:
    """커밋 없는 날에 주변 커밋 패턴을 분석하여 업무 설명을 생성합니다."""
    project_map = hrweb_config.get("project_map", {})
    default_project = hrweb_config.get("default_project", "공통(common)")
    default_minutes = hrweb_config.get("default_minutes_per_day", 480)

    # 같은 주의 커밋 수집 (월~금)
    weekday_idx = target_date.weekday()
    monday = target_date - datetime.timedelta(days=weekday_idx)
    week_commits = []
    week_categories = set()
    for offset in range(5):
        d = monday + datetime.timedelta(days=offset)
        if d in monthly and monthly[d]:
            for cat, commits in monthly[d].items():
                week_categories.add(cat)
                week_commits.extend(commits)

    # 같은 주에 커밋 없으면 월 전체에서 수집
    if not week_commits:
        for d, daily in monthly.items():
            for cat, commits in daily.items():
                week_categories.add(cat)
                week_commits.extend(commits)

    if not week_commits:
        return [{
            "project": default_project,
            "description": "업무 환경 구성 및 기술 검토",
            "minutes": default_minutes,
        }]

    # 주변 커밋에서 폴더/키워드를 추출하여 설명 생성
    folders = set()
    for commit in week_commits:
        if len(commit) > 2:
            folders.add(commit[2])

    # 요일별로 다른 톤의 설명
    filler_templates = {
        0: "설계 검토 및 개발 환경 구성",   # 월
        1: "코드 분석 및 구현",              # 화
        2: "기능 개발 및 단위 테스트",        # 수
        3: "코드 리뷰 및 이슈 대응",         # 목
        4: "테스트 및 배포 검증",             # 금
    }
    base_desc = filler_templates.get(target_date.weekday(), "개발 업무")

    # 폴더명이 있으면 구체적으로
    if folders:
        folder_list = ", ".join(sorted(folders)[:3])
        desc = f"{folder_list} {base_desc}"
    else:
        desc = base_desc

    # 프로젝트 결정: 해당 주에 활동한 카테고리 사용
    cat = next(iter(week_categories)) if week_categories else None
    project = project_map.get(cat, default_project) if cat else default_project

    return [{
        "project": project,
        "description": desc,
        "minutes": default_minutes,
    }]


def _dominant_category(monthly: dict) -> str | None:
    """월 전체에서 가장 많이 등장하는 카테고리를 반환합니다."""
    from collections import Counter
    cats = Counter()
    for daily in monthly.values():
        for cat, commits in daily.items():
            cats[cat] += len(commits)
    return cats.most_common(1)[0][0] if cats else None


def build_daily_entries(daily_commits: dict, hrweb_config: dict,
                        target_date: datetime.date = None,
                        monthly: dict = None,
                        trip_info: str = None) -> list[dict]:
    """커밋 데이터를 HRWeb 입력 항목으로 변환합니다.

    우선순위: 출장 > 커밋 > 추론
    """
    project_map = hrweb_config.get("project_map", {})
    default_project = hrweb_config.get("default_project", "공통(common)")
    default_minutes = hrweb_config.get("default_minutes_per_day", 480)

    # 출장 날이면 출장 내용 사용
    if trip_info:
        cat = _dominant_category(monthly) if monthly else None
        project = project_map.get(cat, default_project) if cat else default_project
        return [{
            "project": project,
            "description": trip_info,
            "minutes": default_minutes,
        }]

    entries = []

    if daily_commits:
        project_count = len(daily_commits)
        minutes_each = default_minutes // project_count
        remainder = default_minutes % project_count

        for i, (cat_name, commits) in enumerate(daily_commits.items()):
            project_name = project_map.get(cat_name, default_project)
            desc = git_collector.format_oneline(commits)
            if not desc:
                desc = f"{cat_name} 관련 작업"

            mins = minutes_each + (remainder if i == 0 else 0)
            entries.append({
                "project": project_name,
                "description": desc,
                "minutes": mins,
            })
    elif target_date and monthly:
        entries = _infer_description(target_date, monthly, hrweb_config)
    else:
        entries.append({
            "project": default_project,
            "description": "업무 환경 구성 및 기술 검토",
            "minutes": default_minutes,
        })

    return entries


class HRWebUploader:
    def __init__(self, base_url: str, user_id: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.password = password
        self.page: Page | None = None
        self._pw = None
        self._browser = None

    def __enter__(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=False)
        self.page = self._browser.new_page()
        return self

    def __exit__(self, *args):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def _is_logged_in(self) -> bool:
        """사이드바에 Logout 버튼이 있으면 로그인 상태."""
        return self.page.locator("text=Logout").count() > 0

    def login(self):
        """HRWeb에 로그인합니다."""
        self.page.goto(f"{self.base_url}/Account/Login")
        self.page.wait_for_load_state("networkidle")
        time.sleep(2)

        if not self._is_logged_in():
            user_input = self.page.locator(
                'input:visible:not([type="hidden"]):not([type="password"]):not([type="submit"])'
            ).first
            pw_input = self.page.locator('input[type="password"]:visible').first

            user_input.fill(self.user_id)
            pw_input.fill(self.password)
            self.page.locator('button[type="submit"]').first.click()
            self.page.wait_for_load_state("networkidle")
            time.sleep(3)

        # 로그인 검증
        if not self._is_logged_in():
            self.page.screenshot(path="dist/debug_login_failed.png")
            raise RuntimeError(
                "로그인 실패! ID/PW를 확인하세요. (스크린샷: dist/debug_login_failed.png)"
            )

        self.page.goto(f"{self.base_url}/TimeTableManage")
        self.page.wait_for_load_state("networkidle")
        time.sleep(3)

        # Blazor 렌더링 대기 - 달력 테이블이 나타날 때까지
        try:
            self.page.locator("table.table-bordered").wait_for(
                state="visible", timeout=15000
            )
        except Exception:
            self.page.screenshot(path="dist/debug_render.png")
            raise RuntimeError(
                "달력이 로드되지 않았습니다. (스크린샷: dist/debug_render.png)"
            )

    def navigate_to_month(self, year: int, month: int):
        """연도와 월을 선택합니다."""
        # 현재 선택된 연도/월 확인, 같으면 건너뜀
        cur_year = self.page.locator("#yearButton").inner_text().strip()
        cur_month = self.page.locator("#monthButton").inner_text().strip()

        if cur_year != str(year):
            self.page.locator("#yearButton").click()
            time.sleep(0.5)
            self.page.locator(f"#yearButton + ul .dropdown-item >> text='{year}'").click()
            time.sleep(2)

        if cur_month != str(month):
            self.page.locator("#monthButton").click()
            time.sleep(0.5)
            month_items = self.page.locator("#monthButton + ul .dropdown-item").all()
            for item in month_items:
                if item.inner_text().strip() == str(month):
                    item.click()
                    break
            time.sleep(2)

        self.page.wait_for_load_state("networkidle")
        time.sleep(2)

    def select_date(self, day: int) -> bool:
        """달력에서 특정 날짜를 클릭합니다."""
        # 디버그: 달력 셀 내용 확인
        day_cells = self.page.locator(
            "table.table-bordered tbody tr:first-child td"
        ).all()

        if not day_cells:
            print(f"    경고: 달력 셀을 찾지 못했습니다.")
            return False

        clicked = False
        for cell in day_cells:
            if cell.inner_text().strip() == str(day):
                cell.click()
                clicked = True
                break

        if not clicked:
            print(f"    경고: 달력에서 {day}일을 찾지 못했습니다.")
            return False

        time.sleep(3)
        return True

    def has_existing_entries(self) -> bool:
        """현재 선택된 날짜에 이미 입력된 데이터가 있는지 확인합니다."""
        rows = self.page.locator("table.table-hover tbody tr").all()
        return len(rows) > 0

    def get_available_projects(self) -> list[str]:
        """사용 가능한 프로젝트 목록을 반환합니다."""
        self.page.locator("#userProject").click()
        time.sleep(0.5)
        items = self.page.locator("#userProject + ul .dropdown-item").all()
        projects = [item.inner_text().strip() for item in items]
        # 드롭다운 닫기
        self.page.keyboard.press("Escape")
        time.sleep(0.3)
        return projects

    def _select_project(self, project_name: str):
        """프로젝트 드롭다운에서 선택합니다 (부분 매칭)."""
        btn = self.page.locator("#userProject")
        btn.scroll_into_view_if_needed()
        btn.click()
        time.sleep(1)
        items = self.page.locator("#userProject + ul .dropdown-item").all()
        for item in items:
            if project_name in item.inner_text():
                item.click()
                time.sleep(0.5)
                return True
        self.page.keyboard.press("Escape")
        return False

    def fill_entry(self, project: str, description: str, minutes: int):
        """시간 입력 폼을 채우고 제출합니다."""
        if not self._select_project(project):
            print(f"    경고: 프로젝트 '{project}' 를 찾을 수 없습니다. 건너뜀.")
            return False

        self.page.locator("#job").fill(description)
        self.page.locator("#hour").fill(str(minutes))

        self.page.locator('button[type="submit"].btn-success').click()
        time.sleep(2)
        return True

    def upload_day(self, day: int, entries: list[dict], skip_existing: bool = True) -> bool:
        """특정 날짜에 시간 데이터를 입력합니다."""
        if not self.select_date(day):
            return False

        if skip_existing and self.has_existing_entries():
            print(f"    {day}일: 이미 입력됨 → 건너뜀")
            return False

        for entry in entries:
            ok = self.fill_entry(entry["project"], entry["description"], entry["minutes"])
            if ok:
                print(f"    {day}일: [{entry['project'][:20]}] {entry['minutes']}분 ← {entry['description'][:40]}")
        return True


def main():
    parser = argparse.ArgumentParser(description="HRWeb 시간 입력 자동화")
    parser.add_argument("--year", type=int, help="대상 연도")
    parser.add_argument("--month", type=int, help="대상 월")
    parser.add_argument("--day", type=int, help="특정 일자만 입력 (테스트용)")
    parser.add_argument("--dry-run", action="store_true", help="실제 입력 없이 미리보기")
    parser.add_argument("--no-skip", action="store_true", help="기존 데이터가 있어도 입력")
    args = parser.parse_args()

    config = load_config()
    hrweb_config = config.get("hrweb", {})

    if not hrweb_config:
        print("오류: dist/config.json에 hrweb 설정이 없습니다.")
        print("다음 형식으로 추가해주세요:")
        example = {
            "hrweb": {
                "url": "http://your-hrweb-server:11080",
                "user_id": "your-id",
                "password": "your-password",
                "default_minutes_per_day": 480,
                "default_project": "공통(common)",
                "project_map": {
                    "APISS": "초소형군집위성"
                }
            }
        }
        print(json.dumps(example, ensure_ascii=False, indent=4))
        return

    today = datetime.date.today()
    year = args.year or today.year
    month = args.month or today.month

    weekdays = get_weekdays(year, month)
    weekdays = [d for d in weekdays if d <= today]

    if args.day:
        weekdays = [d for d in weekdays if d.day == args.day]

    if not weekdays:
        print(f"{year}년 {month}월에 입력할 평일이 없습니다.")
        return

    print("=" * 60)
    print("  HRWeb 시간 입력 자동화")
    print("=" * 60)
    print(f"  대상      : {year}년 {month}월 (월~금, {len(weekdays)}일)")
    print(f"  작성자    : {config['author']}")
    print(f"  기본 시간 : {hrweb_config.get('default_minutes_per_day', 480)}분/일")
    print("=" * 60)
    print()

    # Google Calendar 출장 일정 조회
    print("[1/3] Google Calendar 조회 중...")
    trip_map = fetch_month_events(config, year, month)
    if trip_map:
        for d, desc in sorted(trip_map.items()):
            print(f"  {d} ({WEEKDAY_KR[d.weekday()]}): {desc}")
    else:
        print("  출장 일정 없음")
    print()

    # 월 전체 커밋 수집 (주변 패턴 분석용)
    print("[2/3] Git 커밋 수집 중...")
    monthly = collect_month_commits(config, year, month)

    daily_data = {}
    for d in weekdays:
        commits = monthly.get(d, {})
        trip = trip_map.get(d)
        entries = build_daily_entries(commits, hrweb_config, d, monthly, trip)
        daily_data[d] = entries

        commit_count = sum(len(c) for c in commits.values())
        entry_summary = " + ".join(
            f"{e['project'][:15]}({e['minutes']}분)" for e in entries
        )
        if trip:
            src = "출장"
        elif commit_count:
            src = "Git"
        else:
            src = "추론"
        status = f"커밋 {commit_count}개" if commit_count else ("출장" if trip else "커밋 없음")
        print(f"  {d} ({WEEKDAY_KR[d.weekday()]}): {status} [{src}] → {entry_summary}")
        for e in entries:
            print(f"    └ {e['description'][:60]}")
    print()

    if args.dry_run:
        print("--dry-run 모드: 실제 입력을 하지 않습니다.")
        return

    # HRWeb에 입력
    url = hrweb_config["url"]
    user_id = hrweb_config["user_id"]
    password = hrweb_config["password"]

    print("[3/3] HRWeb 입력 중...")
    with HRWebUploader(url, user_id, password) as uploader:
        uploader.login()
        print("  로그인 완료")

        uploader.navigate_to_month(year, month)
        print(f"  {year}년 {month}월 선택 완료")
        print()

        success = 0
        skipped = 0

        for d in weekdays:
            entries = daily_data[d]
            result = uploader.upload_day(
                d.day, entries, skip_existing=not args.no_skip
            )
            if result:
                success += 1
            else:
                skipped += 1

        print()
        print(f"  완료! 입력: {success}일, 건너뜀: {skipped}일")


if __name__ == "__main__":
    main()
