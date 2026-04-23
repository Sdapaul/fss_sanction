"""개인정보보호위원회 의결·결정 목록 스크래퍼
https://www.pipc.go.kr/np/default/agenda.do?mCode=E030010000
"""
import logging
import os
import re
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_URL = "https://www.pipc.go.kr"
LIST_URL = f"{BASE_URL}/np/default/agenda.do"
M_CODE = "E030010000"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": BASE_URL,
}


class PipcAgendaScraper:
    name = "PIPC_의결결정"
    sheet_name = "PIPC의결결정"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def scrape(self, since_date: datetime, download_dir: str):
        items = []
        attachments = []
        page = 1

        try:
            self.session.get(f"{LIST_URL}?mCode={M_CODE}", timeout=30)
        except Exception as e:
            logger.warning(f"초기 접근 실패: {e}")

        since_str = since_date.strftime("%Y-%m-%d")

        while True:
            logger.info(f"  [{self.name}] 페이지 {page} 조회 중...")
            params = {"mCode": M_CODE, "pageIndex": page}
            try:
                resp = self.session.get(LIST_URL, params=params, timeout=30)
                resp.raise_for_status()
                resp.encoding = "utf-8"
            except Exception as e:
                logger.error(f"  페이지 {page} 요청 실패: {e}")
                break

            soup = BeautifulSoup(resp.text, "lxml")

            # 테이블 행 탐색 (여러 선택자 시도)
            rows = []
            for sel in ["table.board_list tbody tr", "table tbody tr", ".list_wrap tbody tr"]:
                rows = soup.select(sel)
                if rows:
                    break

            if not rows:
                logger.info(f"  [{self.name}] 데이터 없음")
                break

            found_old = False
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 4:
                    continue

                num = cols[0].get_text(strip=True)
                meeting_type = cols[1].get_text(strip=True)

                # 제목과 링크
                title_col = cols[2]
                title = title_col.get_text(strip=True)
                detail_href = ""
                a = title_col.find("a", href=True)
                if a:
                    detail_href = a["href"]

                # 의결일 (YYYY-MM-DD 형식)
                date_str = cols[3].get_text(strip=True)
                views = cols[5].get_text(strip=True) if len(cols) > 5 else ""

                # 날짜 비교
                date_clean = date_str.strip()
                if date_clean and date_clean < since_str:
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

                if detail_href:
                    if detail_href.startswith("/"):
                        detail_url = BASE_URL + detail_href
                    elif detail_href.startswith("http"):
                        detail_url = detail_href
                    else:
                        detail_url = BASE_URL + "/" + detail_href

                    item["상세URL"] = detail_url
                    file_names, file_paths = self._get_detail_attachments(detail_url, download_dir)
                    item["첨부파일명"] = "; ".join(file_names)
                    attachments.extend(file_paths)

                items.append(item)
                time.sleep(0.5)

            if found_old:
                break

            if not self._has_next_page(soup, page):
                break
            page += 1
            time.sleep(1)

        return items, attachments

    def _get_detail_attachments(self, url: str, download_dir: str):
        try:
            resp = self.session.get(url, timeout=30)
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "lxml")

            file_names = []
            file_paths = []

            for a in soup.find_all("a", href=True):
                href = a["href"]
                fn = a.get_text(strip=True)
                if not fn:
                    continue

                is_file_link = any(
                    kw in href for kw in ["download", "filedown", "atch", "file", ".pdf", ".hwp", ".xlsx", ".docx"]
                )
                if not is_file_link:
                    continue

                if href.startswith("/"):
                    file_url = BASE_URL + href
                elif href.startswith("http"):
                    file_url = href
                else:
                    continue

                path = self._download(file_url, fn, download_dir)
                if path:
                    file_names.append(fn)
                    file_paths.append(path)

            return file_names, file_paths
        except Exception as e:
            logger.error(f"상세 페이지 오류 {url}: {e}")
            return [], []

    def _download(self, url: str, filename: str, download_dir: str):
        try:
            resp = self.session.get(url, timeout=60, stream=True)
            resp.raise_for_status()

            cd = resp.headers.get("Content-Disposition", "")
            if cd:
                m = re.findall(r"filename\*?=(?:UTF-8'')?([^\s;]+)", cd, re.IGNORECASE)
                if m:
                    filename = urllib.parse.unquote(m[-1].strip('"\''))

            safe_name = re.sub(r'[\\/:*?"<>|]', "_", filename).strip()
            if not safe_name:
                safe_name = f"file_{abs(hash(url)) % 100000}"

            path = os.path.join(download_dir, f"{self.sheet_name}_{safe_name}")
            with open(path, "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
            logger.info(f"    다운로드 완료: {safe_name}")
            return path
        except Exception as e:
            logger.error(f"파일 다운로드 실패 {url}: {e}")
            return None

    @staticmethod
    def _has_next_page(soup: BeautifulSoup, current_page: int) -> bool:
        for a in soup.select(".paging a, .pagination a, .paginate a, .board_paging a"):
            text = a.get_text(strip=True)
            if text.isdigit() and int(text) > current_page:
                return True
            if text in ("다음", "next", ">", "▶", "»"):
                return True
        return False
