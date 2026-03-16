# macro

Git 커밋 로그를 폴더별로 정리하고, Google Calendar 출장 일정을 연동하여
주간업무보고 엑셀을 자동 생성한 뒤 Gmail로 발송하는 자동화 스크립트입니다.
HRWeb 시간 입력도 자동화할 수 있습니다.

## 사용법

### 주간업무보고

```bash
python main.py                     # 이번 주 보고서 생성 + 메일 발송
python main.py --date 2025-03-10   # 특정 주의 보고서
python main.py --no-email          # 엑셀만 생성
python main.py --quick             # 수동 입력 없이 빠르게 생성
```

### HRWeb 시간 입력

```bash
python hrweb_uploader.py --dry-run               # 미리보기 (입력하지 않음)
python hrweb_uploader.py                          # 이번 달 월~금 전체 입력
python hrweb_uploader.py --year 2026 --month 2    # 특정 월 입력
python hrweb_uploader.py --day 16                 # 특정 일자만 입력
python hrweb_uploader.py --no-skip                # 기존 데이터 있어도 덮어쓰기
```

HRWeb 입력 시 데이터 소스 우선순위:

1. **Google Calendar 출장** — 출장 일정이 있으면 출장 내용 사용
2. **Git 커밋** — 커밋이 있으면 실제 커밋 메시지 사용
3. **패턴 추론** — 주변 커밋 패턴을 분석하여 업무 설명 자동 생성

## 설정

`dist/config.json`에서 설정합니다 (gitignored).

```jsonc
{
    "author": "작성자",                  // Git 작성자명
    "name": "표시이름",                  // 보고서 표시 이름
    "template": "template.xlsx",        // 템플릿 파일명 (dist/ 기준)
    "categories": [
        {
            "name": "APISS",            // 템플릿 C열의 카테고리명과 매칭
            "repos": [                  // Git 저장소 경로 배열
                "C:\\work\\apiss",
                "C:\\work\\instationx"
            ]
        },
        {
            "name": "기타",
            "default_this_week": "월 ~ 수(판교)\n목 ~ 금(대전)"
        },
        {
            "name": "이슈사항",
            "use_calendar": true         // Google Calendar 출장 연동
        }
    ],
    "hrweb": {
        "url": "http://your-hrweb-server:11080",
        "user_id": "your-id",
        "password": "your-password",
        "default_minutes_per_day": 480,
        "default_project": "공통(common)",
        "project_map": {
            "APISS": "초소형군집위성"    // Git 카테고리 → HRWeb 프로젝트 매핑
        }
    }
}
```

- `categories[].name`만 템플릿의 C열 카테고리명과 일치하면 셀 위치는 자동 계산됩니다.
- `repos`가 있는 카테고리는 Git 커밋 로그를 자동 수집합니다.
- `hrweb.project_map`으로 Git 카테고리를 HRWeb 프로젝트에 매핑합니다.

## 폴더 구조

```
macro/
├── main.py               # 주간보고서 CLI
├── hrweb_uploader.py      # HRWeb 시간 입력 자동화 (Playwright)
├── mcp_server.py          # MCP 서버 (Cursor/Claude 연동)
├── git_collector.py       # Git 커밋 수집 & 폴더별 정리
├── excel_writer.py        # 엑셀 XML 조작 + 템플릿 셀 자동 감지
├── email_sender.py        # Gmail SMTP 발송
├── calendar_client.py     # Google Calendar 출장 연동
├── requirements.txt       # 의존성
└── dist/                  # (gitignored) 설정 + 인증 + 결과물
    ├── config.json
    ├── template.xlsx
    ├── credentials.json   # Google Calendar 인증 (선택)
    └── *.xlsx             # 생성된 보고서
```

## Google Calendar 연동 (선택)

1. [Google Cloud Console](https://console.cloud.google.com) → 프로젝트 생성
2. Google Calendar API 활성화
3. OAuth 2.0 클라이언트 ID (데스크톱 앱) 생성
4. `credentials.json` 다운로드 → `dist/` 에 저장
5. `dist/config.json`에서 `google_calendar.enabled` → `true`
6. 첫 실행 시 브라우저 인증 → 이후 `token.json`으로 자동 갱신

## MCP 서버 (Cursor/Claude 연동)

Cursor 또는 Claude Desktop에서 자연어로 보고서 생성 및 HRWeb 입력을 할 수 있습니다.

### Cursor 설정

`.cursor/mcp.json`에 추가:

```json
{
  "mcpServers": {
    "weekly-report": {
      "command": "python",
      "args": ["mcp_server.py"],
      "env": {}
    }
  }
}
```

### 제공 도구

| Tool | 설명 |
|------|------|
| `list_commits` | 특정 주의 커밋 로그 조회 (최대 4주, 패턴 분석용) |
| `get_trips` | Google Calendar 출장 일정 조회 |
| `create_calendar_event` | Google Calendar에 새 일정 생성 |
| `generate_report` | 주간업무보고 엑셀 생성 |
| `generate_report_with_content` | AI가 정리한 내용으로 엑셀 생성 |
| `send_report` | 생성된 보고서 Gmail 발송 |
| `preview_hrweb` | HRWeb 시간 입력 미리보기 (AI가 빈 날짜 채움) |
| `upload_hrweb` | HRWeb에 시간 데이터 자동 입력 |

### 사용 예시

```
이번 주 주간업무보고 만들어줘
최근 3주간 커밋 보여주고 다음 주 할 일 추천해줘
보고서 생성하고 메일 보내줘
3월 20일에 관평동 출장 일정 추가해줘
이번 달 HRWeb 미리보기 해줘
HRWeb 3월 전체 입력해줘
```

## Gmail 설정

1. Google 계정 → 보안 → 2단계 인증 활성화
2. 앱 비밀번호 생성
3. `dist/config.json`의 `email` 섹션에 입력
