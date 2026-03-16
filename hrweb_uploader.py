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


def build_daily_entries(daily_commits: dict, hrweb_config: dict) -> list[dict]:
    """커밋 데이터를 HRWeb 입력 항목으로 변환합니다.

    커밋이 있는 프로젝트별로 시간을 균등 배분합니다.
    커밋이 없는 날은 기본 프로젝트로 전체 시간을 채웁니다.
    """
    project_map = hrweb_config.get("project_map", {})
    default_project = hrweb_config.get("default_project", "공통(common)")
    default_minutes = hrweb_config.get("default_minutes_per_day", 480)

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
    else:
        entries.append({
            "project": default_project,
            "description": "업무",
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

    def login(self):
        """HRWeb에 로그인합니다."""
        self.page.goto(f"{self.base_url}/TimeTableManage")
        self.page.wait_for_load_state("networkidle")
        time.sleep(2)

        # 로그인 페이지로 리다이렉트된 경우
        if "timetablemanage" not in self.page.url.lower():
            # 일반적인 ASP.NET Identity 로그인 폼
            user_input = self.page.locator(
                'input[name="Input.UserId"], input[name="UserId"], '
                'input[id="UserId"], input[type="text"]'
            ).first
            pw_input = self.page.locator(
                'input[name="Input.Password"], input[name="Password"], '
                'input[id="Password"], input[type="password"]'
            ).first

            user_input.fill(self.user_id)
            pw_input.fill(self.password)
            self.page.locator('button[type="submit"]').first.click()
            self.page.wait_for_load_state("networkidle")
            time.sleep(3)

        if "timetablemanage" not in self.page.url.lower():
            self.page.goto(f"{self.base_url}/TimeTableManage")
            self.page.wait_for_load_state("networkidle")
            time.sleep(2)

    def navigate_to_month(self, year: int, month: int):
        """연도와 월을 선택합니다."""
        # 연도 선택
        self.page.locator("#yearButton").click()
        time.sleep(0.5)
        self.page.locator(f"#yearButton + ul .dropdown-item >> text='{year}'").click()
        time.sleep(1.5)

        # 월 선택
        self.page.locator("#monthButton").click()
        time.sleep(0.5)
        month_items = self.page.locator("#monthButton + ul .dropdown-item").all()
        for item in month_items:
            if item.inner_text().strip() == str(month):
                item.click()
                break
        time.sleep(2)

    def select_date(self, day: int):
        """달력에서 특정 날짜를 클릭합니다."""
        # tbody 첫 번째 행(요일 행)의 td 셀에서 날짜 찾기
        day_cells = self.page.locator(
            "table.table-bordered tbody tr:first-child td"
        ).all()
        for cell in day_cells:
            if cell.inner_text().strip() == str(day):
                cell.click()
                break
        time.sleep(2)

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
        self.page.locator("#userProject").click()
        time.sleep(0.5)
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
        self.select_date(day)

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

    # 날짜별 커밋 수집 & 입력 항목 생성
    daily_data = {}
    print("[1/2] Git 커밋 수집 중...")
    for d in weekdays:
        commits = collect_daily_commits(config, d)
        entries = build_daily_entries(commits, hrweb_config)
        daily_data[d] = entries

        commit_count = sum(len(c) for c in commits.values())
        entry_summary = " + ".join(
            f"{e['project'][:15]}({e['minutes']}분)" for e in entries
        )
        status = f"커밋 {commit_count}개" if commit_count else "커밋 없음"
        print(f"  {d} ({WEEKDAY_KR[d.weekday()]}): {status} → {entry_summary}")
    print()

    if args.dry_run:
        print("--dry-run 모드: 실제 입력을 하지 않습니다.")
        return

    # HRWeb에 입력
    url = hrweb_config["url"]
    user_id = hrweb_config["user_id"]
    password = hrweb_config["password"]

    print("[2/2] HRWeb 입력 중...")
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
