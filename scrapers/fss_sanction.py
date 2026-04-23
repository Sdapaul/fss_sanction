"""FSS 검사결과제재 스크래퍼
https://www.fss.or.kr/fss/job/openInfo/list.do?menuNo=200476
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

BASE_URL = "https://www.fss.or.kr"
LIST_URL = f"{BASE_URL}/fss/job/openInfo/list.do"
MENU_NO = "200476"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": BASE_URL,
}


class FssSanctionScraper:
    name = "FSS_검사결과제재"
    sheet_name = "검사결과제재"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def scrape(self, since_date: datetime, download_dir: str):
        """since_date 이후 항목을 모두 수집하여 (items, attachment_paths) 반환."""
        items = []
        attachments = []
        page = 1

        # 첫 페이지로 세션 초기화
        try:
            self.session.get(f"{LIST_URL}?menuNo={MENU_NO}", timeout=30)
        except Exception as e:
            logger.warning(f"초기 접근 실패: {e}")

        since_str = since_date.strftime("%Y%m%d")

        while True:
            logger.info(f"  [{self.name}] 페이지 {page} 조회 중...")
            params = {"menuNo": MENU_NO, "pageIndex": page}
            try:
                resp = self.session.get(LIST_URL, params=params, timeout=30)
                resp.raise_for_status()
                resp.encoding = "utf-8"
            except Exception as e:
                logger.error(f"  페이지 {page} 요청 실패: {e}")
                break

            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("table tbody tr")
            if not rows:
                logger.info(f"  [{self.name}] 데이터 없음, 종료")
                break

            found_old = False
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 5:
                    continue

                num = cols[0].get_text(strip=True)
                org = cols[1].get_text(strip=True)
                date_str = cols[2].get_text(strip=True)  # YYYYMMDD
                dept = cols[4].get_text(strip=True) if len(cols) > 4 else ""
                views = cols[5].get_text(strip=True) if len(cols) > 5 else ""

                # 날짜 비교
                date_clean = re.sub(r"\D", "", date_str)
                if len(date_clean) == 8 and date_clean < since_str:
                    found_old = True
                    continue

                item = {
                    "번호": num,
                    "제재대상기관": org,
                    "제재조치요구일": date_str,
                    "제재조치요구내용": "",
                    "관련부서": dept,
                    "조회수": views,
                    "상세URL": "",
                    "첨부파일명": "",
                }

                # 상세 링크 탐색 (기관명 링크 또는 내용보기 버튼)
                detail_href = None
                for col in cols:
                    a = col.find("a", href=True)
                    if a:
                        detail_href = a["href"]
                        break

                if detail_href:
                    if detail_href.startswith("/"):
                        detail_url = BASE_URL + detail_href
                    elif detail_href.startswith("javascript"):
                        # javascript:fn_view('123') 패턴 추출
                        m = re.search(r"fn_view\('?(\d+)'?\)", detail_href)
                        if m:
                            detail_url = f"{BASE_URL}/fss/job/openInfo/view.do?dataNo={m.group(1)}&menuNo={MENU_NO}"
                        else:
                            detail_url = ""
                    else:
                        detail_url = detail_href

                    item["상세URL"] = detail_url

                    if detail_url:
                        content, file_names, file_paths = self._get_detail(detail_url, download_dir)
                        item["제재조치요구내용"] = content
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

    def _get_detail(self, url: str, download_dir: str):
        try:
            resp = self.session.get(url, timeout=30)
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "lxml")

            # 본문 내용 추출
            content = ""
            for sel in [".view_cont", ".board_view", ".view_area", ".content", "article", ".sub_cont"]:
                elem = soup.select_one(sel)
                if elem:
                    content = elem.get_text(separator=" ", strip=True)
                    break

            # 첨부파일 링크
            file_names = []
            file_paths = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if any(kw in href for kw in ["filedown", "download", "atch", "file"]):
                    fn = a.get_text(strip=True)
                    if not fn:
                        continue
                    file_url = (BASE_URL + href) if href.startswith("/") else href
                    path = self._download(file_url, fn, download_dir)
                    if path:
                        file_names.append(fn)
                        file_paths.append(path)

            return content, file_names, file_paths
        except Exception as e:
            logger.error(f"상세 페이지 오류 {url}: {e}")
            return "", [], []

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
        for a in soup.select(".paging a, .pagination a, .paginate a"):
            text = a.get_text(strip=True)
            if text.isdigit() and int(text) > current_page:
                return True
            if text in ("다음", "next", ">", "▶"):
                return True
        return False
