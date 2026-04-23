"""
금융감독원/개인정보보호위원회 제재 정보 일일 스크래퍼
- FSS 검사결과제재:  https://www.fss.or.kr/fss/job/openInfo/list.do?menuNo=200476
- FSS 경영유의사항: https://www.fss.or.kr/fss/job/openInfoImpr/list.do?menuNo=200483
- PIPC 의결결정:   https://www.pipc.go.kr/np/default/agenda.do?mCode=E030010000

실행 방법:
  python main.py

필수 환경변수 (.env 또는 GitHub Secrets):
  GMAIL_USER          - 발신 Gmail 주소
  GMAIL_APP_PASSWORD  - Gmail 앱 비밀번호
  RECIPIENT_EMAILS    - 수신자 이메일 (쉼표 구분, 여러 개 가능)
"""
import logging
import os
import tempfile
from datetime import datetime, timedelta

import pytz
from dotenv import load_dotenv

from scrapers.fss_sanction import FssSanctionScraper
from scrapers.fss_management import FssManagementScraper
from scrapers.pipc_agenda import PipcAgendaScraper
from utils.excel_writer import write_to_excel
from utils.email_sender import send_email

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

KST = pytz.timezone("Asia/Seoul")


def build_email_body(all_data: dict, run_date: datetime) -> str:
    lines = [
        "=" * 60,
        "  금융감독원 / 개인정보보호위원회  제재정보 일일 리포트",
        f"  기준일: {run_date.strftime('%Y년 %m월 %d일 (%A)')}",
        "=" * 60,
        "",
    ]

    total = 0
    for source, rows in all_data.items():
        count = len(rows)
        total += count
        lines.append(f"▶ {source}  →  {count}건")

        for row in rows[:10]:
            org = row.get("제재대상기관") or row.get("제목") or ""
            date = row.get("제재조치요구일") or row.get("의결일") or ""
            dept = row.get("관련부서") or row.get("회의구분") or ""
            lines.append(f"   • {org}  ({date})  [{dept}]")

        if count > 10:
            lines.append(f"   ... 외 {count - 10}건 더 있습니다 (엑셀 첨부 파일 참조)")
        lines.append("")

    lines += [
        f"총 {total}건의 신규 항목이 발견되었습니다.",
        "",
        "* 첨부된 Excel 파일에서 전체 내용을 확인하세요.",
        "* 각 사이트의 첨부 파일도 함께 전송됩니다.",
    ]

    return "\n".join(lines)


def main():
    now = datetime.now(KST)
    yesterday = now - timedelta(days=1)

    logger.info(f"=== 스크래핑 시작: {now.strftime('%Y-%m-%d %H:%M:%S KST')} ===")
    logger.info(f"대상 기간: {yesterday.strftime('%Y-%m-%d')} ~ {now.strftime('%Y-%m-%d')}")

    scrapers = [
        FssSanctionScraper(),
        FssManagementScraper(),
        PipcAgendaScraper(),
    ]

    all_data = {}
    all_attachment_paths = []

    with tempfile.TemporaryDirectory() as tmpdir:
        # 각 사이트 스크래핑
        for scraper in scrapers:
            try:
                logger.info(f"[{scraper.name}] 스크래핑 시작...")
                items, attachments = scraper.scrape(since_date=yesterday, download_dir=tmpdir)
                all_data[scraper.sheet_name] = items
                all_attachment_paths.extend(attachments)
                logger.info(f"[{scraper.name}] 완료: {len(items)}건, 첨부 {len(attachments)}개")
            except Exception as e:
                logger.error(f"[{scraper.name}] 스크래핑 오류: {e}", exc_info=True)
                all_data[scraper.sheet_name] = []

        total_items = sum(len(v) for v in all_data.values())
        logger.info(f"전체 신규 항목: {total_items}건")

        # 신규 항목이 없어도 일일 리포트는 발송 (빈 엑셀)
        excel_filename = f"제재정보_{now.strftime('%Y%m%d')}.xlsx"
        excel_path = os.path.join(tmpdir, excel_filename)
        write_to_excel(all_data, excel_path)

        # 수신자 목록
        recipients_raw = os.environ.get("RECIPIENT_EMAILS", "")
        recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
        if not recipients:
            logger.error("RECIPIENT_EMAILS 환경변수가 설정되지 않았습니다.")
            return

        subject = (
            f"[FSS/PIPC] 제재정보 일일 리포트 - {now.strftime('%Y년 %m월 %d일')} "
            f"(신규 {total_items}건)"
        )
        body = build_email_body(all_data, now)

        attachments_to_send = [excel_path] + all_attachment_paths

        try:
            send_email(
                recipients=recipients,
                subject=subject,
                body=body,
                attachments=attachments_to_send,
            )
            logger.info("=== 이메일 발송 완료 ===")
        except Exception as e:
            logger.error(f"이메일 발송 실패: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    main()
