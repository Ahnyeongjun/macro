# macro

Git 커밋 로그를 기능별로 정리하고, Google Calendar 출장 일정을 연동하여  
주간업무보고 엑셀을 자동 생성한 뒤 Gmail로 발송하는 자동화 스크립트입니다.

## 사용법

```bash
python main.py                     # 이번 주 보고서 생성 + 메일 발송
python main.py --date 2025-03-10   # 특정 주의 보고서
python main.py --no-email          # 엑셀만 생성
python main.py --quick             # 수동 입력 없이 빠르게 생성
```

## 설정

`dist/config.json`에서 설정합니다.

```jsonc
{
    "author": "Ahnyeongjun",           // Git 작성자명
    "name": "안영준",                   // 보고서 표시 이름
    "template": "template.xlsx",        // 템플릿 파일명 (dist/ 기준)
    "categories": [
        {
            "name": "APISS",            // 템플릿 C열의 카테고리명과 매칭
            "repos": ["C:\\work\\apiss"] // Git 저장소 경로 배열
        },
        {
            "name": "기타",
            "default_this_week": "월 ~ 수(판교)\n목 ~ 금(대전)"
        },
        {
            "name": "이슈사항",
            "use_calendar": true         // Google Calendar 출장 연동
        }
    ]
}
```

- `categories[].name`만 템플릿의 C열 카테고리명과 일치하면 셀 위치는 자동 계산됩니다.
- `repos`가 있는 카테고리는 Git 커밋 로그를 자동 수집합니다.

## 폴더 구조

```
macro/
├── main.py               # 진입점 + CLI + 오케스트레이션
├── git_collector.py      # Git 커밋 수집 & 기능별 정리
├── excel_writer.py       # 엑셀 XML 조작 + 템플릿 셀 자동 감지
├── email_sender.py       # Gmail SMTP 발송
├── calendar_client.py    # Google Calendar 출장 연동
├── requirements.txt      # 의존성
└── dist/                 # (gitignored) 설정 + 결과물
    ├── config.json
    ├── template.xlsx
    ├── credentials.json  # Google Calendar 인증 (선택)
    └── *.xlsx            # 생성된 보고서
```

## Google Calendar 연동 (선택)

1. [Google Cloud Console](https://console.cloud.google.com) → 프로젝트 생성
2. Google Calendar API 활성화
3. OAuth 2.0 클라이언트 ID (데스크톱 앱) 생성
4. `credentials.json` 다운로드 → `dist/` 에 저장
5. `dist/config.json`에서 `google_calendar.enabled` → `true`
6. 첫 실행 시 브라우저 인증 → 이후 `token.json`으로 자동 갱신

## Gmail 설정

1. Google 계정 → 보안 → 2단계 인증 활성화
2. 앱 비밀번호 생성
3. `dist/config.json`의 `email` 섹션에 입력
