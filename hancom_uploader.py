"""한컴인스페이스 공수 관리 대시보드 자동화

사용법:
    python hancom_uploader.py --setup              # 최초 로그인 & 세션 저장
    python hancom_uploader.py --dry-run            # 미리보기만
    python hancom_uploader.py                      # 이번 달 공수 입력
    python hancom_uploader.py --year 2026 --month 6
"""

import argparse
import base64
import calendar
import datetime
import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

import git_collector

SCRIPT_DIR = Path(__file__).parent
DIST_DIR = SCRIPT_DIR / "dist"
HANCOM_URL = "https://hancom-dashboard.vercel.app"
SESSION_FILE = DIST_DIR / "hancom_session.json"
WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def _safe(text: str) -> str:
    return text.encode("cp949", errors="replace").decode("cp949")


def load_config():
    path = DIST_DIR / "config.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_weekdays(year: int, month: int) -> list[datetime.date]:
    import holidays as hl
    kr_holidays = hl.KR(years=year)
    cal = calendar.Calendar()
    return [
        d for d in cal.itermonthdates(year, month)
        if d.month == month and d.weekday() < 5 and d not in kr_holidays
    ]


def collect_daily_commits(config, target_date: datetime.date) -> dict:
    categories = config.get("categories", [])
    github_token = config.get("github_token") or os.environ.get("GITHUB_TOKEN")
    result = {}
    for cat in categories:
        name = cat["name"]
        commits = []

        # 로컬 git 저장소
        for repo in cat.get("repos", []):
            if Path(repo).is_dir():
                day_commits = git_collector.collect(
                    repo, config["author"], target_date, target_date
                )
                commits.extend(day_commits)

        # GitHub API (로컬 커밋이 없거나 github_repos 설정 시)
        if not commits or cat.get("github_repos"):
            for gh_repo in cat.get("github_repos", []):
                day_commits = git_collector.collect_github(
                    gh_repo, config["author"], target_date, target_date, github_token
                )
                commits.extend(day_commits)

        if commits:
            result[name] = commits
    return result


def build_entries(daily_commits: dict, hancom_config: dict,
                  target_date: datetime.date = None,
                  monthly: dict = None) -> list[dict]:
    project_map = hancom_config.get("project_map", {})
    project_full_names = hancom_config.get("project_full_names", {})
    default_project = hancom_config.get("default_project", "")
    default_minutes = hancom_config.get("default_minutes_per_day", 480)
    name = hancom_config.get("name", "")

    if daily_commits:
        project_count = len(daily_commits)
        minutes_each = default_minutes // project_count
        remainder = default_minutes % project_count
        entries = []
        for i, (cat_name, commits) in enumerate(daily_commits.items()):
            project_keyword = project_map.get(cat_name, default_project)
            desc = git_collector.format_oneline(commits)
            if not desc:
                desc = f"{cat_name} 관련 작업"
            mins = minutes_each + (remainder if i == 0 else 0)
            full_name = project_full_names.get(project_keyword, project_keyword)
            entries.append({
                "date": target_date.isoformat() if target_date else "",
                "name": name,
                "project_keyword": project_keyword,
                "project_name": full_name,
                "description": desc,
                "minutes": mins,
            })
        return entries

    desc = _infer_description(target_date, monthly) if (target_date and monthly) else "업무 환경 구성 및 기술 검토"
    full_name = project_full_names.get(default_project, default_project)
    return [{
        "date": target_date.isoformat() if target_date else "",
        "name": name,
        "project_keyword": default_project,
        "project_name": full_name,
        "description": desc,
        "minutes": default_minutes,
    }]


def _infer_description(target_date: datetime.date, monthly: dict) -> str:
    templates = {
        0: "설계 검토 및 개발 환경 구성",
        1: "코드 분석 및 구현",
        2: "기능 개발 및 단위 테스트",
        3: "코드 리뷰 및 이슈 대응",
        4: "테스트 및 배포 검증",
    }
    return templates.get(target_date.weekday(), "개발 업무")


