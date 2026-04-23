# FSS / PIPC 제재정보 일일 스크래퍼

금융감독원(FSS)과 개인정보보호위원회(PIPC)의 제재 공시 정보를 매일 오전 6시(KST)에 자동 수집하여
Excel 파일과 첨부 파일을 이메일로 발송합니다.

## 수집 사이트

| 사이트 | URL |
|--------|-----|
| FSS 검사결과제재 | https://www.fss.or.kr/fss/job/openInfo/list.do?menuNo=200476 |
| FSS 경영유의사항 공시 | https://www.fss.or.kr/fss/job/openInfoImpr/list.do?menuNo=200483 |
| PIPC 의결·결정 | https://www.pipc.go.kr/np/default/agenda.do?mCode=E030010000 |

## GitHub Actions 스케줄

매일 **오전 6시 KST** (= 21:00 UTC) 에 자동 실행됩니다.  
[Actions] 탭에서 **"Run workflow"** 버튼으로 수동 실행도 가능합니다.

## 설정 방법

### 1. Gmail 앱 비밀번호 발급

1. [Google 계정 보안 설정](https://myaccount.google.com/security) 접속
2. **2단계 인증** 활성화
3. **앱 비밀번호** 생성 (앱: 메일, 기기: Windows 컴퓨터)
4. 생성된 16자리 비밀번호 복사

### 2. GitHub Secrets 등록

저장소 → **Settings → Secrets and variables → Actions → New repository secret**

| Secret 이름 | 값 |
|------------|-----|
| `GMAIL_USER` | 발신 Gmail 주소 (예: `sender@gmail.com`) |
| `GMAIL_APP_PASSWORD` | Gmail 앱 비밀번호 (공백 없이 16자리) |
| `RECIPIENT_EMAILS` | 수신자 이메일 (쉼표 구분, 예: `a@mail.com,b@mail.com`) |

### 3. 로컬 테스트 (선택)

```bash
# 1. 가상환경 생성
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 환경변수 설정
cp .env.example .env
# .env 파일을 열어 실제 값으로 수정

# 4. 실행
python main.py
```

## 이메일 형식

- **제목**: `[FSS/PIPC] 제재정보 일일 리포트 - YYYY년 MM월 DD일 (신규 N건)`
- **본문**: 각 사이트별 신규 항목 요약
- **첨부 파일**:
  - `제재정보_YYYYMMDD.xlsx` — 3개 시트 (검사결과제재 / 경영유의사항 / PIPC의결결정)
  - 각 게시물의 원본 첨부 파일 (PDF, HWP, XLSX 등)
