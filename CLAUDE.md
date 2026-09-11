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

## 첨부파일 다운로드 시 주의사항 (2026-09-11 버그 3건 수정 후 정리)

리포트 메일이 회사 보안 게이트웨이(Trellix)에 악성으로 오탐된 사건을 조사하며 발견한
사이트별 함정. 첨부 다운로드 로직을 건드릴 때 다시 재현하지 않도록 기록.

- **FSS "문서뷰어" 링크** (`fss_sanction.py`, `fss_management.py`): 상세페이지의
  미리보기 버튼 href가 `pdfViewr`/`viewType=` 파라미터를 포함하는데, 이 안에 원본
  다운로드 경로 문자열이 그대로 박혀 있어 `"download"` 키워드 필터에 우연히 걸린다.
  이 링크는 실제로 **Content-Length: 0인 빈 응답**만 반환하므로 반드시 제외해야 함
  (`href`에 `pdfViewr`/`viewType=` 포함 시 skip).
- **PIPC BBS 첨부** (`pipc_bbs.py`): `atchFileId`를 페이지 전체에서 정규식으로
  "처음 찾은 값 하나"를 모든 첨부에 재사용하면 안 됨 — 페이지 하단 유관기관 배너
  아이콘의 `<img src="...?atchFileId=FILE_xxx">`가 먼저 매칭돼 완전히 무관한 파일을
  받아온다. 반드시 각 `.download` 블록 안 다운로드 버튼의
  `onclick="fn_egov_downFile(atchFileId, fileSn, ext)"`에서 그 첨부 고유의 값을
  직접 파싱할 것 (`pipc_agenda.py`가 쓰는 방식과 동일).
- **Content-Disposition 파일명 파싱** (3개 스크래퍼 공통): `filename="..."` 값 안에
  인코딩 안 된 공백이 섞여 있을 수 있어 공백 기준으로 자르면 확장자가 통째로
  날아간다 — 따옴표 쌍을 우선 매칭할 것. PIPC는 퍼센트 인코딩 없이 원본 UTF-8
  바이트를 헤더에 그대로 실어 보내 HTTP 헤더 latin-1 디코딩 규칙 때문에 파일명이
  깨지므로 `filename.encode("latin-1").decode("utf-8")`로 복원 필요.