class HancomUploader:
    def __init__(self, google_email: str = "", google_password: str = "", headless: bool = False):
        self.google_email = google_email
        self.google_password = google_password
        self.headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self.page: Page | None = None

    def __enter__(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        if SESSION_FILE.exists():
            try:
                # 세션 파일 유효성 확인 (최소 1KB 이상이어야 정상)
                if SESSION_FILE.stat().st_size < 1024:
                    raise ValueError(f"세션 파일 너무 작음 ({SESSION_FILE.stat().st_size}B)")
                self._context = self._browser.new_context(storage_state=str(SESSION_FILE))
            except Exception as e:
                print(f"  세션 파일 오류 ({e}), 새 세션으로 시작")
                SESSION_FILE.unlink(missing_ok=True)
                self._context = self._browser.new_context()
        else:
            self._context = self._browser.new_context()
        self.page = self._context.new_page()
        return self

    def __exit__(self, *args):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def _save_session(self):
        DIST_DIR.mkdir(exist_ok=True)
        self._context.storage_state(path=str(SESSION_FILE))
        print(f"  세션 저장 완료: {SESSION_FILE}")

    def login(self):
        """Google 계정으로 로그인하고 세션을 저장합니다."""
        self.page.goto(f"{HANCOM_URL}/login", wait_until="networkidle")
        time.sleep(1)

        # 이미 로그인 상태면 스킵
        if self._is_logged_in():
            print("  이미 로그인 상태입니다.")
            return

        # Google 로그인 버튼
        self.page.get_by_role("button", name="Google 계정으로 로그인").click()
        time.sleep(2)

        if self.google_email and self.google_password:
            # 이메일/비밀번호 자동 입력
            email_box = self.page.get_by_role("textbox", name="이메일 또는 휴대전화")
            email_box.fill(self.google_email)
            email_box.press("Enter")
            time.sleep(2)

            pw_box = self.page.get_by_role("textbox", name="비밀번호 입력")
            pw_box.fill(self.google_password)
            pw_box.press("Enter")
            time.sleep(2)

        # 모바일 인증 등 2FA 완료 후 대시보드로 돌아올 때까지 대기 (최대 3분)
        print("  모바일 인증이 필요하면 폰에서 완료해주세요. 자동으로 감지합니다...")
        try:
            self.page.wait_for_url(f"{HANCOM_URL}/**", timeout=180000)
        except Exception:
            pass
        self.page.wait_for_load_state("networkidle")
        time.sleep(2)
        self._save_session()

    def _is_logged_in(self) -> bool:
        try:
            self.page.goto(f"{HANCOM_URL}/", wait_until="networkidle", timeout=15000)
            time.sleep(2)
            return (
                self.page.locator("text=로그아웃").count() > 0
                or self.page.locator("text=안영준").count() > 0
            )
        except Exception:
            return False

    def get_existing_dates(self) -> set[str]:
        """일일 공수 목록에서 이미 입력된 날짜(YYYY-MM-DD)를 가져옵니다."""
        import re
        # 홈에서 시작
        self.page.reload(wait_until="networkidle")
        time.sleep(1.5)
        tab_btn = self.page.get_by_role("button", name="📋 일일 공수")
        tab_btn.wait_for(state="visible", timeout=10000)
        tab_btn.click()
        time.sleep(2)
        all_text = self.page.inner_text("body")

        # 디버그: 날짜처럼 보이는 숫자 패턴 출력
        sample = [line.strip() for line in all_text.splitlines() if re.search(r"\d{1,4}[-./년]\s*\d{1,2}", line)][:5]
        if sample:
            print(f"    [날짜 샘플] {sample[0][:80]}")

        current_year = datetime.date.today().year

        # YYYY-MM-DD 패턴 (가장 정확)
        dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", all_text))

        # 한국어 날짜: 2026년 5월 15일 → 2026-05-15
        for m in re.finditer(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", all_text):
            y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
            dates.add(f"{y}-{mo}-{d}")

        # MM-DD 패턴 (YYYY-MM-DD의 일부가 아닌 것만) → 현재 연도 가정
        for m in re.finditer(r"(?<!\d)(\d{2})-(\d{2})(?!\d)", all_text):
            mo, d = m.group(1), m.group(2)
            if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
                dates.add(f"{current_year}-{mo}-{d}")

        # MM/DD 패턴 → 현재 연도 가정
        for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})\b", all_text):
            mo, d = m.group(1).zfill(2), m.group(2).zfill(2)
            dates.add(f"{current_year}-{mo}-{d}")

        return dates

    def navigate_to_form(self):
        """일일 공수 탭 → + 공수 입력 클릭."""
        # 페이지 리로드로 상태 초기화 (SPA 토글 문제 방지)
        self.page.reload(wait_until="networkidle")
        time.sleep(2)

        # 탭 버튼이 보일 때까지 대기
        tab_btn = self.page.get_by_role("button", name="📋 일일 공수")
        tab_btn.wait_for(state="visible", timeout=15000)

        # 이미 공수입력 버튼이 있으면 탭 클릭 불필요 (toggle 방지)
        add_btn = self.page.locator("button").filter(has_text="공수 입력")
        if add_btn.count() == 0 or not add_btn.first.is_visible():
            tab_btn.click()
            time.sleep(2)

        add_btn.wait_for(state="visible", timeout=15000)
        add_btn.first.click()
        time.sleep(1)

    def fill_entry(self, entry: dict) -> bool:
        """공수 입력 폼을 채우고 저장합니다."""
        try:
            # 이름
            self.page.get_by_role("textbox", name="이름 입력").fill(entry["name"])
            time.sleep(0.3)

            # 날짜
            self.page.locator('input[type="date"]').fill(entry["date"])
            time.sleep(0.3)

            # 프로젝트 선택 (검색 드롭다운)
            full_name = entry.get("project_name", entry["project_keyword"])
            self._select_project(entry["project_keyword"], full_name)

            # 수행업무
            self.page.get_by_role("textbox", name="수행한 업무 내용").fill(entry["description"])
            time.sleep(0.3)

            # 시간(분) - 프리셋 버튼 우선
            self._fill_minutes(entry["minutes"])

            # 저장
            save_btn = self.page.get_by_role("button", name="공수 기록 저장")
            save_btn.click()

            # 저장 성공 확인: 저장 버튼이 사라지거나(모달 닫힘) 성공 토스트 대기
            saved = False
            try:
                self.page.wait_for_selector(
                    'button:has-text("공수 기록 저장")', state="hidden", timeout=8000
                )
                saved = True
                print("    저장됨 (모달 닫힘 확인)")
            except Exception:
                pass

            if not saved:
                # 성공 키워드는 버튼과 무관한 곳에서만 체크
                time.sleep(1.5)
                toast_els = self.page.locator("text=성공").all() + self.page.locator("text=완료").all()
                if toast_els:
                    saved = True
                    print("    저장됨 (성공 메시지 확인)")

            if not saved:
                self.page.screenshot(path=str(DIST_DIR / "debug_save_fail.png"))
                print("    경고: 저장 확인 불가 (스크린샷 저장됨)")

            time.sleep(1)
            return saved

        except Exception as e:
            print(f"    오류: {e}")
            self.page.screenshot(path=str(DIST_DIR / "debug_hancom.png"))
            return False

    def _select_project(self, keyword: str, full_name: str):
        """프로젝트 드롭다운에서 항목을 선택합니다."""
        self.page.get_by_text("프로젝트 선택...▼").click()
        time.sleep(0.5)
        search = self.page.get_by_role("textbox", name="프로젝트명 / 코드 / PM 검색")
        search.fill(keyword)
        time.sleep(2)

        # JS로 드롭다운 결과 항목 클릭 (React 커스텀 컴포넌트 대응)
        short = full_name[:20]
        clicked = self.page.evaluate("""
            (searchText) => {
                // 일반적인 드롭다운 패턴 시도
                const selectors = [
                    '[role="option"]', '[role="listitem"]', 'li',
                    'div[class*="item"]', 'div[class*="option"]',
                    'div[class*="result"]', 'div[class*="menu"] > div'
                ];
                for (const sel of selectors) {
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                        if (el.textContent.includes(searchText) && el.getBoundingClientRect().height > 0) {
                            el.click();
                            return sel + ':' + el.textContent.substring(0, 40);
                        }
                    }
                }
                // 최후 수단: 텍스트 노드로 탐색
                const all = Array.from(document.querySelectorAll('*')).filter(el => {
                    const rect = el.getBoundingClientRect();
                    return rect.height > 10 && rect.height < 80 && rect.width > 100
                        && el.children.length <= 2
                        && el.textContent.trim().startsWith(searchText);
                });
                if (all.length > 0) {
                    all[0].click();
                    return 'fallback:' + all[0].textContent.substring(0, 40);
                }
                return null;
            }
        """, short)

        if clicked:
            print(f"    프로젝트 선택: {clicked}")
        else:
            print(f"    JS 클릭 실패, 키보드 입력 시도")
            search.press("ArrowDown")
            time.sleep(0.3)
            search.press("Enter")

        time.sleep(0.5)

    def _fill_minutes(self, minutes: int):
        preset_map = {60: "1h", 120: "2h", 240: "4h", 360: "6h", 480: "8h"}
        label = preset_map.get(minutes)
        if label:
            btn = self.page.get_by_role("button", name=label)
            if btn.count() > 0:
                btn.first.click()
                time.sleep(0.2)
                return
        # 직접 입력
        inp = self.page.locator('input[type="number"]')
        if inp.count() > 0:
            inp.first.fill(str(minutes))


