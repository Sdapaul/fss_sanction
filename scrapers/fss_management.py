"""FSS 금융회사 경영유의사항 등 공시 스크래퍼
https://www.fss.or.kr/fss/job/openInfoImpr/list.do?menuNo=200483
"""
import logging
import os
import re
import time
import urllib.parse
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BASE_URL = "https://www.fss.or.kr"
LIST_URL = f"{BASE_URL}/fss/job/openInfoImpr/list.do"
MENU_NO = "200483"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": BASE_URL,
}


def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _select_rows(soup: BeautifulSoup) -> list:
    for sel in [
        "table.board_list tbody tr",
        "table.tbl_type tbody tr",
        "table tbody tr",
        "tbody tr",
    ]:
        rows = soup.select(sel)
        if rows:
            return rows
    return []


class FssManagementScraper:
    name = "FSS_경영유의사항"
    sheet_name = "경영유의사항"

    def __init__(self):
        self.session = _make_session()

    def scrape(self, since_date: datetime, download_dir: str):
        items = []
        attachments = []

        try:
            self.session.get(f"{BASE_URL}/fss/main.do", timeout=15)
            time.sleep(0.5)
        except Exception:
            pass

        since_str = since_date.strftime("%Y%m%d")
        year_start = f"{since_date.year}-01-01"
        today_str = datetime.now().strftime("%Y-%m-%d")

        page = 1
        while True:
            logger.info(f"  [{self.name}] 페이지 {page} 조회 중...")

            form_data = {
                "menuNo": MENU_NO,
                "pageIndex": str(page),
                "searchBgnDe": year_start,
                "searchEndDe": today_str,
                "searchCondition": "",
                "searchKeyword": "",
            }
            try:
                resp = self.session.post(LIST_URL, data=form_data, timeout=30)
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
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 4:
                    continue

                num = cols[0].get_text(strip=True)
                if not num.isdigit():
                    continue

                org = cols[1].get_text(strip=True)
                date_str = cols[2].get_text(strip=True)
                dept = cols[4].get_text(strip=True) if len(cols) > 4 else ""

                date_clean = re.sub(r"\D", "", date_str)
                if len(date_clean) == 8 and date_clean < since_str:
                    found_old = True
                    continue

                item = {
                    "일련번호": num,
                    "제재대상기관": org,
                    "제재조치요구일": date_str,
                    "제재조치요구내용": "",
                    "관련부서": dept,
                    "상세URL": "",
                    "첨부파일명": "",
                }

                detail_url = self._parse_detail_url(cols)
                if detail_url:
                    item["상세URL"] = detail_url
                    content, file_names, file_paths = self._get_detail(detail_url, download_dir)
                    item["제재조치요구내용"] = content
                    item["첨부파일명"] = "; ".join(file_names)
                    attachments.extend(file_paths)

                items.append(item)
                time.sleep(0.5)

            if found_old:
                break
            if not _has_next_page(soup, page):
                break
            page += 1
            time.sleep(1)

        return items, attachments

    def _parse_detail_url(self, cols) -> str:
        for col in cols:
            a = col.find("a", href=True)
            if not a:
                continue
            href = a["href"]
            if href.startswith("javascript"):
                m = re.search(r"fn_\w+\('?(\d+)'?\)", href)
                if m:
                    return f"{BASE_URL}/fss/job/openInfoImpr/view.do?dataNo={m.group(1)}&menuNo={MENU_NO}"
            elif href.startswith("/"):
                return BASE_URL + href
            elif href.startswith("http"):
                return href
        return ""

    def _get_detail(self, url: str, download_dir: str):
        try:
            time.sleep(0.5)
            resp = self.session.get(url, timeout=30)
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "lxml")

            content = ""
            for sel in [".view_cont", ".board_view_wrap", ".board_view", ".view_area", ".cont_box", "article"]:
                elem = soup.select_one(sel)
                if elem:
                    content = elem.get_text(separator=" ", strip=True)
                    break

            file_names, file_paths = [], []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if not any(k in href for k in ["filedown", "download", "atch", "FileDown", "fileSn"]):
                    continue
                fn = a.get_text(strip=True)
                if not fn:
                    continue
                file_url = (BASE_URL + href) if href.startswith("/") else href
                path = _download_file(self.session, file_url, fn, download_dir, self.sheet_name)
                if path:
                    file_names.append(fn)
                    file_paths.append(path)

            return content, file_names, file_paths
        except Exception as e:
            logger.error(f"상세 페이지 오류 {url}: {e}")
            return "", [], []


def _has_next_page(soup: BeautifulSoup, current_page: int) -> bool:
    for a in soup.select(".paging a, .pagination a, .paginate a, #paging a"):
        text = a.get_text(strip=True)
        if text.isdigit() and int(text) > current_page:
            return True
        if text in ("다음", "next", ">", "▶", "»", "다음페이지"):
            return True
    return False


def _download_file(session: requests.Session, url: str, filename: str, download_dir: str, prefix: str) -> str:
    try:
        resp = session.get(url, timeout=60, stream=True)
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
