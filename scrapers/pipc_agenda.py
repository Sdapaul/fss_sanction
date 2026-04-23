"""개인정보보호위원회 의결·결정 목록 스크래퍼
https://www.pipc.go.kr/np/default/agenda.do?mCode=E030010000
"""
import logging
import os
import re
import ssl
import time
import urllib.parse
import warnings
from datetime import datetime

import requests
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

warnings.filterwarnings("ignore", message="Unverified HTTPS request")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.pipc.go.kr"
LIST_URL = f"{BASE_URL}/np/default/agenda.do"
M_CODE = "E030010000"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


class _TLSAdapter(HTTPAdapter):
    """TLS 1.2 강제 + 낮은 보안 수준 허용 어댑터."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def _make_session(verify_ssl: bool = True) -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    session.verify = verify_ssl

    if not verify_ssl:
        adapter = _TLSAdapter(max_retries=Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504]))
    else:
        adapter = HTTPAdapter(max_retries=Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504]))

    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _select_rows(soup: BeautifulSoup) -> list:
    for sel in [
        "table.board_list tbody tr",
        "table.list_table tbody tr",
        ".board_wrap table tbody tr",
        "table tbody tr",
        "tbody tr",
    ]:
        rows = soup.select(sel)
        if rows:
            return rows
    return []


class PipcAgendaScraper:
    name = "PIPC_의결결정"
    sheet_name = "PIPC의결결정"

    def __init__(self):
        # SSL 검증 활성화로 먼저 시도, 실패 시 비활성화
        self.session = _make_session(verify_ssl=True)
        self._ssl_fallback = False

    def _request(self, method: str, url: str, **kwargs):
        """SSL 오류 시 verify=False 로 자동 재시도."""
        try:
            return self.session.request(method, url, **kwargs)
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
            if not self._ssl_fallback:
                logger.warning(f"SSL/연결 오류, verify=False 로 재시도: {e}")
                self._ssl_fallback = True
                self.session = _make_session(verify_ssl=False)
            return self.session.request(method, url, **kwargs)

    def scrape(self, since_date: datetime, download_dir: str):
        items = []
        attachments = []

        # 홈페이지 방문으로 쿠키 획득
        try:
            self._request("GET", BASE_URL, timeout=15)
            time.sleep(1)
        except Exception as e:
            logger.warning(f"초기 접근 실패: {e}")

        since_str = since_date.strftime("%Y-%m-%d")
        page = 1

        while True:
            logger.info(f"  [{self.name}] 페이지 {page} 조회 중...")

            params = {"mCode": M_CODE, "pageIndex": page}
            try:
                resp = self._request("GET", LIST_URL, params=params, timeout=30)
                resp.raise_for_status()
                resp.encoding = "utf-8"
            except Exception as e:
                logger.error(f"  페이지 {page} 요청 실패: {e}")
                break

            soup = BeautifulSoup(resp.text, "lxml")
            rows = _select_rows(soup)

            if not rows:
                logger.warning(f"  [{self.name}] 행 없음 (페이지 {page}). 응답 미리보기:\n{resp.text[:800]}")
                break

            found_old = False
            total_on_page = 0
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 4:
                    continue

                num = cols[0].get_text(strip=True)
                if not num.isdigit():
                    continue
                total_on_page += 1

                meeting_type = cols[1].get_text(strip=True)
                title_col = cols[2]
                title = title_col.get_text(strip=True)
                date_str = cols[3].get_text(strip=True)   # YYYY-MM-DD
                views = cols[5].get_text(strip=True) if len(cols) > 5 else ""

                if date_str and date_str < since_str:
                    found_old = True
                    continue

                item = {
                    "번호": num,
                    "회의구분": meeting_type,
                    "제목": title,
                    "의결일": date_str,
                    "조회수": views,
                    "상세URL": "",
                    "첨부파일명": "",
                }

                detail_url = self._parse_detail_url(title_col, cols)
                if detail_url:
                    item["상세URL"] = detail_url
                    file_names, file_paths = self._get_detail_attachments(detail_url, download_dir)
                    item["첨부파일명"] = "; ".join(file_names)
                    attachments.extend(file_paths)

                items.append(item)
                time.sleep(0.5)

            logger.info(
                f"  [{self.name}] 페이지 {page}: {total_on_page}행 발견 / 신규 누적 {len(items)}건"
            )
            if total_on_page == 0 and page == 1:
                logger.warning(
                    f"  [{self.name}] 1페이지 파싱 가능 행 없음 — "
                    f"연결/HTML 구조 확인 필요.\n  응답 미리보기:\n{resp.text[:1500]}"
                )
            if found_old:
                break
            if not _has_next_page(soup, page):
                break
            page += 1
            time.sleep(1)

        return items, attachments

    def _parse_detail_url(self, title_col, cols) -> str:
        # 제목 셀 링크 우선
        a = title_col.find("a", href=True)
        if a:
            href = a["href"]
            if href.startswith("/"):
                return BASE_URL + href
            if href.startswith("http"):
                return href
            if not href.startswith("javascript"):
                return BASE_URL + "/" + href

        # 다른 셀에서 링크 탐색
        for col in cols:
            a = col.find("a", href=True)
            if a:
                href = a["href"]
                if href.startswith("/"):
                    return BASE_URL + href
                if href.startswith("http"):
                    return href
        return ""

    def _get_detail_attachments(self, url: str, download_dir: str):
        try:
            time.sleep(0.5)
            resp = self._request("GET", url, timeout=30)
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "lxml")

            file_names, file_paths = [], []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                fn = a.get_text(strip=True)
                if not fn:
                    continue

                is_file = any(k in href for k in ["download", "filedown", "atch", "file"]) or any(
                    href.lower().endswith(ext) for ext in [".pdf", ".hwp", ".hwpx", ".xlsx", ".docx", ".zip"]
                )
                if not is_file:
                    continue

                file_url = (BASE_URL + href) if href.startswith("/") else href
                path = _download_file(self.session, file_url, fn, download_dir, self.sheet_name)
                if path:
                    file_names.append(fn)
                    file_paths.append(path)

            return file_names, file_paths
        except Exception as e:
            logger.error(f"상세 페이지 오류 {url}: {e}")
            return [], []


def _has_next_page(soup: BeautifulSoup, current_page: int) -> bool:
    for a in soup.select(".paging a, .pagination a, .paginate a, .board_paging a, #paging a"):
        text = a.get_text(strip=True)
        if text.isdigit() and int(text) > current_page:
            return True
        if text in ("다음", "next", ">", "▶", "»", "다음페이지"):
            return True
    return False


def _download_file(session: requests.Session, url: str, filename: str, download_dir: str, prefix: str) -> str:
    try:
        resp = session.get(url, timeout=60, stream=True, verify=False)
        resp.raise_for_status()

        cd = resp.headers.get("Content-Disposition", "")
        if cd:
            m = re.findall(r"filename\*?=(?:UTF-8'')?([^\s;]+)", cd, re.IGNORECASE)
            if m:
                filename = urllib.parse.unquote(m[-1].strip("\"'"))

        safe = re.sub(r'[\\/:*?"<>|]', "_", filename).strip() or f"file_{abs(hash(url)) % 100000}"
        path = os.path.join(download_dir, f"{prefix}_{safe}")
        with open(path, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        logger.info(f"    다운로드: {safe}")
        return path
    except Exception as e:
        logger.error(f"파일 다운로드 실패 {url}: {e}")
        return ""