def main():
    parser = argparse.ArgumentParser(description="한컴 공수 자동 입력")
    parser.add_argument("--setup", action="store_true", help="Google 로그인 세션 설정")
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int)
    parser.add_argument("--day", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fill-empty", action="store_true", help="커밋 없는 날도 템플릿으로 입력")
    parser.add_argument("--headless", action="store_true", help="브라우저 headless 모드 (CI 환경용)")
    args = parser.parse_args()

    # CI 환경: 환경변수에서 세션 복원
    session_b64 = os.environ.get("HANCOM_SESSION")
    if session_b64 and not SESSION_FILE.exists():
        DIST_DIR.mkdir(exist_ok=True)
        SESSION_FILE.write_bytes(base64.b64decode(session_b64))
        print("  세션 환경변수에서 복원됨")

    # CI 환경: config.json을 환경변수에서 복원
    config_json = os.environ.get("HANCOM_CONFIG")
    config_path = DIST_DIR / "config.json"
    if config_json and not config_path.exists():
        DIST_DIR.mkdir(exist_ok=True)
        config_path.write_text(config_json, encoding="utf-8")
        print("  config 환경변수에서 복원됨")

    # CI 환경이면 headless 자동 활성화
    headless = args.headless or os.environ.get("CI") == "true"

    DIST_DIR.mkdir(exist_ok=True)
    config = load_config()
    hancom_config = config.get("hancom", {})
    if not hancom_config:
        print("오류: dist/config.json에 hancom 설정이 없습니다.")
        return

    google_email = hancom_config.get("google_email", "")
    google_password = hancom_config.get("google_password", "")

    if args.setup:
        print("Google 로그인을 진행합니다...")
        with HancomUploader(google_email, google_password, headless=headless) as u:
            u.login()
        print("완료! 이제 자동 입력을 사용할 수 있습니다.")
        return

    if not args.dry_run and not SESSION_FILE.exists():
        print("세션 파일이 없습니다. 먼저 실행해주세요:")
        print("  python hancom_uploader.py --setup")
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

    print(f"[1/2] {year}년 {month}월 커밋 수집 중... ({len(weekdays)}일)")
    monthly = {d: collect_daily_commits(config, d) for d in weekdays}

    daily_data = {}
    empty_days = []

    for d in weekdays:
        commits = monthly.get(d, {})
        if commits:
            daily_data[d] = build_entries(commits, hancom_config, d, monthly)
            for e in daily_data[d]:
                print(f"  {d} ({WEEKDAY_KR[d.weekday()]}) [Git] {e['project_keyword']} - {_safe(e['description'][:40])} ({e['minutes']}분)")
        elif args.fill_empty or args.day:
            daily_data[d] = build_entries({}, hancom_config, d, monthly)
            for e in daily_data[d]:
                print(f"  {d} ({WEEKDAY_KR[d.weekday()]}) [템플릿] {e['project_keyword']} - {_safe(e['description'][:40])} ({e['minutes']}분)")
        else:
            empty_days.append(d)

    if empty_days:
        print(f"\n커밋 없는 날 ({len(empty_days)}일) - 직접 입력 필요:")
        for d in empty_days:
            print(f"  {d} ({WEEKDAY_KR[d.weekday()]})")

    if args.dry_run:
        print("\n--dry-run 모드: 실제 입력 안 함.")
        return

    if not daily_data:
        print("\n입력할 커밋 데이터가 없습니다.")
        return

    print(f"\n[2/2] 한컴 대시보드 입력 중...")
    with HancomUploader(google_email, google_password, headless=headless) as u:
        if not u._is_logged_in():
            print("  세션 만료 → 재로그인 중...")
            u.login()

        print("  로그인 확인 완료")
        existing = u.get_existing_dates()
        if existing:
            print(f"  이미 입력된 날짜 {len(existing)}건 확인됨 → 건너뜀")

        success = 0
        skipped = 0
        entered_dates = []
        for d, entries in daily_data.items():
            if d.isoformat() in existing:
                print(f"  - {d} ({WEEKDAY_KR[d.weekday()]}): 이미 입력됨 → 건너뜀")
                skipped += 1
                continue
            for entry in entries:
                u.navigate_to_form()
                ok = u.fill_entry(entry)
                if ok:
                    print(f"  OK {d} ({WEEKDAY_KR[d.weekday()]}): {_safe(entry['description'][:40])} ({entry['minutes']}분)")
                    success += 1
                    if d.isoformat() not in entered_dates:
                        entered_dates.append(d.isoformat())

        # 같은 브라우저에서 일일 공수 목록 재조회로 저장 검증
        if entered_dates:
            print("\n[검증] 일일 공수 목록에서 저장 확인 중...")
            after_dates = u.get_existing_dates()
            for ds in entered_dates:
                mark = "저장 확인" if ds in after_dates else "실패 (목록에 없음)"
                print(f"  {mark}: {ds}")

    print(f"\n입력 완료: {success}건 OK, {skipped}건 건너뜀")


if __name__ == "__main__":
    main()
