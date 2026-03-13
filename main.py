"""주간업무보고 자동화 - 진입점

사용법:
    python main.py                     # 이번 주 보고서 생성 + 메일 발송
    python main.py --date 2025-03-10   # 특정 주의 보고서
    python main.py --no-email          # 엑셀만 생성
    python main.py --quick             # 수동 입력 없이 빠르게 생성
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import calendar_client
import email_sender
import excel_writer
import git_collector

SCRIPT_DIR = Path(__file__).parent
DIST_DIR = SCRIPT_DIR / "dist"
WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def load_config():
    path = DIST_DIR / "config.json"
    if not path.exists():
        print(f"설정 파일이 없습니다: {path}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_week_info(target_date=None):
    if target_date is None:
        target_date = datetime.date.today()
    monday = target_date - datetime.timedelta(days=target_date.weekday())
    friday = monday + datetime.timedelta(days=4)
    week_num = (monday.day - 1) // 7 + 1
    return monday, friday, week_num


def date_to_excel_serial(d):
    return (d - datetime.date(1899, 12, 30)).days


def main():
    parser = argparse.ArgumentParser(description="주간업무보고 자동화")
    parser.add_argument("--date", help="대상 날짜 (YYYY-MM-DD), 기본: 오늘")
    parser.add_argument("--no-email", action="store_true", help="엑셀만 생성")
    parser.add_argument("--quick", action="store_true", help="수동 입력 없이 빠르게 생성")
    args = parser.parse_args()

    DIST_DIR.mkdir(exist_ok=True)
    config = load_config()

    # 템플릿 로드 & 카테고리 셀 자동 감지
    template_path = DIST_DIR / config.get("template", "template.xlsx")
    if not template_path.exists():
        print(f"오류: 템플릿 파일 없음 - {template_path}")
        sys.exit(1)

    cell_map = excel_writer.detect_category_cells(str(template_path))
    categories = excel_writer.resolve_cell_refs(config.get("categories", []), cell_map)

    # 날짜 계산
    target_date = datetime.date.fromisoformat(args.date) if args.date else datetime.date.today()
    monday, friday, week_num = get_week_info(target_date)
    next_monday = monday + datetime.timedelta(days=7)
    next_friday = friday + datetime.timedelta(days=7)

    print("=" * 50)
    print("  주간업무보고 자동 생성")
    print("=" * 50)
    print(f"  대상 기간 : {monday} ({WEEKDAY_KR[monday.weekday()]}) ~ {friday} ({WEEKDAY_KR[friday.weekday()]})")
    print(f"  주차      : {monday.year}년 {monday.month}월 {week_num}째주")
    print(f"  작성자    : {config['author']}")
    print(f"  카테고리  : {', '.join(c['name'] for c in categories)}")
    print("=" * 50)
    print()

    # ----- Google Calendar -----
    gcal_cfg = config.get("google_calendar", {})
    cal_service = None
    if gcal_cfg.get("enabled", False):
        print("[0/4] Google Calendar 연결 중...")
        cal_service = calendar_client.connect(DIST_DIR, gcal_cfg)
        print("  연결 완료" if cal_service else "  연결 실패 - 출장 일정 없이 진행")
        print()

    cal_id = gcal_cfg.get("calendar_id", "primary")
    trip_kw = gcal_cfg.get("trip_keyword", "출장")

    # ----- 1. Git 커밋 수집 -----
    print("[1/4] Git 커밋 로그 수집 중...")
    cat_commits = {}
    for cat in categories:
        repos = cat.get("repos", [])
        if not repos:
            continue
        name = cat["name"]
        all_commits = []
        for repo in repos:
            if not os.path.isdir(repo):
                print(f"  경고: 경로 없음 - {repo}")
                continue
            commits = git_collector.collect(repo, config["author"], monday, friday)
            print(f"  [{name}] {repo} → {len(commits)}개 커밋")
            all_commits.extend(commits)
        cat_commits[name] = all_commits
    print()

    # ----- 2. 데이터 구성 -----
    print("[2/4] 보고서 데이터 구성 중...")
    updates = {"B1": date_to_excel_serial(friday)}

    print("\n--- 금주 업무 내용 ---")
    for cat in categories:
        name, cell = cat["name"], cat["this_week_cell"]

        if name in cat_commits and cat_commits[name]:
            content = git_collector.format_by_feature(cat_commits[name])
        elif cat.get("use_calendar") and cal_service:
            events = calendar_client.fetch_trips(cal_service, cal_id, monday, friday, trip_kw)
            content = calendar_client.format_trips(events)
        elif "default_this_week" in cat:
            content = cat["default_this_week"]
        elif not args.quick:
            content = input(f"  {name} (금주 내용, Enter=없음): ").strip() or "x"
        else:
            content = "x"

        updates[cell] = content
        if content != "x":
            for line in content.split("\n"):
                print(f"  [{name}] {line}")

    print("\n--- 차주 업무 목표 ---")
    for cat in categories:
        name, cell = cat["name"], cat["next_week_cell"]

        if cat.get("use_calendar") and cal_service:
            events = calendar_client.fetch_trips(cal_service, cal_id, next_monday, next_friday, trip_kw)
            content = calendar_client.format_trips(events)
        elif "default_next_week" in cat:
            content = cat["default_next_week"]
        elif not args.quick:
            content = input(f"  {name} (차주 목표, Enter=없음): ").strip() or "x"
        else:
            content = "x"

        updates[cell] = content
        if content != "x":
            for line in content.split("\n"):
                print(f"  [{name}] {line}")
    print()

    # ----- 3. 엑셀 생성 -----
    print("[3/4] 엑셀 파일 생성 중...")
    filename = f"{monday.year}_{monday.month:02d}_{week_num}째주 {config['name']} 주간업무보고.xlsx"
    output_path = DIST_DIR / filename

    excel_writer.update(str(template_path), str(output_path), updates)
    print(f"  생성 완료: {output_path}")
    print()

    # ----- 4. 메일 발송 -----
    if args.no_email:
        print("[4/4] --no-email 옵션으로 메일 발송 건너뜀")
    else:
        email_cfg = config.get("email", {})
        sender = email_cfg.get("sender", "")
        app_pw = email_cfg.get("app_password", "")
        recipients = email_cfg.get("recipients", [])

        if not sender or sender == "your.email@gmail.com":
            print("[4/4] 이메일 미설정. dist/config.json을 확인해주세요.")
        elif not recipients:
            print("[4/4] 수신자 미설정.")
        else:
            subject = filename.replace(".xlsx", "")
            body = f"{config['name']} {monday.year}년 {monday.month}월 {week_num}째주 주간업무보고입니다."
            confirm = "y" if args.quick else input(f"  '{subject}' 메일 발송? (y/N): ").strip().lower()
            if confirm == "y":
                print(f"  발송 중... ({sender} → {', '.join(recipients)})")
                try:
                    email_sender.send(sender, app_pw, recipients, subject, body, str(output_path))
                    print("  메일 발송 완료!")
                except Exception as e:
                    print(f"  메일 발송 실패: {e}")
            else:
                print("  메일 발송 취소")

    print("\n완료!")


if __name__ == "__main__":
    main()
