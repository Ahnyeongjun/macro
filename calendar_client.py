"""Google Calendar 연동 - 출장 일정 조회 + 일정 생성"""

import datetime
from pathlib import Path

WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

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
    """지정 기간의 출장 일정을 가져옵니다.
    반환: [(date, summary, description)]"""
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
            events.append((
                datetime.date.fromisoformat(date_str),
                item.get("summary", ""),
                item.get("description", ""),
            ))
        except ValueError:
            continue
    return events


def format_trips(events):
    """출장 이벤트를 [MM-DD/요일] 형식으로 포맷합니다."""
    if not events:
        return "x"
    lines = []
    for ev in sorted(events, key=lambda x: x[0]):
        date, summary = ev[0], ev[1]
        wd = WEEKDAY_KR[date.weekday()]
        lines.append(f"[{date.strftime('%m-%d')}/{wd}] {summary}")
    return "\n".join(lines)


def format_trips_as_headers(events):
    """출장 이벤트를 대카테고리 형식으로 포맷합니다.

    출력 예:
      [03-12/목] 항우연 출장
      - 기술미팅
      - APISS 시연
    """
    if not events:
        return ""
    lines = []
    for ev in sorted(events, key=lambda x: x[0]):
        date, summary = ev[0], ev[1]
        desc = ev[2] if len(ev) > 2 else ""
        wd = WEEKDAY_KR[date.weekday()]
        lines.append(f"[{date.strftime('%m-%d')}/{wd}] {summary}")
        if desc:
            for dl in desc.strip().split("\n"):
                dl = dl.strip()
                if dl:
                    if not dl.startswith("-"):
                        dl = f"- {dl}"
                    lines.append(dl)
    return "\n".join(lines)


def categorize_trips(events, category_names):
    """이벤트 제목에 카테고리 이름이 포함되면 해당 카테고리로 분류합니다.
    매칭되지 않은 이벤트는 None 키에 모입니다.
    events: [(date, summary, description)]"""
    result = {name: [] for name in category_names}
    result[None] = []

    for ev in events:
        date, summary = ev[0], ev[1]
        desc = ev[2] if len(ev) > 2 else ""
        matched = False
        for name in category_names:
            if name in summary:
                clean_summary = summary.replace(f"[{name}]", "").replace(name, "").strip()
                clean_summary = clean_summary.lstrip("- ").strip()
                result[name].append((date, clean_summary or summary, desc))
                matched = True
                break
        if not matched:
            result[None].append((date, summary, desc))
    return result


def create_event(service, calendar_id: str, title: str,
                 start_date: datetime.date, end_date: datetime.date | None = None,
                 description: str = "", location: str = ""):
    """Google Calendar에 일정을 생성합니다. 종일 이벤트로 생성됩니다."""
    if service is None:
        return None

    if end_date is None:
        end_date = start_date + datetime.timedelta(days=1)
    else:
        end_date = end_date + datetime.timedelta(days=1)

    body = {
        "summary": title,
        "start": {"date": start_date.isoformat()},
        "end": {"date": end_date.isoformat()},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location

    try:
        event = service.events().insert(calendarId=calendar_id, body=body).execute()
        return event.get("htmlLink")
    except Exception as e:
        print(f"  경고: 일정 생성 실패: {e}")
        return None
