"""Google Calendar 연동 - 출장 일정 조회"""

import datetime
from pathlib import Path

WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    _HAS_LIBS = True
except ImportError:
    _HAS_LIBS = False


def connect(dist_dir: Path, gcal_cfg: dict):
    """Google Calendar API 서비스를 반환합니다. 실패 시 None."""
    if not _HAS_LIBS:
        print("  경고: Google Calendar 패키지 미설치")
        print("  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        return None

    creds_path = dist_dir / gcal_cfg.get("credentials_path", "credentials.json")
    token_path = dist_dir / gcal_cfg.get("token_path", "token.json")

    if not creds_path.exists():
        print(f"  경고: 인증 파일 없음 - {creds_path}")
        return None

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def fetch_trips(service, calendar_id: str, start: datetime.date, end: datetime.date, keyword: str):
    """지정 기간의 출장 일정을 가져옵니다."""
    if service is None:
        return []

    time_min = f"{start.isoformat()}T00:00:00+09:00"
    time_max = f"{end.isoformat()}T23:59:59+09:00"

    try:
        result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            q=keyword,
        ).execute()
    except Exception as e:
        print(f"  경고: Google Calendar 조회 실패: {e}")
        return []

    events = []
    for item in result.get("items", []):
        s = item.get("start", {})
        date_str = s.get("date") or s.get("dateTime", "")[:10]
        try:
            events.append((datetime.date.fromisoformat(date_str), item.get("summary", "")))
        except ValueError:
            continue
    return events


def format_trips(events):
    """출장 이벤트를 [MM-DD/요일] 형식으로 포맷합니다."""
    if not events:
        return "x"
    lines = []
    for date, summary in sorted(events, key=lambda x: x[0]):
        wd = WEEKDAY_KR[date.weekday()]
        lines.append(f"[{date.strftime('%m-%d')}/{wd}] {summary}")
    return "\n".join(lines)
