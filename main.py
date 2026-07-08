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
import html
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta

import pytz
from dotenv import load_dotenv

from scrapers.fss_sanction import FssSanctionScraper
from scrapers.fss_management import FssManagementScraper
from scrapers.pipc_agenda import PipcAgendaScraper
from scrapers.pipc_bbs import PipcResultScraper, PipcNoticeScraper
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
STATE_FILE = "state.json"


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"state.json 읽기 실패: {e}")
    return {}


def save_state(state: dict):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"state.json 저장 실패: {e}")


# 소스별 메타 정보: 목록 URL · 컬럼 정의 · 링크 키
_SOURCE_META = {
    "검사결과제재": {
        "list_url": "https://www.fss.or.kr/fss/job/openInfo/list.do?menuNo=200476",
        "source_name": "금융감독원 검사결과제재",
        "columns": ["번호", "제재대상기관", "제재조치요구일", "관련부서"],
        "url_key": "상세URL",
        "content_key": "제재조치요구내용",
    },
    "경영유의사항": {
        "list_url": "https://www.fss.or.kr/fss/job/openInfoImpr/list.do?menuNo=200483",
        "source_name": "금융감독원 경영유의사항",
        "columns": ["일련번호", "제재대상기관", "제재조치요구일", "관련부서"],
        "url_key": "상세URL",
        "content_key": "제재조치요구내용",
    },
    "PIPC의결결정": {
        "list_url": "https://www.pipc.go.kr/np/default/agenda.do?mCode=E030010000",
        "source_name": "개인정보보호위원회 의결·결정",
        "columns": ["번호", "회의구분", "제목", "의결일"],
        "url_key": "상세URL",
        "title_key": "제목",
        "content_key": "상세내용",
    },
    "PIPC_결과공표": {
        "list_url": "https://www.pipc.go.kr/np/cop/bbs/selectBoardList.do?bbsId=BS258&mCode=C010040000",
        "source_name": "개인정보보호위원회 결과의 공표",
        "columns": ["번호", "제목", "작성부서", "게시일"],
        "url_key": "상세URL",
        "title_key": "제목",
        "content_key": "내용",
    },
    "PIPC_공시송달": {
        "list_url": "https://www.pipc.go.kr/np/cop/bbs/selectBoardList.do?bbsId=BS262&mCode=C010030000",
        "source_name": "개인정보보호위원회 공시송달",
        "columns": ["번호", "제목", "작성부서", "게시일"],
        "url_key": "상세URL",
        "title_key": "제목",
        "content_key": "내용",
    },
}


def build_email_body(all_data: dict, run_date: datetime) -> str:
    """플레인텍스트 이메일 본문 (HTML 미지원 클라이언트용 fallback)."""
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
        meta = _SOURCE_META.get(source, {})
        list_url = meta.get("list_url", "")
        source_name = meta.get("source_name", source)
        lines.append(f"▶ {source_name}  →  {count}건")
        if list_url:
            lines.append(f"   출처: {list_url}")

        for row in rows:
            org = row.get("제재대상기관") or row.get("제목") or ""
            date = row.get("제재조치요구일") or row.get("의결일") or row.get("게시일") or ""
            dept = row.get("관련부서") or row.get("회의구분") or row.get("작성부서") or ""
            detail_url = row.get("상세URL", "")
            line = f"   • {org}  ({date})  [{dept}]"
            if detail_url:
                line += f"\n     {detail_url}"
            lines.append(line)

        lines.append("")

    lines += [
        f"총 {total}건의 신규 항목이 발견되었습니다.",
        "",
        "* 첨부된 Excel 파일에서 전체 내용을 확인하세요.",
        "* 각 사이트의 첨부 파일도 함께 전송됩니다.",
    ]

    return "\n".join(lines)


