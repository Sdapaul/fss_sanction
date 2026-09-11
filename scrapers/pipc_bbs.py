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

from utils.file_parser import extract_text

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
    adapter = _TLSAdapter(max_retries=Retry(
        total=1,
        connect=1,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        raise_on_status=False,
    ))
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
                resp = self.session.get(LIST_URL, params=params, timeout=(5, 30))
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
                content, file_names, file_paths, att_text = self._get_detail(detail_url, ntt_id, download_dir)

                item = {
                    "번호": str(ntt_id),
                    "제목": title,
                    "작성부서": dept,
                    "게시일": date_str,
                    "내용": content,
                    "상세URL": detail_url,
                    "첨부파일명": "; ".join(file_names),
                    "첨부파일내용": att_text,
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
            resp = self.session.get(url, timeout=(5, 30))
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

            # 첨부파일: 각 ".download" 블록 안 다운로드 버튼의 onclick(fn_egov_downFile(...))에서
            # atchFileId/fileSn/fileExtsn을 직접 추출한다.
            # (과거에는 atchFileId를 "페이지 전체에서 처음 찾은 값 하나"로 모든 첨부에 재사용했는데,
            #  1) 폼 템플릿의 빈 hidden input이 실제 값보다 먼저 나와 못 찾거나,
            #  2) 못 찾으면 정규식이 페이지 하단 유관기관 배너 아이콘의 atchFileId=FILE_xxx 쿼리스트링을
            #     잡아버려 — 완전히 무관한 이미지 파일을 "첨부파일"로 받아 보내는 버그가 있었다.
            #  fileSn도 실제 서버 값이 아니라 목록 순번을 그대로 썼던 것도 함께 버그였음.)
            file_names, file_paths, file_texts = [], [], []
            for div in soup.select(".download"):
                btn = div.find("a", onclick=True)
                if not btn:
                    continue
                m = re.search(
                    r"fn_egov_downFile\w*\('([^']+)',\s*'?(\d+)'?,\s*'([^']+)'\)",
                    btn.get("onclick", ""),
                )
                if not m:
                    continue
                atch_id, sn, ext = m.group(1), m.group(2), m.group(3)

                fn = btn.get("alt") or div.get_text(strip=True)
                fn = re.sub(r"다운로드|첨부파일.*", "", fn).strip()
                if not fn or len(fn) < 3:
                    fn = f"file_{sn}.{ext}"

                dl_url = f"{FILE_DOWN_URL}?atchFileId={atch_id}&fileSn={sn}&fileExtsn={ext}"
                path = _download_file(self.session, dl_url, fn, download_dir, self.sheet_name)
                if path:
                    file_names.append(fn)
                    file_paths.append(path)
                    t = extract_text(path)
                    if t:
                        file_texts.append(t)

            return content, file_names, file_paths, "\n\n---\n\n".join(file_texts)
        except Exception as e:
            logger.error(f"  [{self.name}] 상세 오류 {url}: {e}")
            return "", [], [], ""


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
            # 따옴표로 감싼 filename 안 공백은 [^\s;]+ 로 자르면 확장자가 잘린다 — 따옴표 쌍 우선 매칭
            m = re.search(r'filename\*?=(?:UTF-8\'\')?"([^"]+)"', cd, re.IGNORECASE)
            if not m:
                m = re.search(r"filename\*?=(?:UTF-8'')?([^\s;]+)", cd, re.IGNORECASE)
            if m:
                filename = urllib.parse.unquote(m.group(1).strip("\"'"))
                # PIPC는 퍼센트 인코딩 없이 원본 UTF-8 바이트를 헤더에 그대로 실어 보낸다 —
                # HTTP 헤더는 latin-1로 디코딩되어 파일명이 깨지므로 되돌려서 재해석
                try:
                    filename = filename.encode("latin-1").decode("utf-8")
                except (UnicodeDecodeError, UnicodeEncodeError):
                    pass

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
