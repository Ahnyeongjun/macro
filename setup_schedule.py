"""Windows 작업 스케줄러에 한컴 공수 자동 입력 작업을 등록합니다.

사용법:
    python setup_schedule.py          # 작업 등록
    python setup_schedule.py --remove # 작업 삭제
    python setup_schedule.py --status # 현재 상태 확인
"""

import argparse
import subprocess
import sys
from pathlib import Path

TASK_NAME = "HancomAutoUpload"
SCRIPT = Path(__file__).parent.resolve() / "hancom_uploader.py"
PYTHON = sys.executable


def register(hour: str = "09:00"):
    cmd = (
        f'schtasks /create /tn "{TASK_NAME}" '
        f'/tr "\\"{PYTHON}\\" \\"{SCRIPT}\\"" '
        f'/sc WEEKLY /d MON,TUE,WED,THU,FRI '
        f'/st {hour} /f'
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"등록 완료: {TASK_NAME}")
        print(f"  실행 시간: 매주 월-금 {hour}")
        print(f"  스크립트: {SCRIPT}")
        print(f"\n  컴퓨터가 켜져 있는 날 자동으로 실행됩니다.")
        print(f"  이미 입력된 날짜는 자동으로 건너뜁니다.")
    else:
        print(f"오류: {result.stderr.strip()}")
        sys.exit(1)


def unregister():
    result = subprocess.run(
        f'schtasks /delete /tn "{TASK_NAME}" /f',
        shell=True, capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"삭제 완료: {TASK_NAME}")
    else:
        print(f"오류 (이미 없는 작업일 수 있음): {result.stderr.strip()}")


def status():
    result = subprocess.run(
        f'schtasks /query /tn "{TASK_NAME}" /fo LIST',
        shell=True, capture_output=True, text=True, encoding="cp949"
    )
    if result.returncode == 0:
        print(result.stdout)
    else:
        print(f"등록된 작업 없음: {TASK_NAME}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="한컴 공수 자동 입력 스케줄 설정")
    parser.add_argument("--remove", action="store_true", help="작업 삭제")
    parser.add_argument("--status", action="store_true", help="현재 상태 확인")
    parser.add_argument("--time", default="09:00", help="실행 시각 (기본: 09:00)")
    args = parser.parse_args()

    if args.remove:
        unregister()
    elif args.status:
        status()
    else:
        register(args.time)