def build_email_html(all_data: dict, run_date: datetime) -> str:
    """HTML 이메일 본문: 소스별 출처 링크 + 상세URL 클릭 가능 테이블."""
    total = sum(len(v) for v in all_data.values())
    date_str = run_date.strftime("%Y년 %m월 %d일")

    parts = [
        '<!DOCTYPE html>',
        '<html lang="ko">',
        '<head><meta charset="utf-8"></head>',
        '<body style="font-family:\'Malgun Gothic\',\'Apple SD Gothic Neo\',sans-serif;'
        'background:#f4f6f9;margin:0;padding:20px;">',
        '<div style="max-width:920px;margin:0 auto;background:#fff;border-radius:8px;'
        'overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.12);">',

        # 헤더
        '<div style="background:#003366;color:#fff;padding:22px 32px;">',
        '<h1 style="margin:0;font-size:19px;font-weight:bold;">',
        '금융감독원 / 개인정보보호위원회 &nbsp;제재정보 일일 리포트</h1>',
        f'<p style="margin:8px 0 0;font-size:13px;opacity:.85;">'
        f'{date_str} &nbsp;·&nbsp; 신규 <strong>{total}건</strong></p>',
        '</div>',

        '<div style="padding:24px 32px;">',
    ]

    for sheet_name, rows in all_data.items():
        if not rows:
            continue

        meta = _SOURCE_META.get(sheet_name, {})
        list_url = meta.get("list_url", "")
        source_name = meta.get("source_name", sheet_name)
        columns = meta.get("columns", list(rows[0].keys())[:4])
        url_key = meta.get("url_key", "상세URL")
        title_key = meta.get("title_key", "")
        content_key = meta.get("content_key", "")

        # 섹션 헤더
        parts.append('<div style="margin-bottom:28px;">')
        parts.append(
            '<h2 style="margin:0 0 10px;font-size:15px;color:#003366;'
            'border-bottom:2px solid #003366;padding-bottom:8px;">'
        )
        if list_url:
            parts.append(
                f'<a href="{html.escape(list_url)}" style="color:#003366;text-decoration:none;">'
                f'{html.escape(source_name)}</a>'
            )
        else:
            parts.append(html.escape(source_name))
        parts.append(
            f' <span style="font-size:12px;font-weight:normal;color:#666;">({len(rows)}건)</span>'
            '</h2>'
        )

        # 테이블
        parts.append(
            '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        )

        # 컬럼 헤더
        th_style = ('padding:8px 10px;text-align:left;border:1px solid #d0d7e3;'
                    'background:#e8eef5;white-space:nowrap;')
        parts.append('<thead><tr>')
        for col in columns:
            parts.append(f'<th style="{th_style}">{html.escape(col)}</th>')
        parts.append(f'<th style="{th_style}text-align:center;">상세</th>')
        parts.append('</tr></thead>')

        # 데이터 행
        parts.append('<tbody>')
        for i, row in enumerate(rows):
            bg = '#fff' if i % 2 == 0 else '#f7f9fc'
            parts.append(f'<tr style="background:{bg};">')
            detail_url = row.get(url_key, "")
            td_style = 'padding:7px 10px;border:1px solid #e8e8e8;'

            for col in columns:
                val = html.escape(str(row.get(col, "")))
                # title_key 컬럼은 값 자체를 링크로 표시
                if col == title_key and detail_url:
                    parts.append(
                        f'<td style="{td_style}">'
                        f'<a href="{html.escape(detail_url)}" style="color:#0055cc;">{val}</a>'
                        '</td>'
                    )
                else:
                    parts.append(f'<td style="{td_style}">{val}</td>')

            # 상세보기 버튼 열
            if detail_url:
                parts.append(
                    f'<td style="{td_style}text-align:center;">'
                    f'<a href="{html.escape(detail_url)}" '
                    'style="color:#0055cc;text-decoration:none;font-size:12px;'
                    'background:#e8f0fe;padding:3px 9px;border-radius:3px;white-space:nowrap;">'
                    '상세보기</a></td>'
                )
            else:
                parts.append(
                    f'<td style="{td_style}text-align:center;color:#aaa;">-</td>'
                )

            parts.append('</tr>')

            # 내용 서브 행: 페이지 본문 + 첨부파일 텍스트
            page_text = (row.get(content_key, "") or "").strip() if content_key else ""
            att_text = (row.get("첨부파일내용", "") or "").strip()
            if page_text or att_text:
                num_cols = len(columns) + 1
                sub_td = (
                    f'padding:6px 14px 10px 14px;border:1px solid #e8e8e8;'
                    f'border-top:none;background:#f9fafc;font-size:12px;color:#444;'
                )
                parts.append(f'<tr><td colspan="{num_cols}" style="{sub_td}">')
                if page_text:
                    snippet = html.escape(page_text[:400]) + ("…" if len(page_text) > 400 else "")
                    parts.append(
                        '<div style="margin-bottom:6px;">'
                        '<span style="font-weight:bold;color:#555;font-size:11px;">페이지 내용</span><br>'
                        f'<span style="white-space:pre-wrap;">{snippet}</span>'
                        '</div>'
                    )
                if att_text:
                    snippet = html.escape(att_text[:800]) + ("…" if len(att_text) > 800 else "")
                    parts.append(
                        '<div>'
                        '<span style="font-weight:bold;color:#555;font-size:11px;">첨부파일 내용</span><br>'
                        f'<span style="white-space:pre-wrap;">{snippet}</span>'
                        '</div>'
                    )
                parts.append('</td></tr>')

        parts.append('</tbody></table>')
        parts.append('</div>')  # end section

    # 푸터
    parts += [
        '<div style="margin-top:20px;padding-top:14px;border-top:1px solid #eee;'
        'font-size:12px;color:#888;">',
        f'<p style="margin:0;">총 {total}건의 신규 항목 &nbsp;·&nbsp; '
        '첨부된 Excel 파일에서 전체 내용을 확인하세요.</p>',
        '</div>',
        '</div>',   # end padding
        '</div>',   # end container
        '</body></html>',
    ]

    return "\n".join(parts)


def main():
    now = datetime.now(KST)
    lookback_days = int(os.environ.get("LOOKBACK_DAYS", "1"))
    since_date = now - timedelta(days=lookback_days)

    logger.info(f"=== 스크래핑 시작: {now.strftime('%Y-%m-%d %H:%M:%S KST')} ===")
    logger.info(f"대상 기간: {since_date.strftime('%Y-%m-%d')} ~ {now.strftime('%Y-%m-%d')} (최근 {lookback_days}일, LOOKBACK_DAYS={lookback_days})")

    state = load_state()
    logger.info(f"이전 수집 상태: {state}")

    scrapers = [
        FssSanctionScraper(),
        FssManagementScraper(),
        PipcAgendaScraper(),
        PipcResultScraper(),
        PipcNoticeScraper(),
    ]

    all_data = {}
    all_attachment_paths = []

    with tempfile.TemporaryDirectory() as tmpdir:
        # 각 사이트 스크래핑
        for scraper in scrapers:
            last_num = state.get(scraper.name, 0)
            try:
                logger.info(f"[{scraper.name}] 스크래핑 시작... (last_num={last_num})")
                items, attachments = scraper.scrape(since_date=since_date, download_dir=tmpdir, last_num=last_num)
                all_data[scraper.sheet_name] = items
                all_attachment_paths.extend(attachments)
                logger.info(f"[{scraper.name}] 완료: {len(items)}건, 첨부 {len(attachments)}개")
            except Exception as e:
                logger.error(f"[{scraper.name}] 스크래핑 오류: {e}", exc_info=True)
                all_data[scraper.sheet_name] = []

        total_items = sum(len(v) for v in all_data.values())
        logger.info(f"전체 신규 항목: {total_items}건")

        # 수집된 항목으로 state 업데이트 (이메일 발송 여부와 무관하게 저장)
        new_state = dict(state)
        for scraper in scrapers:
            # 비제재 포함 처리된 최대 번호가 있으면 우선 사용 (PIPC)
            if getattr(scraper, "max_processed_num", 0) > 0:
                new_state[scraper.name] = scraper.max_processed_num
                continue
            rows = all_data.get(scraper.sheet_name, [])
            if rows:
                num_key = "번호" if "번호" in rows[0] else "일련번호"
                nums = [int(r[num_key]) for r in rows if str(r.get(num_key, "")).isdigit()]
                if nums:
                    new_state[scraper.name] = max(nums)
        save_state(new_state)
        logger.info(f"수집 상태 저장: {new_state}")

        if total_items == 0:
            logger.info("신규 제재 내역이 없습니다. 이메일 발송을 건너뜁니다.")
            return

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
        html_body = build_email_html(all_data, now)

        attachments_to_send = [excel_path] + all_attachment_paths

        try:
            send_email(
                recipients=recipients,
                subject=subject,
                body=body,
                attachments=attachments_to_send,
                html_body=html_body,
            )
            logger.info("=== 이메일 발송 완료 ===")
        except Exception as e:
            logger.error(f"이메일 발송 실패: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    main()
