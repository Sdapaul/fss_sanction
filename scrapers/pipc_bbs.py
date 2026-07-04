"""PIPC BBS 타입 제재 공시 스크래퍼
- 결과의 공표 (BS258): 개인정보보호법 제66조 처분 결과 공표
- 공시송달    (BS262): 시정조치 통보·과태료 독촉 공시송달
"""
import logging
import os
import re
import ssl
import time
import urllib.parse
import warnings

import requests
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

warnings.filterwarnings("ignore", message="Unverified HTTPS request")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.pipc.go.kr"
LIST_URL = f"{BASE_URL}/np/cop/bbs/selectBoardList.do"
DETAIL_URL = f"{BASE_URL}/np/cop/bbs/selectBoardArticle.do"
FILE_DOWN_URL = f"{BASE_URL}/np/cmm/fms/FileDown.do"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Connection": "keep-alive",
}


class _TLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    session.verify = False
    adapter = _TLSAdapter(max_retries=Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504]))
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _has_next_page(soup: BeautifulSoup, current_page: int) -> bool:
    for a in soup.select(".paging a, .pagination a, .paginate a, .board_paging a, #paging a"):
        text = a.get_text(strip=True)
        if text.isdigit() and int(text) > current_page:
            return True
        if text in ("다음", "next", ">", "▶", "»", "다음페이지"):
            return True
    return False


class _PipcBbsScraper:
    """결과의공표·공시송달 등 PIPC BBS 공통 스크래퍼."""

    bbs_id: str = ""
    m_code: str = ""
    name: str = ""
    sheet_name: str = ""

    def __init__(self):
        self.session = _make_session()
        self.max_processed_num = 0

    def scrape(self, since_date, download_dir: str, last_num: int = 0):
        from datetime import datetime
        items = []
        attachments = []
        since_str = since_date.strftime("%Y-%m-%d")
        seen = set()
        page = 1

        while True:
            logger.info(f"  [{self.name}] 페이지 {page} 조회 중...")
            params = {"bbsId": self.bbs_id, "mCode": self.m_code, "pageIndex": page}
            try:
                resp = self.session.get(LIST_URL, params=params, timeout=30)
                resp.raise_for_status()
                resp.encoding = "utf-8"
            except Exception as e:
                logger.error(f"  [{self.name}] 페이지 {page} 요청 실패: {e}")
                break

            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("table tbody tr")
            if not rows:
                logger.warning(f"  [{self.name}] 행 없음 (페이지 {page})")
                break

            found_old = False
            total_on_page = 0
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 3:
                    continue

                # nttId를 번호로 사용
                a_tag = row.find("a", href=True)
                if not a_tag:
                    continue
                m = re.search(r"nttId=(\d+)", a_tag["href"])
                if not m:
                    continue
                ntt_id = int(m.group(1))

                if ntt_id in seen:
                    continue
                seen.add(ntt_id)
                total_on_page += 1

                # 날짜/제목/작성부서 추출
                texts = [c.get_text(strip=True) for c in cols]
                title = a_tag.get_text(strip=True) or (texts[1] if len(texts) > 1 else "")
                date_str = next((t for t in texts if re.match(r"\d{4}-\d{2}-\d{2}$", t)), "")
                dept = texts[2] if len(texts) > 2 else ""

                # 번호 기반 추적
                if last_num > 0:
                    if ntt_id <= last_num:
                        found_old = True
                        continue
                else:
                    if date_str and date_str < since_str:
                        found_old = True
                        continue

                self.max_processed_num = max(self.max_processed_num, ntt_id)

                detail_url = f"{DETAIL_URL}?bbsId={self.bbs_id}&mCode={self.m_code}&nttId={ntt_id}"
                content, file_names, file_paths = self._get_detail(detail_url, ntt_id, download_dir)

                item = {
                    "번호": str(ntt_id),
                    "제목": title,
                    "작성부서": dept,
                    "게시일": date_str,
                    "내용": content,
                    "상세URL": detail_url,
                    "첨부파일명": "; ".join(file_names),
                }
                items.append(item)
                attachments.extend(file_paths)
                time.sleep(0.5)

            logger.info(f"  [{self.name}] 페이지 {page}: {total_on_page}행 / 누적 {len(items)}건")
            if found_old:
                break
            if not _has_next_page(soup, page):
                break
            page += 1
            time.sleep(1)

        return items, attachments

    def _get_detail(self, url: str, ntt_id: int, download_dir: str):
        try:
            time.sleep(0.3)
            resp = self.session.get(url, timeout=30)
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "lxml")

            # 본문 텍스트
            content = ""
            view = soup.select_one(".boardViewArea, .board_view, .view_cont")
            if view:
                # 불필요한 탐색 텍스트 제거
                for tag in view.select("caption, .screen_out"):
                    tag.decompose()
                content = view.get_text(separator=" ", strip=True)[:1000]

            # 첨부파일: atchFileId + fileSn 패턴
            file_names, file_paths = [], []
            src = resp.text

            # HTML에서 atchFileId 추출
            atch_ids = re.findall(r'atchFileId["\s]*[=:]["\s]*(FILE_\w+)', src)
            if not atch_ids:
                # hidden input에서 추출
                inp = soup.find("input", {"name": "atchFileId"})
                if inp and inp.get("value"):
                    atch_ids = [inp["value"]]

            # 파일명 목록 추출 (.download 영역)
            file_divs = soup.select(".download, .file_list li, .atch_file li")
            for i, div in enumerate(file_divs):
                fn = div.get_text(strip=True)
                fn = re.sub(r"다운로드|첨부파일.*", "", fn).strip()
                if not fn or len(fn) < 3:
                    continue
                # 파일 확장자 유추
                ext = ""
                ext_m = re.search(r"\.(pdf|hwp|hwpx|xlsx|docx|zip)$", fn, re.IGNORECASE)
                if ext_m:
                    ext = ext_m.group(1).lower()

                if atch_ids:
                    atch_id = atch_ids[0]
                    dl_url = f"{FILE_DOWN_URL}?atchFileId={atch_id}&fileSn={i}&fileExtsn={ext}"
                    path = _download_file(self.session, dl_url, fn, download_dir, self.sheet_name)
                    if path:
                        file_names.append(fn)
                        file_paths.append(path)

            return content, file_names, file_paths
        except Exception as e:
            logger.error(f"  [{self.name}] 상세 오류 {url}: {e}")
            return "", [], []


class PipcResultScraper(_PipcBbsScraper):
    """개인정보보호법 제66조 결과의 공표 (BS258)."""
    bbs_id = "BS258"
    m_code = "C010040000"
    name = "PIPC_결과공표"
    sheet_name = "PIPC_결과공표"


class PipcNoticeScraper(_PipcBbsScraper):
    """시정조치·과태료 공시송달 (BS262)."""
    bbs_id = "BS262"
    m_code = "C010030000"
    name = "PIPC_공시송달"
    sheet_name = "PIPC_공시송달"


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
        logger.error(f"    파일 다운로드 실패 {url}: {e}")
        return ""
