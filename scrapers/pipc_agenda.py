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

    retry = Retry(
        total=4,
        connect=4,
        backoff_factor=2,
        status_forcelist=[500, 502, 503, 504],
        raise_on_status=False,
    )
    if not verify_ssl:
        adapter = _TLSAdapter(max_retries=retry)
    else:
        adapter = HTTPAdapter(max_retries=retry)

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


SANCTION_KEYWORDS = ["위반행위", "시정조치", "과징금", "과태료", "시정명령", "처분"]


class PipcAgendaScraper:
    name = "PIPC_의결결정"
    sheet_name = "PIPC의결결정"

    def __init__(self):
        # SSL 검증 활성화로 먼저 시도, 실패 시 비활성화
        self.session = _make_session(verify_ssl=True)
        self._ssl_fallback = False
        self.max_processed_num = 0  # 비제재 포함 처리된 최대 번호

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

    def scrape(self, since_date: datetime, download_dir: str, last_num: int = 0):
        items = []
        attachments = []
        since_str = since_date.strftime("%Y-%m-%d")
        page = 1
        seen_nums = set()

        while True:
            logger.info(f"  [{self.name}] 페이지 {page} 조회 중...")

            params = {"mCode": M_CODE, "pageIndex": page}
            try:
                resp = self._request("GET", LIST_URL, params=params, timeout=(30, 60))
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

                # 중복 항목 방지
                if num in seen_nums:
                    continue
                seen_nums.add(num)
                total_on_page += 1

                meeting_type = cols[1].get_text(strip=True)
                title_col = cols[2]
                title = title_col.get_text(strip=True)
                date_str = cols[3].get_text(strip=True)   # YYYY-MM-DD
                views = cols[5].get_text(strip=True) if len(cols) > 5 else ""

                # 번호 기반 추적 (last_num > 0이면 우선 사용)
                if last_num > 0:
                    if int(num) <= last_num:
                        found_old = True
                        continue
                else:
                    if date_str and date_str < since_str:
                        found_old = True
                        continue

                # 처리된 최대 번호 갱신 (비제재 포함)
                self.max_processed_num = max(self.max_processed_num, int(num))

                # 제재 관련 항목만 수집 (침해요인 평가·정보제공 요청 등 비제재 제외)
                if not any(kw in title for kw in SANCTION_KEYWORDS):
                    logger.debug(f"  [{self.name}] 비제재 항목 제외: {title[:50]}")
                    continue

                item = {
                    "번호": num,
                    "회의구분": meeting_type,
                    "제목": title,
                    "의결일": date_str,
                    "조회수": views,
                    "상세URL": "",
                    "상세내용": "",
                    "첨부파일명": "",
                }

                detail_url = self._parse_detail_url(title_col, cols)
                if detail_url:
                    item["상세URL"] = detail_url
                    content, file_names, file_paths = self._get_detail(detail_url, download_dir)
                    item["상세내용"] = content
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
        for col in [title_col] + list(cols):
            a = col.find("a", href=True)
            if not a:
                continue
            href = a["href"]
            if href.startswith("javascript"):
                continue
            if href.startswith("http"):
                return href
            if href.startswith("/"):
                return BASE_URL + href
            if href.startswith("?"):
                # ?op=view&... 상대 쿼리 → 현재 목록 URL의 경로에 붙임
                return LIST_URL + href
        return ""

    def _get_detail(self, url: str, download_dir: str):
        try:
            time.sleep(0.5)
            resp = self._request("GET", url, timeout=(30, 60))
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "lxml")

            # 상세 내용: tbView 테이블에서 의안번호·공시일(작성일)·본문 추출
            content_parts = []
            body_text = ""
            table = soup.select_one("table.tbView")
            if table:
                for tr in table.find_all("tr"):
                    ths = tr.find_all("th")
                    tds = tr.find_all("td")
                    if not ths:
                        # thead 없이 colspan td만 있는 행 = 실제 본문
                        for td in tds:
                            if td.get("colspan"):
                                txt = td.get_text(separator=" ", strip=True)
                                if len(txt) > 5 and "다운로드" not in txt:
                                    body_text = txt[:300]
                        continue
                    for th, td in zip(ths, tds):
                        key = th.get_text(strip=True)
                        if key in ("의안번호", "의결일", "작성일"):
                            content_parts.append(f"{key}: {td.get_text(strip=True)}")
            if body_text:
                content_parts.append(f"본문: {body_text}")
            content = " | ".join(content_parts)[:800]

            # 첨부파일: fn_egov_downFileCnv onclick에서 파라미터 추출
            file_names, file_paths = [], []
            dl_calls = re.findall(
                r"fn_egov_downFileCnv\('([^']+)',\s*'?(\d+)'?,\s*'([^']+)'\)",
                resp.text
            )
            # 파일명: .download div 텍스트 노드에서 추출
            dl_divs = soup.select(".download > div.download, .download div")
            fn_map: dict[tuple, str] = {}
            for div in dl_divs:
                onclick_a = div.find("a", onclick=True)
                if not onclick_a:
                    continue
                m = re.search(r"fn_egov_downFileCnv\('([^']+)',\s*'?(\d+)'?,\s*'([^']+)'\)", onclick_a.get("onclick", ""))
                if not m:
                    continue
                atch_id, sn, ext = m.group(1), m.group(2), m.group(3)
                # 텍스트 노드에서 파일명 추출 (img 태그 제외)
                fn = ""
                for node in div.children:
                    if hasattr(node, "name") and node.name in ("img", "span", "script"):
                        continue
                    txt = str(node).strip()
                    if txt:
                        fn = txt
                        break
                fn = fn or f"file_{sn}.{ext}"
                fn_map[(atch_id, sn, ext)] = fn

            seen_files: set = set()
            for atch_id, sn, ext in dl_calls:
                key = (atch_id, sn, ext)
                if key in seen_files:
                    continue
                seen_files.add(key)
                fn = fn_map.get(key, f"file_{sn}.{ext}")
                dl_url = f"{BASE_URL}/np/cmm/fms/FileDown.do?atchFileId={atch_id}&fileSn={sn}&fileExtsn={ext}"
                path = _download_file(self.session, dl_url, fn, download_dir, self.sheet_name)
                if path:
                    file_names.append(fn)
                    file_paths.append(path)

            return content, file_names, file_paths
        except Exception as e:
            logger.error(f"상세 페이지 오류 {url}: {e}")
            return "", [], []


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
