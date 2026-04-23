# CLAUDE.md — 프로젝트 컨텍스트

## 프로젝트 목적

금융감독원(FSS)과 개인정보보호위원회(PIPC)의 제재 공시를 매일 자동 수집해 Excel + 첨부파일로 이메일 발송.

## 수집 대상 URL

| 변수명 / 클래스 | URL |
|----------------|-----|
| `FssSanctionScraper` | https://www.fss.or.kr/fss/job/openInfo/list.do?menuNo=200476 |
| `FssManagementScraper` | https://www.fss.or.kr/fss/job/openInfoImpr/list.do?menuNo=200483 |
| `PipcAgendaScraper` | https://www.pipc.go.kr/np/default/agenda.do?mCode=E030010000 |

## 파일 구조

```
fss_sanction/
├── main.py                          # 진입점 — 스크래핑 → Excel → 이메일 순으로 실행
├── scrapers/
│   ├── fss_sanction.py              # FSS 검사결과제재 스크래퍼
│   ├── fss_management.py            # FSS 경영유의사항 스크래퍼
│   └── pipc_agenda.py               # PIPC 의결결정 스크래퍼
├── utils/
│   ├── excel_writer.py              # openpyxl — 시트별 Excel 생성
│   └── email_sender.py              # Gmail SMTP (App Password)
├── .github/workflows/daily_scrape.yml  # 매일 21:00 UTC = 06:00 KST
├── requirements.txt
├── .env.example                     # 로컬 테스트용 환경변수 템플릿
├── 사용법.md                        # 한국어 사용자 가이드
└── README.md                        # GitHub 프로젝트 설명
```

## 스크래퍼 공통 인터페이스

모든 스크래퍼는 동일한 시그니처를 가집니다:

```python
def scrape(self, since_date: datetime, download_dir: str) -> tuple[list[dict], list[str]]:
    """
    since_date 이후 신규 항목 수집.
    반환: (rows, attachment_paths)
      rows              — dict 리스트 (각 키가 Excel 컬럼명)
      attachment_paths  — 다운로드된 첨부파일 로컬 경로 리스트
    """
```

`since_date`는 KST 기준 `datetime`이며, 일반적으로 `now - timedelta(days=1)`.

## 날짜 처리 규칙

- FSS 사이트: 날짜가 `YYYYMMDD` 8자리 정수 문자열 (예: `20260420`)
- PIPC 사이트: 날짜가 `YYYY-MM-DD` 문자열 (예: `2026-04-08`)
- 비교는 문자열 사전순 비교로 충분 (`"20260420" < "20260423"`)
- 페이지 순회 중 `since_date` 이전 항목을 만나면 해당 페이지에서 중단 (`found_old = True`)

## 환경변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `GMAIL_USER` | O | 발신 Gmail 주소 |
| `GMAIL_APP_PASSWORD` | O | Gmail 앱 비밀번호 (16자리, 공백 없이) |
| `RECIPIENT_EMAILS` | O | 수신자 이메일, 쉼표 구분 |

로컬: `.env` 파일 (python-dotenv로 자동 로드)  
GitHub Actions: Repository Secrets

## GitHub Actions 스케줄

```yaml
cron: "0 21 * * *"   # UTC 21:00 = KST 06:00
```

`workflow_dispatch` 트리거도 설정되어 있어 수동 실행 가능.

## 의존성

```
requests          HTTP 요청
beautifulsoup4    HTML 파싱
lxml              BeautifulSoup 파서
openpyxl          Excel 생성
pytz              KST 타임존 처리
python-dotenv     로컬 .env 로드
```

## 주의사항

- `.env` 파일은 `.gitignore`에 포함 — 절대 커밋하지 않음
- 각 요청 사이 `time.sleep(0.5~1)` 적용 — 서버 부하 방지
- 첨부파일 다운로드는 `tempfile.TemporaryDirectory` 안에서 처리 — 실행 후 자동 삭제
- 사이트 HTML 구조가 바뀌면 각 스크래퍼의 CSS 선택자 수정 필요
- `_has_next_page()` 는 `.paging`, `.pagination`, `.paginate` 클래스 기반 — 변경 시 업데이트
