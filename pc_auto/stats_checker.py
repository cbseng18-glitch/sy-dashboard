#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stats_checker.py — 집 PC(C:\\sy_auto) 통합 경량 조회기
=====================================================

만든 목적
  인1(김승용22 / @kimseungyong22)이 브라우저·사용량을 쓰지 않고도 확인하던
  세 가지 정기 점검을 집 PC(상시 가동)에서 0원으로 돌리기 위한 스크립트.

  1) 쇼피 8개국 실시간 유입 + 무료 부스트 슬롯/쿨다운 조회
     - `work/shopee-api/README.md`의 셀러센터 JSON API 그대로 사용
     - 쿠키 파일(`shopee_cookies.json`)이 없거나 만료면 **죽지 않고** `cookie_expired`만 보고
  2) 큐텐 재팬 공개 상품 페이지 5종 HTTP 상태·판매상태·가격 노출 확인
  3) 오늘/최근 당첨 발표 예정 설문 페이지 키워드 경량 체크
     (`당첨자`, `발표`, `김승용`, `8972`)

  결과는 `pc_stats.json` 로컬 저장 + (옵션) GitHub `cbseng18-glitch/sy-dashboard` 푸시
  + (옵션) 텔레그램 요약.

의존성
  Python 3.7+ 표준 라이브러리만 사용. `pip install` 불필요.
  (선택) `curl_cffi` 가 설치돼 있으면 큐텐 조회에 자동으로 먼저 사용한다.

큐텐 재팬 HTTP 엔진 (v1.1.0)
  `urllib` 의 TLS 지문 때문에 Akamai 가 523/403 을 돌려주므로, 큐텐만 아래 순서로 시도한다.
    1) curl_cffi (설치된 경우, Chrome 지문 위장)
    2) Windows 10/11 기본 내장 `curl.exe` (Schannel TLS — 집 PC에서 Akamai 통과율 높음)
    3) 기존 urllib (최종 폴백)
  curl 경로는 `--max-time 15`, `-L` 리다이렉트 추적, UTF-8 본문, `%{http_code}` 상태코드 파싱,
  헤더 덤프로 charset 확인까지 처리한다. config `qoo10.http_engine` 으로 고정 가능
  (`auto` 기본 | `curl` | `cffi` | `urllib`).

사용 예
  python stats_checker.py --dry-run        # 네트워크 조회는 하되 저장·전송 안 함(검증용)
  python stats_checker.py                  # 실제 실행(로컬 저장 + config에서 켜면 GitHub 푸시)
  python stats_checker.py --telegram       # 텔레그램 요약까지
  python stats_checker.py --self-test      # 네트워크 없이 파서·판정 로직 자체 점검(로컬 목업 서버로 curl 경로까지)
  python stats_checker.py --fetch-test     # 큐텐 1건으로 사용 가능한 HTTP 엔진 실측(집 PC 확인용)
  python stats_checker.py --print-json     # 감시 스크립트용 결과 JSON 한 줄 출력

토큰·쿠키는 코드에 넣지 않는다. `config.json` 또는 환경변수에서만 읽는다.
  GITHUB_TOKEN / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / SY_CURL_EXE(선택: curl.exe 경로 지정)
"""

from __future__ import print_function

import argparse
import base64
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1.1.0"
KST = datetime.timezone(datetime.timedelta(hours=9))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 sy_auto-stats-checker/" + VERSION)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# 쇼피 8개국 (work/shopee-api/README.md 1절 그대로)
# ---------------------------------------------------------------------------
REGIONS = [
    # (지역, 샵명, cnsc_shop_id, cbsc_shop_region)
    ("SG", "syglobal.sg", "1938185445", "sg"),
    ("TW", "syglobalwl", "1938234251", "tw"),
    ("MY", "syglobaldx.my", "1938234247", "my"),
    ("PH", "syglobalev.ph", "1938234248", "ph"),
    ("VN", "syglobalkd.vn", "1938234249", "vn"),
    ("TH", "syglobalen.th", "1938234250", "th"),
    ("MX", "syglobaldd.mx", "1938234253", "mx"),
    ("BR", "syglobalne.br", "1938234252", "br"),
]

PATH_REALTIME = "/api/mydata/krsc/shop/v2/campaign_board/realtime_metrics/"
PATH_BUMPED = "/api/v3/opt/mpsku/list/get_bumped_product_list"

# 큐텐 재팬 5호 (2026-11-26 21:15 실측 기준 가격)
QOO10_PRODUCTS = [
    {"no": "1224104135", "label": "1호 다꾸세트", "expect_price": 3280},
    {"no": "1224104228", "label": "2호 산리오세트", "expect_price": 2980},
    {"no": "1224104377", "label": "3호 K-POP 키트", "expect_price": 2980},
    {"no": "1224104455", "label": "4호 스터디세트", "expect_price": 3280},
    {"no": "1224104483", "label": "5호 폰꾸세트", "expect_price": 3480},
]

# 당첨 발표 감시 기본 대상 (config.json 에서 자유롭게 교체·추가)
SURVEY_WATCH = [
    {"name": "모두가 도시농부(모두농)", "url": "https://modunong.or.kr",
     "note": "「모두가 도시농부」 만족도 조사 발표 11-27"},
    {"name": "비짓부산 공지", "url": "https://www.visitbusan.net/kor/board/list.do?menuCd=DOM_000000101001001000",
     "note": "비짓부산 만족도 조사 발표 11-27"},
    {"name": "비짓부산 홈", "url": "https://www.visitbusan.net",
     "note": "비짓부산 홈페이지 상단 공지"},
]

DEFAULT_KEYWORDS = ["당첨자", "당첨", "발표", "김승용", "8972"]

DEFAULT_CONFIG = {
    "output_path": "pc_stats.json",
    "state_path": "pc_stats_state.json",
    "last_run_path": "last_run_stats.json",
    "shopee": {
        "enabled": True,
        "base_url": "https://seller.shopee.kr",
        "cookies_path": "shopee_cookies.json",
        "timeout": 20,
        "delay_seconds": 0.5,
        "collect_boost": True,
    },
    "qoo10": {
        "enabled": True,
        "timeout": 25,
        "http_engine": "auto",        # auto | curl | cffi | urllib (auto: curl_cffi→curl.exe→urllib)
        "curl_timeout": 15,           # curl/curl_cffi --max-time (brief: 15초)
        "url_template": "https://www.qoo10.jp/g/{no}",
        "products": QOO10_PRODUCTS,
    },
    "survey_watch": {
        "enabled": True,
        "keywords": DEFAULT_KEYWORDS,
        "targets": SURVEY_WATCH,
        "history_path": "in1/history.json",
        "lookahead_days": 0,        # 0=오늘만, 1=내일까지
    },
    "github": {
        "enabled": False, "owner": "cbseng18-glitch", "repo": "sy-dashboard",
        "path": "pc_stats.json", "branch": "main",
        "token_env": "GITHUB_TOKEN", "commit_message": "chore: PC 통합 조회(pc_stats) 갱신",
        "keep_snapshots": 24, "redact_personal": True,
    },
    "telegram": {"enabled": False, "token_env": "TELEGRAM_BOT_TOKEN",
                 "chat_id_env": "TELEGRAM_CHAT_ID"},
}


# ---------------------------------------------------------------------------
# 로그 · 시간
# ---------------------------------------------------------------------------

class Log(object):
    def __init__(self, quiet=False):
        self.quiet = quiet
        self.lines = []

    def _emit(self, level, msg):
        line = "[%s] %s" % (level, msg)
        self.lines.append(line)
        if not self.quiet:
            print(line)
            try:
                sys.stdout.flush()
            except Exception:
                pass

    def info(self, msg):
        self._emit("INFO", msg)

    def ok(self, msg):
        self._emit("OK", msg)

    def warn(self, msg):
        self._emit("WARN", msg)

    def error(self, msg):
        self._emit("ERROR", msg)


def _now():
    return datetime.datetime.now(KST)


def _iso(dt=None):
    return (dt or _now()).astimezone(KST).isoformat(timespec="seconds")


def _hhmm(dt):
    return dt.astimezone(KST).strftime("%m-%d %H:%M")


def _parse_iso(text):
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# HTTP (표준 라이브러리만)
# ---------------------------------------------------------------------------

def http_request(url, method="GET", data=None, headers=None, timeout=25,
                 retries=2, log=None, max_bytes=2 * 1024 * 1024):
    """돌려주는 값: ok / status / body(bytes) / error / content_type / url(최종)"""
    hdrs = {
        "User-Agent": UA,
        "Accept-Language": "ko-KR,ko;q=0.9,ja;q=0.8,en;q=0.5",
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
    }
    if headers:
        hdrs.update(headers)
    last = {"ok": False, "status": None, "body": b"", "error": "not executed",
            "content_type": "", "url": url}
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(max_bytes)
                return {"ok": True, "status": getattr(resp, "status", 200), "body": body,
                        "error": None, "content_type": resp.headers.get("Content-Type", ""),
                        "url": resp.geturl() or url}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read()[:400].decode("utf-8", "replace")
            except Exception:
                pass
            last = {"ok": False, "status": exc.code, "body": b"",
                    "error": "HTTP %s %s" % (exc.code, " ".join(detail.split())[:200]),
                    "content_type": "", "url": url}
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                if log:
                    log.warn("HTTP %s, %d초 후 재시도: %s" % (exc.code, 2 * (attempt + 1), url))
                time.sleep(2 * (attempt + 1))
                continue
            return last
        except Exception as exc:  # URLError, timeout, ssl 등
            last = {"ok": False, "status": None, "body": b"",
                    "error": "%s: %s" % (type(exc).__name__, exc),
                    "content_type": "", "url": url}
            if attempt < retries:
                if log:
                    log.warn("%s, 재시도 %d/%d: %s" % (type(exc).__name__, attempt + 1, retries, url))
                time.sleep(1.5 * (attempt + 1))
                continue
            return last
    return last


def _charset(result):
    m = re.search(r"charset=([\w\-]+)", (result.get("content_type") or ""), re.I)
    if m:
        return m.group(1).lower()
    body = result.get("body") or b""
    m = re.search(br"charset=[\"']?([\w\-]+)", body[:4096], re.I)
    if m:
        return m.group(1).decode("ascii", "replace").lower()
    return ""


def decode_body(result):
    charset = _charset(result)
    raw = result.get("body") or b""
    order = [charset, "utf-8", "cp949", "euc-kr", "shift_jis"]
    for enc in order:
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


META_REFRESH_RE = re.compile(
    r"""<meta[^>]+http-equiv=["']?refresh["']?[^>]*content=["']?\s*\d+\s*;\s*url=([^"'>]+)""", re.I)


def fetch_html(url, timeout=25, referer=None, log=None, follow_meta=2):
    """meta refresh 까지 따라가며 HTML 텍스트를 가져온다(경량 GET)."""
    headers = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
    if referer:
        headers["Referer"] = referer
    res = http_request(url, headers=headers, timeout=timeout, retries=1, log=log)
    hops = []
    for _ in range(follow_meta):
        if not res.get("ok"):
            break
        html = decode_body(res)
        m = META_REFRESH_RE.search(html)
        if not m:
            return res, html, hops
        target = urllib.parse.urljoin(res.get("url") or url, m.group(1).strip())
        hops.append(target)
        if log:
            log.info("meta refresh 따라감: %s" % target)
        res = http_request(target, headers=headers, timeout=timeout, retries=1, log=log)
    return res, (decode_body(res) if res.get("ok") else ""), hops


# ---------------------------------------------------------------------------
# HTTP 대체 엔진 (curl_cffi → Windows curl.exe → urllib)
# ---------------------------------------------------------------------------
#   urllib 의 TLS 지문(JA3)은 Akamai 가 523 으로 잘라낸다. 집 PC(Windows)의
#   내장 curl.exe 는 Schannel TLS 라 지문이 달라 통과하는 경우가 많다.

CURL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CURL_DEFAULT_TIMEOUT = 15
_CURL_CACHE = {}
_CFFI_CACHE = {}  # {loaded: bool, module: module|None}


def find_curl_exe(refresh=False):
    """curl 실행 파일 경로. 없으면 None.

    우선순위: 환경변수 SY_CURL_EXE → Windows System32\\curl.exe → PATH 의 curl(.exe)
    """
    if not refresh and "path" in _CURL_CACHE:
        return _CURL_CACHE["path"]
    candidates = []
    env = (os.environ.get("SY_CURL_EXE") or "").strip()
    if env:
        candidates.append(env)
    if os.name == "nt":
        root = os.environ.get("SystemRoot") or os.environ.get("windir") or "C:\\Windows"
        candidates.append(os.path.join(root, "System32", "curl.exe"))
        candidates.append("curl.exe")
        candidates.append("curl")
    else:
        candidates.extend(["/usr/bin/curl", "/usr/local/bin/curl", "/bin/curl", "curl"])
    found = None
    for cand in candidates:
        if not cand:
            continue
        looks_like_path = os.path.sep in cand or "/" in cand or "\\" in cand
        try:
            if looks_like_path:
                if os.path.isfile(cand):
                    found = cand
                    break
            else:
                hit = shutil.which(cand)
                if hit:
                    found = hit
                    break
        except Exception:
            continue
    _CURL_CACHE["path"] = found
    return found


def curl_cffi_module():
    """설치돼 있으면 curl_cffi.requests 모듈, 없으면 None (결과는 캐시)."""
    if "module" in _CFFI_CACHE:
        return _CFFI_CACHE["module"]
    module = None
    try:
        from curl_cffi import requests as _cffi_requests  # noqa: WPS433 (선택 의존성)
        module = _cffi_requests
    except Exception:
        module = None
    _CFFI_CACHE["module"] = module
    return module


def _http_result(url, fetcher):
    return {"ok": False, "status": None, "body": b"", "error": "not executed",
            "content_type": "", "url": url, "fetcher": fetcher}


def _default_headers(headers=None):
    hdrs = {
        "User-Agent": CURL_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en;q=0.8,ko;q=0.6",
    }
    if headers:
        hdrs.update({k: v for k, v in headers.items() if v})
    return hdrs


def parse_curl_probe(text):
    """curl -w "%{http_code}\\t%{url_effective}" 출력 파싱 → (status:int|None, url:str)"""
    text = (text or "").strip()
    if not text:
        return None, ""
    status, url = None, ""
    for line in reversed(text.splitlines()):
        parts = [part.strip() for part in line.split("\t")]
        for part in parts:
            if re.match(r"^[1-5][0-9][0-9]$", part):
                status = int(part)
                break
        if status is not None:
            for part in parts:
                if re.match(r"^https?://", part):
                    url = part
                    break
            break
    return status, url


def _parse_last_headers(raw):
    """curl -D 덤프에서 마지막 응답 블록의 헤더값을 dict 로."""
    if not raw:
        return {}
    blocks = re.split(r"\r?\n\r?\n", raw.strip())
    headers = {}
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not any(re.match(r"^HTTP/\d", line) for line in lines):
            continue
        current = {}
        for line in lines:
            m = re.match(r"^([A-Za-z0-9\-]+):\s*(.*)$", line)
            if m:
                current[m.group(1).lower()] = m.group(2)
        if current:
            headers = current  # 마지막 응답 블록으로 계속 교체
    return headers


def curl_fetch(url, headers=None, timeout=CURL_DEFAULT_TIMEOUT, log=None,
               max_bytes=2 * 1024 * 1024, exe=None):
    """Windows 기본 curl.exe 로 GET. 결과 dict 는 http_request 와 같은 형태."""
    result = _http_result(url, "curl")
    exe = exe or find_curl_exe()
    if not exe or not os.path.isfile(exe):
        result["error"] = "curl 실행 파일 없음"
        return result
    hdrs = _default_headers(headers)
    max_time = max(5, min(_to_int(timeout, CURL_DEFAULT_TIMEOUT) or CURL_DEFAULT_TIMEOUT, 60))
    tmpdir = tempfile.mkdtemp(prefix="sy_curl_")
    body_path = os.path.join(tmpdir, "body.bin")
    hdr_path = os.path.join(tmpdir, "headers.txt")
    argv = [exe, "-L", "-sS", "--compressed",
            "--max-time", str(max_time),
            "--connect-timeout", str(min(10, max_time)),
            "--max-redirs", "5",
            "-A", hdrs["User-Agent"],
            "-H", "Accept: " + hdrs["Accept"],
            "-H", "Accept-Language: " + hdrs["Accept-Language"],
            "-D", hdr_path,
            "-o", body_path,
            "-w", "%{http_code}\t%{url_effective}",
            url]
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW (콘솔 창 깜박임 방지)

    def _invoke(extra_flags=()):
        cmd = [exe] + list(extra_flags) + argv[1:]
        return subprocess.run(cmd, timeout=max_time + 15, **kwargs)

    try:
        proc = _invoke()
        err_text = (proc.stderr or b"").decode("utf-8", "replace")
        # Windows Schannel 의 CRL(폐기목록) 조회 실패로 정상 인증서도 거절되는 경우가 있다.
        # 인증서 검증은 그대로 두고 폐기목록 확인만 생략해 한 번 재시도한다(--ssl-no-revoke).
        if proc.returncode == 60 and "revok" in err_text.lower():
            if log:
                log.warn("curl 인증서 폐기목록(CRL) 조회 실패 → --ssl-no-revoke 로 재시도: %s" % url)
            proc = _invoke(("--ssl-no-revoke",))
    except subprocess.TimeoutExpired:
        _rmtree_quiet(tmpdir)
        result["error"] = "curl 타임아웃(%d초)" % max_time
        return result
    except Exception as exc:
        _rmtree_quiet(tmpdir)
        result["error"] = "curl 실행 실패: %s: %s" % (type(exc).__name__, exc)
        return result
    try:
        stdout = proc.stdout.decode("utf-8", "replace") if proc.stdout else ""
        stderr = proc.stderr.decode("utf-8", "replace") if proc.stderr else ""
        status, final_url = parse_curl_probe(stdout)
        body = b""
        try:
            with open(body_path, "rb") as fp:
                body = fp.read(max_bytes)
        except Exception:
            body = b""
        content_type = ""
        try:
            with open(hdr_path, "r", encoding="utf-8", errors="replace") as fp:
                content_type = _parse_last_headers(fp.read()).get("content-type", "")
        except Exception:
            content_type = ""
    finally:
        _rmtree_quiet(tmpdir)
    result.update({"status": status, "body": body, "content_type": content_type,
                   "url": final_url or url, "exit_code": proc.returncode})
    if body:
        if status and 200 <= status < 400:
            result["ok"] = True
            result["error"] = None
        else:
            result["error"] = "HTTP %s" % (status if status else "?")
    else:
        detail = " ".join(stderr.split())[:200]
        result["error"] = "curl exit %s%s" % (proc.returncode, (" · " + detail) if detail else "")
    return result


def _rmtree_quiet(path):
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def cffi_fetch(url, headers=None, timeout=CURL_DEFAULT_TIMEOUT, log=None,
               max_bytes=2 * 1024 * 1024):
    """curl_cffi(설치 시)로 Chrome 지문 위장 GET."""
    result = _http_result(url, "cffi")
    module = curl_cffi_module()
    if module is None:
        result["error"] = "curl_cffi 미설치"
        return result
    max_time = max(5, min(_to_int(timeout, CURL_DEFAULT_TIMEOUT) or CURL_DEFAULT_TIMEOUT, 60))
    try:
        resp = module.get(url, headers=_default_headers(headers), timeout=max_time,
                          impersonate="chrome", allow_redirects=True)
        result.update({"status": getattr(resp, "status_code", None),
                       "body": (resp.content or b"")[:max_bytes],
                       "content_type": (resp.headers or {}).get("content-type", ""),
                       "url": getattr(resp, "url", None) or url})
        status = result["status"]
        if status and 200 <= status < 400:
            result["ok"] = True
            result["error"] = None
        else:
            result["error"] = "HTTP %s" % (status if status else "?")
    except Exception as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
    return result


def fetch_with_engine(name, url, headers=None, timeout=CURL_DEFAULT_TIMEOUT, log=None,
                      max_bytes=2 * 1024 * 1024):
    if name == "curl":
        return curl_fetch(url, headers=headers, timeout=timeout, log=log, max_bytes=max_bytes)
    if name == "cffi":
        return cffi_fetch(url, headers=headers, timeout=timeout, log=log, max_bytes=max_bytes)
    # urllib 은 기존 동작 그대로(모듈 UA/헤더 유지) — 최종 폴백이므로 건드리지 않는다.
    res = http_request(url, headers=headers, timeout=timeout,
                       retries=0, log=log, max_bytes=max_bytes)
    res["fetcher"] = "urllib"
    return res


def available_engines():
    """이 PC에서 실제로 쓸 수 있는 엔진 목록."""
    have = []
    if curl_cffi_module() is not None:
        have.append("cffi")
    if find_curl_exe():
        have.append("curl")
    have.append("urllib")
    return have


def strip_html(raw_html):
    if not raw_html:
        return ""
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw_html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h[1-6]|li|tr|td|section|article)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                         ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(entity, char)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# 설정 · 파일
# ---------------------------------------------------------------------------

def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except FileNotFoundError:
        return default
    except Exception:
        return default


def save_json_atomic(path, payload):
    folder = os.path.dirname(os.path.abspath(path))
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=1)
        fp.write("\n")
    os.replace(tmp, path)


def resolve_path(value, base_dir=None):
    if not value:
        return value
    if os.path.isabs(value) or re.match(r"^[A-Za-z]:[\\/]", value):
        return value
    return os.path.join(base_dir or BASE_DIR, value)


def _deep_merge(base, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path=None):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path:
        candidate = os.path.abspath(path)
        config_path = candidate if os.path.exists(candidate) else resolve_path(path)
    else:
        config_path = resolve_path("config.json")
    raw = load_json(config_path, default=None)
    if isinstance(raw, dict):
        _deep_merge(cfg, raw)
    return cfg, (config_path if isinstance(raw, dict) else None)


def _token(cfg_section, key="token_env"):
    env_name = cfg_section.get(key) or ""
    token = os.environ.get(env_name, "") if env_name else ""
    if not token:
        token = cfg_section.get("token") or ""
    return token.strip()


# ---------------------------------------------------------------------------
# 1) 쇼피
# ---------------------------------------------------------------------------

def load_shopee_cookies(path):
    """shopee_cookies.json → {cookie, spc_cds, saved_at, source, ok, error}

    허용 형식(어느 하나면 됨)
      {"cookie": "SPC_F=...; SPC_SI=...", "spc_cds": "35738ed8-..."}
      {"Cookie": "...", "SPC_CDS": "..."}
      {"cookies": {"SPC_F": "...", "SPC_SI": "..."}, "SPC_CDS": "..."}
      {"cookies": [{"name": "SPC_F", "value": "..."}, ...]}
      쿠키 헤더 문자열만 저장한 plain text 파일
    """
    out = {"ok": False, "cookie": "", "spc_cds": "", "saved_at": None,
           "source": None, "error": None}
    if not path or not os.path.exists(path):
        out["error"] = "파일 없음: %s" % path
        return out
    raw_text = ""
    try:
        with open(path, "r", encoding="utf-8") as fp:
            raw_text = fp.read()
    except Exception as exc:
        out["error"] = "읽기 실패: %s" % exc
        return out

    data = None
    try:
        data = json.loads(raw_text)
    except Exception:
        data = None

    if isinstance(data, str):
        out["cookie"] = data.strip()
    elif isinstance(data, list):
        pairs = []
        for item in data:
            if isinstance(item, dict) and item.get("name"):
                pairs.append("%s=%s" % (item["name"], item.get("value", "")))
        out["cookie"] = "; ".join(pairs)
    elif isinstance(data, dict):
        cookie = data.get("cookie") or data.get("Cookie") or data.get("cookie_header") or ""
        if not cookie:
            jar = data.get("cookies") or data.get("cookie_jar")
            if isinstance(jar, dict):
                cookie = "; ".join("%s=%s" % (k, v) for k, v in jar.items())
            elif isinstance(jar, list):
                cookie = "; ".join("%s=%s" % (c.get("name"), c.get("value", ""))
                                   for c in jar if isinstance(c, dict) and c.get("name"))
        out["cookie"] = str(cookie).strip()
        out["spc_cds"] = str(data.get("spc_cds") or data.get("SPC_CDS")
                             or data.get("spcCds") or "").strip()
        out["saved_at"] = data.get("saved_at") or data.get("updated_at")
        out["source"] = data.get("source")
    else:
        out["cookie"] = raw_text.strip()

    if not out["spc_cds"] and out["cookie"]:
        m = re.search(r"(?:^|;\s*)SPC_CDS=([0-9a-fA-F\-]{36})", out["cookie"])
        if m:
            out["spc_cds"] = m.group(1)
    if out["cookie"]:
        out["ok"] = True
    else:
        out["error"] = "cookie 값이 비어 있음"
    return out


def _shopee_headers(cookies):
    return {
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://seller.shopee.kr/portal/product/list/all",
        "Cookie": cookies.get("cookie") or "",
    }


def _looks_like_login_page(text):
    low = (text or "")[:4000].lower()
    return ("<html" in low and "login" in low) or "please login" in low or "로그인이 필요" in low


def classify_shopee_response(res):
    """(상태, 메시지) — 상태: ok / cookie_expired / api_error / http_error / bad_json"""
    if not res.get("ok"):
        status = res.get("status")
        err = (res.get("error") or "")[:160]
        if status in (401, 403):
            return "cookie_expired", "HTTP %s(로그인/토큰 만료 의심)" % status
        return "http_error", err or "요청 실패"
    text = decode_body(res)
    if _looks_like_login_page(text):
        return "cookie_expired", "로그인 페이지 응답"
    try:
        data = json.loads(text)
    except Exception:
        return "bad_json", "JSON 아님(앞부분: %s)" % " ".join(text[:80].split())
    if not isinstance(data, dict):
        return "bad_json", "JSON 형식 예상과 다름"
    code = data.get("code")
    msg = str(data.get("message") or data.get("msg") or "")
    is_ok = (code in (0, "0", None)) and ("data" in data)
    if is_ok:
        return "ok", ""
    if code in (401, 403) or re.search(r"csrf|login|session|unauthor|비로그인", msg, re.I):
        return "cookie_expired", "code=%s %s" % (code, msg[:120])
    return "api_error", "code=%s %s" % (code, msg[:120])


def shopee_get(base_url, path, params, cookies, timeout=20, log=None):
    query = dict(params)
    query.setdefault("SPC_CDS_VER", "2")
    if cookies.get("spc_cds"):
        query.setdefault("SPC_CDS", cookies["spc_cds"])
    url = base_url.rstrip("/") + path + "?" + urllib.parse.urlencode(query)
    res = http_request(url, headers=_shopee_headers(cookies), timeout=timeout, retries=1, log=log)
    state, detail = classify_shopee_response(res)
    payload = None
    if state == "ok":
        payload = json.loads(decode_body(res))
    return {"state": state, "detail": detail, "payload": payload,
            "status": res.get("status"), "url": url}


def _to_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _region_params(region):
    _, shop, shop_id, area = region
    return {"cnsc_shop_id": shop_id, "cbsc_shop_region": area}


def check_shopee_region(cfg, cookies, region, log):
    code, shop, shop_id, area = region
    base = (cfg.get("shopee") or {}).get("base_url") or "https://seller.shopee.kr"
    timeout = _to_int((cfg.get("shopee") or {}).get("timeout"), 20) or 20
    item = {"region": code, "shop": shop, "shop_id": shop_id, "area": area,
            "ok": False, "state": None, "metrics": {}, "boost": {}, "warnings": []}

    rm = shopee_get(base, PATH_REALTIME,
                    dict(_region_params(region), event="paid"), cookies, timeout, log)
    item["state"] = rm["state"]
    if rm["state"] == "ok":
        data = (rm["payload"] or {}).get("data") or {}
        km = data.get("key_metrics") or {}
        hourly = data.get("sales_hourly") or []
        metrics = {
            "uv": _to_int(km.get("uv")),
            "pv": _to_int(km.get("pv")),
            "product_clicks": _to_int(km.get("product_clicks")),
            "orders": _to_int(km.get("orders")),
            "units": _to_int(km.get("units")),
            "buyers": _to_int(km.get("buyers")),
            "sales": km.get("sales"),
            "sales_currency": km.get("currency") or km.get("sales_currency"),
            "server_time": data.get("time"),
            "hour_buckets": len(hourly),
        }
        if len(hourly) <= 1:
            metrics["note"] = "현지 자정 직후(유입 0은 리셋 영향일 수 있음)"
        item["metrics"] = metrics
        item["ok"] = True
    else:
        item["error"] = rm["detail"]

    if item["ok"] and (cfg.get("shopee") or {}).get("collect_boost", True):
        bl = shopee_get(base, PATH_BUMPED, _region_params(region), cookies, timeout, log)
        if bl["state"] == "ok":
            data = (bl["payload"] or {}).get("data") or {}
            conf = data.get("config") or {}
            products = data.get("products") or []
            total = _to_int(conf.get("total_slots"), 5)
            used = len(products)
            cooldowns = [_to_int(p.get("cool_down_seconds")) for p in products
                         if isinstance(p, dict)]
            max_cd = max(cooldowns) if cooldowns else 0
            item["boost"] = {
                "slots_total": total,
                "slots_used": used,
                "slots_free": max(total - used, 0),
                "cooldown_seconds_total": _to_int(conf.get("cooldown_seconds")),
                "cooldown_remaining_seconds": max_cd,
                "next_free_at": _iso(_now() + datetime.timedelta(seconds=max_cd)) if max_cd else None,
                "product_ids": [str(p.get("id")) for p in products if isinstance(p, dict)],
            }
        else:
            item["warnings"].append("부스트 슬롯 조회 %s: %s" % (bl["state"], bl["detail"]))
    return item


def collect_shopee(cfg, log, dry_run=False):
    section = cfg.get("shopee") or {}
    result = {"enabled": bool(section.get("enabled", True)), "cookie_state": None,
              "cookie_error": None, "cookie_saved_at": None, "regions": [],
              "totals": {}, "warnings": [], "errors": []}
    if not result["enabled"]:
        result["cookie_state"] = "disabled"
        return result

    cookies_path = resolve_path(section.get("cookies_path") or "shopee_cookies.json")
    cookies = load_shopee_cookies(cookies_path)
    result["cookie_saved_at"] = cookies.get("saved_at")
    if not cookies["ok"]:
        result["cookie_state"] = "cookie_missing"
        result["cookie_error"] = cookies.get("error")
        result["warnings"].append("쿠키 파일 없음/비어 있음 → 쇼피 조회 건너뜀(%s)"
                                 % (cookies.get("error") or "사유 미상"))
        log.warn("쇼피 쿠키 없음: %s" % cookies_path)
        return result

    delay = float(section.get("delay_seconds") or 0.5)
    expired_hit = False
    for idx, region in enumerate(REGIONS):
        item = check_shopee_region(cfg, cookies, region, log)
        result["regions"].append(item)
        if item["state"] == "cookie_expired":
            expired_hit = True
            log.warn("쇼피 %s: 쿠키 만료/로그인 필요 (%s)" % (region[0], item.get("error")))
        elif item["ok"]:
            m = item["metrics"]
            b = item.get("boost") or {}
            log.ok("쇼피 %s: 방문 %d · 클릭 %d · 주문 %d · 부스트 %d/%d%s" % (
                region[0], m.get("uv", 0), m.get("product_clicks", 0), m.get("orders", 0),
                b.get("slots_used", 0), b.get("slots_total", 0),
                (" · 해제 %s" % _hhmm(_parse_iso(b["next_free_at"]))) if b.get("next_free_at") else ""))
        else:
            log.warn("쇼피 %s: %s (%s)" % (region[0], item["state"], item.get("error")))
        if idx < len(REGIONS) - 1 and delay:
            time.sleep(delay)

    states = [r.get("state") for r in result["regions"]]
    for r in result["regions"]:
        for warning in r.get("warnings") or []:
            result["warnings"].append("%s: %s" % (r["region"], warning))
    if expired_hit or (states and all(s == "cookie_expired" for s in states)):
        result["cookie_state"] = "cookie_expired"
    elif any(s == "ok" for s in states):
        result["cookie_state"] = "ok"
    else:
        result["cookie_state"] = "error"

    ok_items = [r for r in result["regions"] if r["ok"]]
    totals = {"regions_ok": len(ok_items), "regions_total": len(REGIONS),
              "uv": 0, "pv": 0, "product_clicks": 0, "orders": 0, "units": 0, "buyers": 0,
              "sales_sum": 0.0, "sales_sum_note": "지역별 응답값 단순합(환산 아님)",
              "boost_used": 0, "boost_total": 0}
    for r in ok_items:
        m = r["metrics"]
        for key in ("uv", "pv", "product_clicks", "orders", "units", "buyers"):
            totals[key] += _to_int(m.get(key))
        try:
            totals["sales_sum"] += float(m.get("sales") or 0)
        except Exception:
            pass
        b = r.get("boost") or {}
        totals["boost_used"] += _to_int(b.get("slots_used"))
        totals["boost_total"] += _to_int(b.get("slots_total"))
    totals["sales_sum"] = round(totals["sales_sum"], 2)
    result["totals"] = totals
    if totals["boost_total"] and totals["boost_used"] < totals["boost_total"]:
        result["warnings"].append("쇼피 무료 부스트 빈 슬롯 %d개 (run_boost 로 채울 수 있음)"
                                  % (totals["boost_total"] - totals["boost_used"]))
    return result


# ---------------------------------------------------------------------------
# 2) 큐텐 재팬
# ---------------------------------------------------------------------------

QOO10_BLOCK_MARKERS = [
    "523 error", "エラーが発生しました", "access denied", "アクセスが集中",
    "forbidden", "captcha", "cf-browser-verification",
]
QOO10_SOLD_MARKERS = ["販売終了", "販売を終了", "売り切れ", "sold out", "在庫切れ"]
QOO10_ONSALE_MARKERS = ["販売中", "カートに入れる", "今すぐ購入", "add to cart"]


def is_blocked_html(html):
    low = (html or "").lower()
    return any(marker in low for marker in QOO10_BLOCK_MARKERS)


def qoo10_engine_order(section=None):
    """큐텐 조회 엔진 순서.

    auto: curl_cffi(설치 시) → Windows curl.exe → urllib
          (비Windows 는 urllib 먼저, 그래도 막히면 curl 로 한 번 더)
    """
    section = section or {}
    mode = (section.get("http_engine") or "auto").strip().lower()
    have = available_engines()
    if mode == "urllib":
        return ["urllib"]
    if mode == "curl":
        return [name for name in ("curl", "urllib") if name in have] or ["urllib"]
    if mode == "cffi":
        return [name for name in ("cffi", "urllib") if name in have] or ["urllib"]
    order = []
    if "cffi" in have:
        order.append("cffi")
    if os.name == "nt" and "curl" in have:
        order.append("curl")
    order.append("urllib")
    if os.name != "nt" and "curl" in have:
        order.append("curl")
    return order


def fetch_qoo10_page(url, headers=None, timeout=25, log=None, section=None):
    """큐텐 페이지를 여러 엔진으로 시도해 (결과, html) 을 돌려준다.

    차단 페이지(Akamai 523 등)는 다음 엔진으로 넘어가고, 모두 막히면 마지막 차단
    응답을 그대로 돌려준다(원인 보고용). 시도 내역은 결과의 `attempts` 에 담긴다.
    """
    section = section or {}
    engine_timeout = _to_int(section.get("curl_timeout"), CURL_DEFAULT_TIMEOUT) or CURL_DEFAULT_TIMEOUT
    attempts = []
    blocked = None
    errors = []
    for name in qoo10_engine_order(section):
        try:
            engine_timeout_now = timeout if name == "urllib" else engine_timeout
            res = fetch_with_engine(name, url, headers=headers, timeout=engine_timeout_now, log=log)
        except Exception as exc:  # 엔진 하나가 죽어도 전체를 죽이지 않는다
            res = _http_result(url, name)
            res["error"] = "%s: %s" % (type(exc).__name__, exc)
        body = res.get("body") or b""
        html = decode_body(res) if body else ""
        attempts.append({"engine": name, "ok": bool(res.get("ok")),
                         "status": res.get("status"), "bytes": len(body),
                         "error": res.get("error")})
        if body and not is_blocked_html(html):
            res["attempts"] = attempts
            res["blocked_page"] = False
            if log and name != attempts[0]["engine"]:
                log.info("큐텐 %s 엔진으로 우회 성공 (앞선 시도: %s)" % (
                    name, ", ".join("%s=%s" % (a["engine"], a["status"]) for a in attempts[:-1])))
            return res, html
        if body and is_blocked_html(html):
            if blocked is None:
                blocked = (res, html)
            continue
        errors.append((res, html))
    if blocked is not None:
        blocked[0]["attempts"] = attempts
        blocked[0]["blocked_page"] = True
        return blocked
    if errors:
        errors[0][0]["attempts"] = attempts
        return errors[0]
    res = _http_result(url, None)
    res["error"] = "시도할 HTTP 엔진 없음"
    res["attempts"] = attempts
    return res, ""

LD_JSON_RE = re.compile(
    r"""<script[^>]+type=["']application/ld\+json["'][^>]*>(.*?)</script>""", re.S | re.I)
META_PRICE_RE = re.compile(
    r"""<meta[^>]+(?:itemprop|property)=["'](?:price|product:price:amount)["'][^>]*content=["']([^"']+)""", re.I)
ITEMPROP_PRICE_RE = re.compile(
    r"""itemprop=["']price["'][^>]*content=["']([0-9][0-9,\.]*)""", re.I)
PRICE_CLASS_RE = re.compile(
    r"""(?:id|class)=["'][^"']*(?:q_price|price|価格)[^"']*["'][^>]*>\s*(?:<[^>]+>\s*)*([^<]{0,40})""", re.I)
YEN_RE = re.compile(r"(?:¥|￥)\s?([0-9][0-9,]{2,})")
EN_RE = re.compile(r"([0-9][0-9,]{2,})\s?円")


def _digits(value):
    if value is None:
        return None
    m = re.search(r"([0-9][0-9,]{1,})", str(value))
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except Exception:
        return None


def _walk_json(node, depth=0):
    if depth > 6:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            for sub in _walk_json(value, depth + 1):
                yield sub
    elif isinstance(node, list):
        for value in node:
            for sub in _walk_json(value, depth + 1):
                yield sub


def parse_qoo10_price(html):
    """가격 후보를 (값, 근거) 목록으로. 신뢰도 높은 순서대로 정렬해 돌려준다."""
    found = []
    for m in LD_JSON_RE.finditer(html or ""):
        raw = m.group(1).strip()
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        for node in _walk_json(obj):
            offers = node.get("offers")
            if isinstance(offers, dict):
                price = _digits(offers.get("price") or offers.get("lowPrice"))
                currency = offers.get("priceCurrency") or ""
                if price:
                    found.append((price, "jsonld%s" % ("(%s)" % currency if currency else "")))
            elif isinstance(offers, list):
                for off in offers:
                    if isinstance(off, dict):
                        price = _digits(off.get("price") or off.get("lowPrice"))
                        if price:
                            found.append((price, "jsonld-list"))
            price = _digits(node.get("price"))
            if price and "Product" in str(node.get("@type", "")):
                found.append((price, "jsonld-price"))
    for m in META_PRICE_RE.finditer(html or ""):
        price = _digits(m.group(1))
        if price:
            found.append((price, "meta"))
    for m in ITEMPROP_PRICE_RE.finditer(html or ""):
        price = _digits(m.group(1))
        if price:
            found.append((price, "itemprop"))
    for m in PRICE_CLASS_RE.finditer(html or ""):
        price = _digits(m.group(1))
        if price:
            found.append((price, "price-class"))
    for m in YEN_RE.finditer(html or ""):
        price = _digits(m.group(1))
        if price:
            found.append((price, "yen-mark"))
    if not found:
        for m in EN_RE.finditer(html or ""):
            price = _digits(m.group(1))
            if price:
                found.append((price, "yen-suffix"))
    weight = {"jsonld": 100, "jsonld-list": 95, "jsonld-price": 95, "meta": 90,
              "itemprop": 90, "price-class": 80, "yen-mark": 50, "yen-suffix": 40}
    def rank(entry):
        source = entry[1].split("(")[0]
        return -weight.get(source, 10)
    found.sort(key=rank)
    seen, unique = set(), []
    for price, source in found:
        key = (price, source)
        if key in seen:
            continue
        seen.add(key)
        unique.append({"price": price, "source": source})
    return unique


def check_qoo10_item(cfg, item, log):
    section = cfg.get("qoo10") or {}
    template = section.get("url_template") or "https://www.qoo10.jp/g/{no}"
    url = template.format(no=item["no"])
    timeout = _to_int(section.get("timeout"), 25) or 25
    out = {"no": item["no"], "label": item.get("label") or "", "url": url, "ok": False,
           "status": None, "http_status": None, "page_title": "", "sale_state": "unknown",
           "price": None, "price_source": None, "expect_price": item.get("expect_price"),
           "price_match": None, "blocked": False, "warnings": [],
           "fetcher": None, "fetch_attempts": []}
    res, html = fetch_qoo10_page(url, headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                                               "Accept-Language": "ja,en;q=0.8,ko;q=0.6"},
                                 timeout=timeout, log=log, section=section)
    out["http_status"] = res.get("status")
    out["fetcher"] = res.get("fetcher")
    out["fetch_attempts"] = res.get("attempts") or []
    if not res.get("ok") and not html:
        out["status"] = "http_error"
        out["warnings"].append(res.get("error") or "요청 실패")
        return out

    if is_blocked_html(html):
        out["status"] = "blocked"
        out["blocked"] = True
        tried = ", ".join("%s=%s" % (a["engine"], a["status"]) for a in out["fetch_attempts"])
        out["warnings"].append("Qoo10 차단/오류 페이지(Akamai 523 등) — 시도 엔진: %s · 주거용 IP(집 PC)에서만 정상 조회"
                               % (tried or "없음"))
        return out

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if title_match:
        out["page_title"] = " ".join(strip_html(title_match.group(1)).split())[:160]

    prices = parse_qoo10_price(html)
    if prices:
        out["price"] = prices[0]["price"]
        out["price_source"] = prices[0]["source"]
        out["price_candidates"] = prices[:5]
        out["price_confidence"] = "low" if out["price_source"].startswith("yen") else "high"
        if out["expect_price"] and out["price_confidence"] == "high":
            out["price_match"] = (out["price"] == _to_int(out["expect_price"]))
            if not out["price_match"]:
                out["warnings"].append("가격 불일치: 기대 %s엔 / 확인 %s엔 (%s)" % (
                    out["expect_price"], out["price"], out["price_source"]))
        elif out["expect_price"]:
            out["warnings"].append("가격은 화면 텍스트 추정값 %s엔(신뢰도 낮음) — 원문 확인 권장"
                                   % out["price"])
    else:
        out["warnings"].append("가격 표기 미발견(페이지 구조 변경·차단 가능)")

    if any(marker in html for marker in QOO10_SOLD_MARKERS):
        out["sale_state"] = "sold_out_or_ended"
    elif any(marker in html for marker in QOO10_ONSALE_MARKERS):
        out["sale_state"] = "on_sale"
    else:
        out["sale_state"] = "unknown"

    out["ok"] = True
    out["status"] = "ok"
    log.ok("큐텐 %s %s: HTTP %s · %s · %s · %s" % (
        item["no"], out["label"], out["http_status"], out["sale_state"],
        ("%s엔(%s)" % (out["price"], out["price_source"])) if out["price"] else "가격 미확인",
        out["fetcher"] or "?"))
    return out


def collect_qoo10(cfg, log):
    section = cfg.get("qoo10") or {}
    result = {"enabled": bool(section.get("enabled", True)), "items": [], "warnings": [],
              "engines": available_engines(), "engine_order": qoo10_engine_order(section),
              "counts": {"total": 0, "ok": 0, "blocked": 0, "price_found": 0,
                         "price_matched": 0, "price_mismatch": 0}}
    if not result["enabled"]:
        return result
    for item in section.get("products") or []:
        if not isinstance(item, dict) or not item.get("no"):
            continue
        checked = check_qoo10_item(cfg, item, log)
        result["items"].append(checked)
        time.sleep(0.3)
    items = result["items"]
    result["counts"]["total"] = len(items)
    result["counts"]["ok"] = len([i for i in items if i["ok"]])
    result["counts"]["blocked"] = len([i for i in items if i.get("blocked")])
    result["counts"]["price_found"] = len([i for i in items if i.get("price")])
    result["counts"]["price_matched"] = len([i for i in items if i.get("price_match") is True])
    result["counts"]["price_mismatch"] = len([i for i in items if i.get("price_match") is False])
    blocked = result["counts"]["blocked"]
    if blocked and blocked == result["counts"]["total"]:
        result["warnings"].append("큐텐 전 항목 차단(Akamai) — 이 네트워크에서는 조회 불가, 집 PC에서 실행 "
                                  "(시도 엔진: %s)" % ", ".join(result["engine_order"]))
    return result


# ---------------------------------------------------------------------------
# 3) 설문 당첨 발표 감시
# ---------------------------------------------------------------------------

def _snippet(text, start, end, pad=90):
    left = max(0, start - pad)
    right = min(len(text), end + pad)
    return " ".join(text[left:right].split())


def keyword_hits(text, keywords, max_hits=6):
    hits = []
    seen = set()
    for keyword in keywords:
        for m in re.finditer(re.escape(keyword), text, re.I):
            snippet = _snippet(text, m.start(), m.end())
            key = re.sub(r"\s+", "", snippet)[:120]
            if key in seen:
                continue
            seen.add(key)
            hits.append({"keyword": keyword, "snippet": snippet[:220]})
            if len(hits) >= max_hits:
                return hits
    return hits


def check_watch_target(target, cfg, log):
    section = cfg.get("survey_watch") or {}
    keywords = section.get("keywords") or DEFAULT_KEYWORDS
    timeout = _to_int(section.get("timeout"), 25) or 25
    name = target.get("name") or urllib.parse.urlsplit(target.get("url", "")).netloc
    url = target.get("url") or ""
    out = {"name": name, "url": url, "note": target.get("note") or "", "ok": False,
           "status": None, "http_status": None, "final_url": url, "title": "",
           "hits": [], "announcement": False, "name_mentioned": False,
           "warnings": []}
    if not url:
        out["status"] = "no_url"
        return out
    res, html, hops = fetch_html(url, timeout=timeout, log=log)
    out["http_status"] = res.get("status")
    out["final_url"] = res.get("url") or url
    out["redirects"] = hops
    if not res.get("ok") or not html:
        out["status"] = "http_error"
        out["warnings"].append(res.get("error") or "본문 없음")
        log.warn("당첨감시 %s: %s" % (name, out["warnings"][-1]))
        return out

    text = strip_html(html)
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if title_match:
        out["title"] = " ".join(strip_html(title_match.group(1)).split())[:140]
    hits = keyword_hits(text, keywords)
    out["hits"] = hits
    out["announcement"] = bool(re.search(r"당첨자?\s*(?:발표|안내|공지)|당첨\s*결과", text))
    out["name_mentioned"] = bool(re.search(r"김\s*승\s*용|8972", text))
    out["ok"] = True
    out["status"] = "ok"
    out["text_length"] = len(text)
    if len(text) < 300 and not hits:
        out["status"] = "js_rendered"
        out["warnings"].append("서버 HTML 본문이 거의 없음(SPA/JS 렌더링) — 이 주소는 브라우저 확인 필요")
        log.warn("당첨감시 %s: JS 렌더링 페이지로 감지 불가(%d자)" % (name, len(text)))
        return out
    if hits or out["announcement"]:
        log.ok("당첨감시 %s: 키워드 %d건%s%s" % (
            name, len(hits), " · 게시판 발표 문구" if out["announcement"] else "",
            " · 이름/번호 노출!" if out["name_mentioned"] else ""))
    else:
        log.info("당첨감시 %s: 키워드 없음(정상)" % name)
    return out


def load_due_surveys(cfg, log):
    section = cfg.get("survey_watch") or {}
    path = resolve_path(section.get("history_path") or "")
    lookahead = _to_int(section.get("lookahead_days"), 0)
    out = {"path": path, "found": False, "due": []}
    data = load_json(path, default=None)
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        if path and os.path.exists(path):
            log.warn("history.json 형식이 예상과 달라 건너뜀: %s" % path)
        return out
    out["found"] = True
    today = _now().date()
    limit = today + datetime.timedelta(days=max(lookahead, 0))
    for entry in data["entries"]:
        if not isinstance(entry, dict):
            continue
        raw = str(entry.get("announce_date") or "")
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
        if not m:
            continue
        try:
            day = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            continue
        if today <= day <= limit:
            out["due"].append({
                "id": entry.get("id"), "title": entry.get("title"),
                "announce_date": raw, "deadline": entry.get("deadline"),
                "status": entry.get("status"),
                "survey_url": entry.get("url"),
            })
    out["due"].sort(key=lambda e: (e.get("announce_date") or "", e.get("title") or ""))
    if out["due"]:
        log.info("오늘(%s) 발표 예정 설문 %d건: %s" % (
            today.isoformat(), len(out["due"]),
            " · ".join((e.get("title") or "")[:24] for e in out["due"])))
    return out


def collect_surveys(cfg, log):
    section = cfg.get("survey_watch") or {}
    result = {"enabled": bool(section.get("enabled", True)), "targets": [],
              "due": {}, "alerts": [], "warnings": []}
    if not result["enabled"]:
        return result
    result["due"] = load_due_surveys(cfg, log)
    targets = section.get("targets") or []
    for target in targets:
        if not isinstance(target, dict):
            continue
        checked = check_watch_target(target, cfg, log)
        result["targets"].append(checked)
        if checked.get("name_mentioned"):
            result["alerts"].append({"level": "high", "target": checked["name"],
                                     "reason": "이름/번호(김승용·8972) 노출",
                                     "url": checked.get("final_url")})
        elif checked.get("announcement"):
            result["alerts"].append({"level": "info", "target": checked["name"],
                                     "reason": "당첨자 발표 문구 발견",
                                     "url": checked.get("final_url")})
        time.sleep(0.3)
    failed = [t["name"] for t in result["targets"] if not t.get("ok")]
    if failed:
        result["warnings"].append("당첨감시 실패: %s" % ", ".join(failed))
    js_only = [t["name"] for t in result["targets"] if t.get("status") == "js_rendered"]
    if js_only:
        result["warnings"].append("JS 렌더링이라 감시 불가: %s (브라우저 확인 필요)" % ", ".join(js_only))
    result["counts"] = {
        "targets": len(result["targets"]),
        "ok": len([t for t in result["targets"] if t.get("status") == "ok"]),
        "js_rendered": len(js_only),
        "name_mentioned": len([t for t in result["targets"] if t.get("name_mentioned")]),
        "due": len((result.get("due") or {}).get("due") or []),
    }
    return result


# ---------------------------------------------------------------------------
# 이전 실행과 비교(변화량)
# ---------------------------------------------------------------------------

def compute_changes(prev, payload):
    if not isinstance(prev, dict):
        return None
    changes = {"since": prev.get("last_run_at"), "regions": {}, "qoo10": [], "watch": []}
    prev_regions = (prev.get("regions") or {})
    for item in payload["shopee"].get("regions", []):
        before = prev_regions.get(item["region"]) or {}
        if not item.get("ok") or not before:
            continue
        delta = {}
        for key in ("uv", "pv", "product_clicks", "orders", "units"):
            now_val = _to_int((item.get("metrics") or {}).get(key))
            old_val = _to_int((before.get("metrics") or {}).get(key))
            if now_val != old_val:
                delta[key] = now_val - old_val
        prev_slots = _to_int((before.get("boost") or {}).get("slots_used"))
        now_slots = _to_int((item.get("boost") or {}).get("slots_used"))
        if now_slots != prev_slots:
            delta["boost_slots"] = now_slots - prev_slots
        if delta:
            changes["regions"][item["region"]] = delta
    prev_q = prev.get("qoo10") or {}
    for item in payload["qoo10"].get("items", []):
        before = prev_q.get(item["no"]) or {}
        if not before:
            continue
        if before.get("sale_state") != item.get("sale_state"):
            changes["qoo10"].append({"no": item["no"], "label": item.get("label"),
                                     "from": before.get("sale_state"), "to": item.get("sale_state")})
        if before.get("price") != item.get("price"):
            changes["qoo10"].append({"no": item["no"], "label": item.get("label"),
                                     "price_from": before.get("price"), "price_to": item.get("price")})
    prev_watch = prev.get("watch") or {}
    for target in payload["survey_watch"].get("targets", []):
        before = prev_watch.get(target["name"]) or {}
        old_snips = set(h.get("snippet") for h in (before.get("hits") or []))
        new_hits = [h for h in target.get("hits", []) if h.get("snippet") not in old_snips]
        if new_hits:
            changes["watch"].append({"target": target["name"], "new_hits": new_hits[:3]})
    changes["has_changes"] = bool(changes["regions"] or changes["qoo10"] or changes["watch"])
    return changes


def build_state(payload):
    state = {"version": 1, "last_run_at": payload.get("generated_at"), "regions": {},
             "qoo10": {}, "watch": {}}
    for item in payload["shopee"].get("regions", []):
        if item.get("ok"):
            state["regions"][item["region"]] = {"metrics": item.get("metrics"),
                                                "boost": item.get("boost"),
                                                "checked_at": payload.get("generated_at")}
    for item in payload["qoo10"].get("items", []):
        state["qoo10"][item["no"]] = {"sale_state": item.get("sale_state"),
                                      "price": item.get("price"),
                                      "checked_at": payload.get("generated_at")}
    for target in payload["survey_watch"].get("targets", []):
        state["watch"][target["name"]] = {"hits": target.get("hits") or [],
                                          "announcement": target.get("announcement"),
                                          "checked_at": payload.get("generated_at")}
    return state


# ---------------------------------------------------------------------------
# GitHub · 텔레그램
# ---------------------------------------------------------------------------

def redact_for_github(payload):
    """GitHub(sy-dashboard)는 공개 저장소다. 이름·전화번호가 섞일 수 있는
    당첨 감시 상세(스니펫·대상별 결과)는 빼고 집계만 올린다."""
    out = json.loads(json.dumps(payload, ensure_ascii=False))
    watch = out.get("survey_watch") or {}
    due = (watch.get("due") or {}).get("due") or []
    targets = watch.get("targets") or []
    out["survey_watch"] = {
        "enabled": watch.get("enabled"),
        "counts": watch.get("counts") or {},
        "due_count": len(due),
        "due_dates": sorted(set(str(e.get("announce_date") or "") for e in due))[:5],
        "alerts_count": len(watch.get("alerts") or []),
        "high_alerts": len([a for a in (watch.get("alerts") or [])
                            if a.get("level") == "high"]),
        "targets_checked": len(targets),
        "note": "개인정보 보호를 위해 상세 스니펫·대상 URL은 로컬 pc_stats.json 에만 저장",
    }
    out["redacted"] = True
    out.pop("config_path", None)
    out.pop("output", None)
    return out


def _compact_snapshot(payload):
    shopee = payload.get("shopee") or {}
    totals = shopee.get("totals") or {}
    qoo10 = payload.get("qoo10") or {}
    watch = payload.get("survey_watch") or {}
    return {
        "generated_at": payload.get("generated_at"),
        "cookie_state": shopee.get("cookie_state"),
        "uv": totals.get("uv"), "clicks": totals.get("product_clicks"),
        "orders": totals.get("orders"),
        "boost": "%s/%s" % (totals.get("boost_used"), totals.get("boost_total")),
        "qoo10": qoo10.get("counts"),
        "alerts": len(watch.get("alerts") or []),
        "regions": {r["region"]: {"uv": (r.get("metrics") or {}).get("uv"),
                                  "clicks": (r.get("metrics") or {}).get("product_clicks"),
                                  "orders": (r.get("metrics") or {}).get("orders"),
                                  "slots": "%s/%s" % ((r.get("boost") or {}).get("slots_used"),
                                                      (r.get("boost") or {}).get("slots_total"))}
                    for r in shopee.get("regions", []) if r.get("ok")},
    }


def merge_payloads(remote, new_payload, keep=24):
    snaps = []
    if isinstance(remote, dict):
        snaps = [s for s in (remote.get("snapshots") or []) if isinstance(s, dict)]
    snap = _compact_snapshot(new_payload)
    snaps = [s for s in snaps if s.get("generated_at") != snap.get("generated_at")]
    snaps.append(snap)
    snaps = snaps[-keep:]
    out = dict(new_payload)
    out["snapshots"] = snaps
    out["merged_at"] = _iso()
    return out


def github_push(payload, gh_cfg, log):
    owner = (gh_cfg.get("owner") or "").strip()
    repo = (gh_cfg.get("repo") or "").strip()
    path = (gh_cfg.get("path") or "pc_stats.json").strip()
    branch = (gh_cfg.get("branch") or "main").strip()
    token = _token(gh_cfg, "token_env")
    if not owner or not repo:
        return {"ok": False, "detail": "config.json github.owner/repo 미설정"}
    if not token:
        return {"ok": False, "detail": "토큰 없음(환경변수 %s 미설정)" % gh_cfg.get("token_env", "GITHUB_TOKEN")}

    api = "https://api.github.com/repos/%s/%s/contents/%s" % (owner, repo, urllib.parse.quote(path))
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    }
    sha, remote = None, None
    res = http_request(api + "?ref=" + urllib.parse.quote(branch), headers=headers, log=log, retries=1)
    if res["ok"]:
        try:
            meta = json.loads(decode_body(res))
            sha = meta.get("sha")
            if meta.get("content"):
                remote = json.loads(base64.b64decode(meta["content"]).decode("utf-8"))
        except Exception as exc:
            return {"ok": False, "detail": "원격 파일 파싱 실패: %s" % exc}
    elif res.get("status") != 404:
        return {"ok": False, "detail": "원격 조회 실패: %s" % res.get("error")}

    merged = merge_payloads(remote, payload, _to_int(gh_cfg.get("keep_snapshots"), 24) or 24)
    body = {
        "message": gh_cfg.get("commit_message") or "chore: PC 통합 조회(pc_stats) 갱신",
        "content": base64.b64encode(json.dumps(merged, ensure_ascii=False, indent=1).encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    put_headers = dict(headers)
    put_headers["Content-Type"] = "application/json"
    for attempt in range(2):
        res = http_request(api, method="PUT", data=json.dumps(body).encode("utf-8"),
                           headers=put_headers, log=log, retries=0)
        if res["ok"]:
            try:
                data = json.loads(decode_body(res))
                return {"ok": True, "detail": "snapshots %d" % len(merged.get("snapshots") or []),
                        "url": (data.get("commit") or {}).get("html_url") or ""}
            except Exception:
                return {"ok": True, "detail": "pushed"}
        if res.get("status") in (409, 422) and attempt == 0:
            log.warn("GitHub 충돌(%s), sha 재조회 후 1회 재시도" % res.get("status"))
            res2 = http_request(api + "?ref=" + urllib.parse.quote(branch), headers=headers,
                                log=log, retries=1)
            if res2["ok"]:
                try:
                    meta = json.loads(decode_body(res2))
                    if meta.get("sha"):
                        body["sha"] = meta["sha"]
                except Exception:
                    pass
            continue
        return {"ok": False, "detail": res.get("error") or "push failed"}
    return {"ok": False, "detail": "push failed"}


def send_telegram(text, tg_cfg, log):
    token = _token(tg_cfg, "token_env")
    chat_id = os.environ.get(tg_cfg.get("chat_id_env") or "TELEGRAM_CHAT_ID", "") \
        or tg_cfg.get("chat_id") or ""
    chat_id = str(chat_id).strip()
    if not token or not chat_id:
        return {"ok": False, "detail": "토큰/chat_id 없음(%s, %s)" % (
            tg_cfg.get("token_env", "TELEGRAM_BOT_TOKEN"),
            tg_cfg.get("chat_id_env", "TELEGRAM_CHAT_ID"))}
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text,
                                   "disable_web_page_preview": "true"}).encode("utf-8")
    res = http_request(url, method="POST", data=data,
                       headers={"Content-Type": "application/x-www-form-urlencoded"},
                       log=log, retries=1)
    if res["ok"]:
        return {"ok": True, "detail": "sent"}
    return {"ok": False, "detail": res.get("error")}


def build_telegram_text(payload):
    shopee = payload.get("shopee") or {}
    totals = shopee.get("totals") or {}
    qoo10 = payload.get("qoo10") or {}
    qc = qoo10.get("counts") or {}
    watch = payload.get("survey_watch") or {}
    lines = ["[PC 통합조회] %s" % _hhmm(_parse_iso(payload.get("generated_at")) or _now())]

    cookie_state = shopee.get("cookie_state")
    if cookie_state == "ok":
        lines.append("쇼피 %d/%d국 · 방문 %s · 클릭 %s · 주문 %s · 부스트 %s/%s" % (
            totals.get("regions_ok", 0), totals.get("regions_total", 0),
            totals.get("uv", 0), totals.get("product_clicks", 0), totals.get("orders", 0),
            totals.get("boost_used", 0), totals.get("boost_total", 0)))
        releases = []
        for r in shopee.get("regions", []):
            nxt = (r.get("boost") or {}).get("next_free_at")
            if nxt:
                releases.append(_hhmm(_parse_iso(nxt)))
        if releases:
            lines.append("부스트 해제 %s~%s" % (min(releases), max(releases)))
    elif cookie_state == "cookie_expired":
        lines.append("⚠ 쇼피 쿠키 만료/로그인 필요 → shopee_cookies.json 갱신 필요")
    elif cookie_state == "cookie_missing":
        lines.append("⚠ 쇼피 쿠키 파일 없음 → shopee_cookies.json 필요")
    else:
        lines.append("⚠ 쇼피 조회 실패(%s)" % (shopee.get("cookie_state") or "error"))

    if qc.get("total"):
        if qc.get("blocked") == qc.get("total"):
            lines.append("큐텐 %d종 전부 차단(이 네트워크) → 집 PC에서 재실행" % qc.get("total"))
        else:
            lines.append("큐텐 %d/%d 정상 · 판매중지/품절 %d · 가격 %d/%d 확인%s" % (
                qc.get("ok", 0), qc.get("total", 0),
                len([i for i in qoo10.get("items", []) if i.get("sale_state") == "sold_out_or_ended"]),
                qc.get("price_found", 0), qc.get("total", 0),
                (" · 불일치 %d" % qc["price_mismatch"]) if qc.get("price_mismatch") else ""))

    due = (watch.get("due") or {}).get("due") or []
    if due:
        titles = []
        for entry in due[:4]:
            title = entry.get("title") or ""
            titles.append(title[:18] + ("…" if len(title) > 18 else ""))
        lines.append("오늘 발표 예정 %d건: %s" % (len(due), ", ".join(titles)))
    for alert in (watch.get("alerts") or [])[:4]:
        if not alert.get("is_new", True):
            continue
        lines.append("%s %s — %s" % ("‼" if alert.get("level") == "high" else "·",
                                     alert.get("target"), alert.get("reason")))
    changes = payload.get("changes")
    if changes and changes.get("has_changes"):
        parts = []
        for region, delta in (changes.get("regions") or {}).items():
            if "uv" in delta:
                parts.append("%s 방문 %+d" % (region, delta["uv"]))
            if "product_clicks" in delta:
                parts.append("%s 클릭 %+d" % (region, delta["product_clicks"]))
        if parts:
            lines.append("변화: " + ", ".join(parts[:6]))
    errors = payload.get("errors") or []
    if errors:
        lines.append("오류 %d건: %s" % (len(errors), errors[0][:80]))
    return "\n".join(lines)[:3800]


# ---------------------------------------------------------------------------
# 전체 실행
# ---------------------------------------------------------------------------

def run_once(dry_run=False, config_path=None, out_path=None, telegram=False,
             push_github=None, quiet=False, only=None):
    """감시 스크립트(sy_auto_watch.py)에서도 그대로 호출하는 진입점."""
    log = Log(quiet=quiet)
    cfg, cfg_path = load_config(config_path)
    started = _now()
    payload = {
        "schema": "pc_stats/1",
        "generator": "stats_checker.py",
        "version": VERSION,
        "generated_at": _iso(started),
        "mode": "dry_run" if dry_run else "live",
        "config_path": cfg_path,
        "shopee": {"enabled": False, "cookie_state": "skipped", "regions": [],
                   "totals": {}, "warnings": [], "errors": []},
        "qoo10": {"enabled": False, "items": [], "counts": {}, "warnings": []},
        "survey_watch": {"enabled": False, "targets": [], "due": {}, "alerts": [],
                         "warnings": []},
        "warnings": [], "errors": [], "github": None, "telegram": None,
    }
    only_set = set(only or ["shopee", "qoo10", "surveys"])

    if "shopee" in only_set:
        try:
            payload["shopee"] = collect_shopee(cfg, log, dry_run)
        except Exception as exc:
            payload["errors"].append("쇼피 조회 예외: %s: %s" % (type(exc).__name__, exc))
            log.error(payload["errors"][-1])
    if "qoo10" in only_set:
        try:
            payload["qoo10"] = collect_qoo10(cfg, log)
        except Exception as exc:
            payload["errors"].append("큐텐 조회 예외: %s: %s" % (type(exc).__name__, exc))
            log.error(payload["errors"][-1])
    if "surveys" in only_set:
        try:
            payload["survey_watch"] = collect_surveys(cfg, log)
        except Exception as exc:
            payload["errors"].append("당첨 감시 예외: %s: %s" % (type(exc).__name__, exc))
            log.error(payload["errors"][-1])

    state_path = resolve_path(cfg.get("state_path") or "pc_stats_state.json")
    prev = load_json(state_path, default=None)
    payload["changes"] = compute_changes(prev, payload)
    new_watch_targets = set(c.get("target") for c in (payload["changes"] or {}).get("watch", []))
    for alert in payload["survey_watch"].get("alerts") or []:
        alert["is_new"] = (alert.get("target") in new_watch_targets) if prev else True

    for section in ("shopee", "qoo10", "survey_watch"):
        payload["warnings"].extend(payload[section].get("warnings") or [])

    counts = {
        "shopee_regions_ok": (payload["shopee"].get("totals") or {}).get("regions_ok", 0),
        "shopee_cookie_state": payload["shopee"].get("cookie_state"),
        "qoo10_ok": (payload["qoo10"].get("counts") or {}).get("ok", 0),
        "qoo10_price_mismatch": (payload["qoo10"].get("counts") or {}).get("price_mismatch", 0),
        "watch_due": len((payload["survey_watch"].get("due") or {}).get("due") or []),
        "watch_alerts": len(payload["survey_watch"].get("alerts") or []),
        "watch_new_alerts": len([a for a in (payload["survey_watch"].get("alerts") or [])
                                 if a.get("is_new")]),
        "errors": len(payload["errors"]),
        "warnings": len(payload["warnings"]),
    }
    payload["counts"] = counts

    # 저장
    out_path = out_path or resolve_path(cfg.get("output_path") or "pc_stats.json")
    if dry_run:
        dry_path = out_path.replace(".json", "") + ".dryrun.json"
        save_json_atomic(dry_path, payload)
        payload["output"] = dry_path
        log.info("드라이런 결과 저장: %s (상태 파일·전송 없음)" % dry_path)
    else:
        save_json_atomic(out_path, payload)
        payload["output"] = out_path
        save_json_atomic(state_path, build_state(payload))
        last_path = resolve_path(cfg.get("last_run_path") or "last_run_stats.json")
        save_json_atomic(last_path, {
            "generated_at": payload["generated_at"], "counts": counts,
            "changes": payload.get("changes"), "warnings": payload["warnings"][:10],
            "errors": payload["errors"][:10],
        })

    # GitHub 푸시
    gh_cfg = cfg.get("github") or {}
    want_push = push_github if push_github is not None else bool(gh_cfg.get("enabled"))
    if want_push and not dry_run:
        try:
            to_push = redact_for_github(payload) if gh_cfg.get("redact_personal", True) else payload
            payload["github"] = github_push(to_push, gh_cfg, log)
            if payload["github"] is not None and gh_cfg.get("redact_personal", True):
                payload["github"]["redacted"] = True
        except Exception as exc:
            payload["github"] = {"ok": False, "detail": "%s: %s" % (type(exc).__name__, exc)}
        log.info("GitHub: %s" % (payload["github"] or {}).get("detail"))

    # 텔레그램 (드라이런에서는 시도조차 안 함)
    if telegram and not dry_run:
        try:
            payload["telegram"] = send_telegram(build_telegram_text(payload),
                                                cfg.get("telegram") or {}, log)
        except Exception as exc:
            payload["telegram"] = {"ok": False, "detail": "%s: %s" % (type(exc).__name__, exc)}
        log.info("텔레그램: %s" % (payload["telegram"] or {}).get("detail"))

    log.info("완료: 쇼피 %d/%d국(쿠키 %s) · 큐텐 %d/%d · 당첨감시 알림 %d · 경고 %d · 오류 %d" % (
        counts["shopee_regions_ok"], len(REGIONS), counts["shopee_cookie_state"],
        counts["qoo10_ok"], (payload["qoo10"].get("counts") or {}).get("total", 0),
        counts["watch_alerts"], counts["warnings"], counts["errors"]))
    return payload


# ---------------------------------------------------------------------------
# 자체 점검 (--self-test, 네트워크 불필요)
# ---------------------------------------------------------------------------

SAMPLE_BOOST_LIST = {
    "code": 0,
    "data": {"config": {"total_slots": 5, "cooldown_seconds": 14400},
             "products": [{"id": 111111, "cool_down_seconds": 3300},
                          {"id": 222222, "cool_down_seconds": 2000}]},
}
SAMPLE_REALTIME = {
    "code": 0,
    "data": {"key_metrics": {"uv": 4, "pv": 6, "product_clicks": 1, "orders": 0,
                             "units": 0, "buyers": 0, "sales": 0},
             "sales_hourly": [0, 0, 0], "time": 1795754205},
}
SAMPLE_CSRF_FAIL = {"code": 403, "message": "csrf validation fail"}


def _start_mock_server(routes):
    """자체점검용 로컬 HTTP 서버.

    실제 qoo10.jp Akamai 처럼 urllib(스크립트 UA) 만 523 으로 끊는 /akamai 경로와
    리다이렉트(/redir), 523 응답(/blocked)을 제공한다.
    """
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status, body, extra=None):
            payload = (body or "").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(payload)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(payload)
            except Exception:
                pass

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            ua = self.headers.get("User-Agent", "")
            if path == "/redir":
                self._send(302, "", {"Location": "/ok"})
                return
            if path == "/akamai":
                # urllib(스크립트 UA) 은 523, 브라우저 UA(curl) 는 통과 → 우회 재현
                if "sy_auto-stats-checker" in ua:
                    self._send(523, "<html><body><h1>523 Error connecting to origin</h1></body></html>")
                else:
                    self._send(200, routes["/ok"][1])
                return
            status, body = routes.get(path, (404, "<html><body>not found</body></html>"))
            self._send(status, body)

        def log_message(self, *args):
            return

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def _self_test(log):
    failures = []

    # 쿠키 파서 4형식
    import tempfile
    cases = [
        ({"cookie": "SPC_F=abc; SPC_SI=def", "spc_cds": "35738ed8-bd7b-4d2c-b9c9-fe584ea9fc6e"}, "spc_cds"),
        ({"Cookie": "SPC_F=abc", "SPC_CDS": "35738ed8-bd7b-4d2c-b9c9-fe584ea9fc6e"}, "spc_cds"),
        ({"cookies": {"SPC_F": "abc", "SPC_SI": "def"}}, "spc_cds"),
        ({"cookies": [{"name": "SPC_F", "value": "abc"}]}, "spc_cds"),
    ]
    for blob, _ in cases:
        path = os.path.join(tempfile.gettempdir(), "ck_test.json")
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(blob, fp, ensure_ascii=False)
        parsed = load_shopee_cookies(path)
        if not parsed["ok"] or "SPC_F=abc" not in parsed["cookie"].replace(";;", ";"):
            failures.append("쿠키 형식 파싱 실패: %s" % list(blob.keys()))
    plain = os.path.join(tempfile.gettempdir(), "ck_plain.txt")
    with open(plain, "w", encoding="utf-8") as fp:
        fp.write("SPC_F=abc; SPC_SI=def; SPC_CDS=35738ed8-bd7b-4d2c-b9c9-fe584ea9fc6e")
    parsed = load_shopee_cookies(plain)
    if parsed["cookie"] != "SPC_F=abc; SPC_SI=def; SPC_CDS=35738ed8-bd7b-4d2c-b9c9-fe584ea9fc6e" \
            or parsed["spc_cds"] != "35738ed8-bd7b-4d2c-b9c9-fe584ea9fc6e":
        failures.append("plain 쿠키 헤더 파싱 실패")

    # 응답 판정
    def fake(status, text, ok=True):
        return {"ok": ok, "status": status, "body": text.encode("utf-8"),
                "error": None, "content_type": "text/plain", "url": "u"}
    if classify_shopee_response(fake(200, json.dumps(SAMPLE_CSRF_FAIL)))[0] != "cookie_expired":
        failures.append("csrf 응답을 cookie_expired 로 판정하지 못함")
    if classify_shopee_response(fake(200, json.dumps(SAMPLE_REALTIME)))[0] != "ok":
        failures.append("정상 응답 판정 실패")
    if classify_shopee_response(fake(403, "forbidden", ok=False))[0] != "cookie_expired":
        failures.append("403 을 cookie_expired 로 판정하지 못함")
    if classify_shopee_response(fake(200, "<html><body>Please login</body></html>"))[0] != "cookie_expired":
        failures.append("로그인 HTML 판정 실패")

    # 슬롯 계산
    data = SAMPLE_BOOST_LIST["data"]
    free = data["config"]["total_slots"] - len(data["products"])
    if free != 3:
        failures.append("빈 슬롯 계산 실패")

    # 큐텐 가격 파서(JSON-LD·메타·엔 표기)
    html = """<html><head><title>テスト商品</title>
    <script type="application/ld+json">{"@type":"Product","name":"x","offers":{"price":"3280","priceCurrency":"JPY"}}</script>
    </head><body><span class="price">3,280円</span><p>販売中</p></body></html>"""
    prices = parse_qoo10_price(html)
    if not prices or prices[0]["price"] != 3280 or "jsonld" not in prices[0]["source"]:
        failures.append("큐텐 JSON-LD 가격 파싱 실패")
    html2 = '<html><head><meta itemprop="price" content="2980"></head><body>販売終了</body></html>'
    prices2 = parse_qoo10_price(html2)
    if not prices2 or prices2[0]["price"] != 2980:
        failures.append("큐텐 meta 가격 파싱 실패")
    if any(m in html2 for m in QOO10_BLOCK_MARKERS):
        failures.append("정상 HTML 을 차단으로 오판")
    blocked = "<html><body>523 Error connecting 1.2.3.4</body></html>"
    if not any(m in blocked.lower() for m in QOO10_BLOCK_MARKERS):
        failures.append("523 차단 페이지 감지 실패")

    # --- HTTP 엔진(urllib / curl.exe / curl_cffi) : 로컬 목업 서버로 실측 ---
    #   데이터센터(Linux) IP 는 실제 qoo10.jp 가 막으므로, 같은 현상을 목업으로 재현해
    #   curl 경로 문법(--max-time/-L/-D/-o/-w 파싱), 523 상태코드, UTF-8 본문,
    #   차단→다음 엔진 우회, 엔진 실패→urllib 폴백을 전부 검증한다.
    if parse_curl_probe("200\thttps://www.qoo10.jp/g/1") != (200, "https://www.qoo10.jp/g/1"):
        failures.append("curl -w 출력 파싱 실패(200/URL)")
    if parse_curl_probe("523\thttps://www.qoo10.jp/g/1")[0] != 523:
        failures.append("curl -w 출력 파싱 실패(523)")
    if parse_curl_probe("curl: (7) Failed to connect") != (None, ""):
        failures.append("curl 오류 출력을 상태코드로 오판")
    hdr = _parse_last_headers("HTTP/1.1 301 Moved\r\nLocation: /x\r\n\r\n"
                              "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=UTF-8\r\n\r\n")
    if hdr.get("content-type") != "text/html; charset=UTF-8":
        failures.append("curl 헤더 덤프 마지막 블록 파싱 실패: %r" % (hdr,))

    ok_html = ("<html><head><title>テスト商品</title>"
               "<script type=\"application/ld+json\">{\"@type\":\"Product\",\"offers\":"
               "{\"price\":\"3280\",\"priceCurrency\":\"JPY\"}}</script></head>"
               "<body><span class=\"price\">3,280円</span><p>販売中</p></body></html>")
    akamai_html = "<html><body><h1>523 Error connecting to origin</h1></body></html>"
    srv = base = None
    try:
        srv, base = _start_mock_server({"/ok": (200, ok_html), "/blocked": (523, akamai_html)})
        r_ok = fetch_with_engine("urllib", base + "/ok", timeout=5)
        if not r_ok.get("ok") or r_ok.get("status") != 200 or "3,280" not in decode_body(r_ok):
            failures.append("목업 urllib 조회 실패: %s" % (r_ok.get("error"),))
        if find_curl_exe():
            r_curl = fetch_with_engine("curl", base + "/ok", timeout=5)
            if not (r_curl.get("ok") and r_curl.get("status") == 200
                    and "販売中" in decode_body(r_curl)):
                failures.append("목업 curl 조회 실패(본문/UTF-8): %s" % (r_curl.get("error"),))
            if parse_qoo10_price(decode_body(r_curl))[:1] != [{"price": 3280, "source": "jsonld(JPY)"}]:
                failures.append("목업 curl 본문에서 가격(3280엔) 추출 실패")
            r_blk = fetch_with_engine("curl", base + "/blocked", timeout=5)
            if r_blk.get("status") != 523 or not is_blocked_html(decode_body(r_blk)):
                failures.append("목업 curl 523 상태코드/본문 파싱 실패: status=%r err=%r"
                                % (r_blk.get("status"), r_blk.get("error")))
            r_redir = fetch_with_engine("curl", base + "/redir", timeout=5)
            if not r_redir.get("ok") or r_redir.get("url", "").endswith("/redir") \
                    or "3,280" not in decode_body(r_redir):
                failures.append("목업 curl 리다이렉트(-L) 추적 실패: %r" % (r_redir.get("url"),))
            # urllib 은 523, curl 은 통과 → 자동 우회 경로
            res, _html = fetch_qoo10_page(base + "/akamai", timeout=5,
                                          section={"http_engine": "auto"})
            tried = [a["engine"] for a in (res.get("attempts") or [])]
            if not res.get("ok") or res.get("fetcher") not in ("curl", "cffi"):
                failures.append("차단(Akamai 523) → curl 우회 실패: fetcher=%r tried=%r"
                                % (res.get("fetcher"), tried))
            elif os.name != "nt" and "urllib" not in tried:
                failures.append("차단 시 urllib→curl 순서 기록 실패: %r" % (tried,))
            # curl 실행파일이 없을 때 → urllib 폴백
            saved = _CURL_CACHE.get("path", "__none__")
            _CURL_CACHE["path"] = "/nonexistent/curl.exe"
            try:
                res2, _h2 = fetch_qoo10_page(base + "/ok", timeout=5,
                                            section={"http_engine": "curl"})
            finally:
                if saved == "__none__":
                    _CURL_CACHE.pop("path", None)
                else:
                    _CURL_CACHE["path"] = saved
            if not res2.get("ok") or res2.get("fetcher") != "urllib":
                failures.append("curl 부재 시 urllib 폴백 실패: fetcher=%r err=%r"
                                % (res2.get("fetcher"), res2.get("error")))
            res3, _h3 = fetch_qoo10_page(base + "/blocked", timeout=5,
                                        section={"http_engine": "auto"})
            if not is_blocked_html(_h3 or "") or not res3.get("blocked_page"):
                failures.append("전 엔진 차단 시 차단 페이지 보존 실패(blocked_page 미설정)")
            if not res3.get("attempts"):
                failures.append("엔진 시도 내역(attempts) 미기록")
        else:
            log.warn("curl 실행 파일이 없어 curl 경로 목업 점검은 건너뜀(urllib 폴백만 확인)")
            res2, _h2 = fetch_qoo10_page(base + "/ok", timeout=5)
            if not res2.get("ok"):
                failures.append("curl 부재 환경에서 urllib 폴백 실패")
    except Exception as exc:
        failures.append("HTTP 엔진 목업 점검 예외: %s: %s" % (type(exc).__name__, exc))
    finally:
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass

    # 키워드·발표 문구
    text = "공지사항\n2026년 모두가 도시농부 만족도 조사 당첨자 발표\n당첨자: 김승용(010-****-8972)\n감사합니다."
    hits = keyword_hits(text, DEFAULT_KEYWORDS)
    if not hits or not any(h["keyword"] in ("당첨자", "김승용", "8972") for h in hits):
        failures.append("키워드 추출 실패")
    if not re.search(r"당첨자?\s*(?:발표|안내|공지)|당첨\s*결과", text):
        failures.append("당첨 발표 문구 감지 실패")

    # meta refresh 추출
    m = META_REFRESH_RE.search('<meta http-equiv="refresh" content="0;url=/welcome.do">')
    if not m or m.group(1) != "/welcome.do":
        failures.append("meta refresh 추출 실패")

    # 변화량 계산
    prev = {"last_run_at": "2026-11-27T12:00:00+09:00",
            "regions": {"SG": {"metrics": {"uv": 1}, "boost": {"slots_used": 5}}},
            "qoo10": {"1224104135": {"sale_state": "on_sale", "price": 3280}},
            "watch": {"모두가 도시농부(모두농)": {"hits": []}}}
    payload = {"shopee": {"regions": [{"region": "SG", "ok": True,
                                       "metrics": {"uv": 4}, "boost": {"slots_used": 3}}]},
               "qoo10": {"items": [{"no": "1224104135", "label": "1호", "sale_state": "on_sale",
                                    "price": 3280}]},
               "survey_watch": {"targets": []}}
    ch = compute_changes(prev, payload)
    if ch["regions"]["SG"]["uv"] != 3 or ch["regions"]["SG"]["boost_slots"] != -2:
        failures.append("변화량 계산 실패")

    if failures:
        for f in failures:
            log.error("자체점검 실패: %s" % f)
        return 1
    log.ok("자체점검 통과: 쿠키 5형식 · 응답판정 4종 · 슬롯계산 · 큐텐 가격 3종 · 키워드/발표문구 · "
           "meta refresh · 변화량 · HTTP엔진(curl/urllib/차단우회/폴백)")
    return 0


def _fetch_test(url, config_path=None, log=None):
    """큐텐 URL 1건으로 어떤 HTTP 엔진이 통과하는지 실측(집 PC 확인용)."""
    log = log or Log()
    cfg, _cfg_path = load_config(config_path)
    section = cfg.get("qoo10") or {}
    log.info("사용 가능 엔진: %s" % ", ".join(available_engines()))
    log.info("시도 순서: %s" % " → ".join(qoo10_engine_order(section)))
    log.info("curl 경로: %s" % (find_curl_exe() or "없음"))
    log.info("curl_cffi: %s" % ("설치됨" if curl_cffi_module() else "미설치"))
    log.info("대상: %s" % url)
    res, html = fetch_qoo10_page(url, timeout=_to_int(section.get("timeout"), 25) or 25,
                                 section=section, log=log)
    for attempt in res.get("attempts") or []:
        log.info("  - %s: ok=%s status=%s bytes=%s %s" % (
            attempt.get("engine"), attempt.get("ok"), attempt.get("status"),
            attempt.get("bytes"), attempt.get("error") or ""))
    if res.get("ok") and not res.get("blocked_page"):
        prices = parse_qoo10_price(html)
        log.ok("결과: %s 엔진으로 통과 · HTTP %s · %d자 · 가격 %s" % (
            res.get("fetcher"), res.get("status"), len(html),
            ("%s엔(%s)" % (prices[0]["price"], prices[0]["source"])) if prices else "미확인"))
        return 0
    if res.get("blocked_page"):
        log.error("결과: 전 엔진 차단(Akamai) — 이 IP/네트워크에서는 조회 불가. "
                  "집 PC(Wi-Fi 유선/공유기 IP)에서 --fetch-test 로 재확인 필요")
        return 3
    log.error("결과: 전 엔진 실패/차단 — %s" % (
        res.get("error") or (": " + " ".join(strip_html(html).split())[:140])))
    return 2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="집 PC용 쇼피·큐텐·설문 당첨 확인 통합 경량 조회기 (표준 라이브러리만 사용)")
    parser.add_argument("--dry-run", action="store_true",
                        help="조회는 하되 상태 저장·GitHub 푸시·텔레그램 없이 *.dryrun.json 만 남김")
    parser.add_argument("--config", help="config.json 경로(기본: 스크립트 옆 config.json)")
    parser.add_argument("--out", help="결과 JSON 경로(기본: pc_stats.json)")
    parser.add_argument("--telegram", action="store_true", help="텔레그램 요약 발송")
    parser.add_argument("--no-github", action="store_true", help="config에서 켜져 있어도 GitHub 푸시 안 함")
    parser.add_argument("--push-github", action="store_true", help="config와 무관하게 GitHub 푸시")
    parser.add_argument("--only", help="쉼표 구분: shopee,qoo10,surveys (기본 전부)")
    parser.add_argument("--print-json", action="store_true", help="결과 요약 JSON을 stdout에 출력")
    parser.add_argument("--self-test", action="store_true", help="네트워크 없이 파서·판정 로직 점검")
    parser.add_argument("--fetch-test", nargs="?",
                        const="https://www.qoo10.jp/g/1224104135", metavar="URL",
                        help="큐텐 URL 1건으로 HTTP 엔진(curl/urllib) 실측 — 집 PC 적용 확인용")
    parser.add_argument("--quiet", action="store_true", help="로그 최소화")
    parser.add_argument("--version", action="version", version="stats_checker " + VERSION)
    return parser


def main(argv=None):
    try:  # Windows 콘솔(cp949)에서도 한글·기호가 깨지지 않게
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = build_parser().parse_args(argv)

    if args.self_test:
        code = _self_test(Log(quiet=args.quiet))
        return code

    if args.fetch_test:
        return _fetch_test(args.fetch_test, args.config, Log(quiet=args.quiet))

    push = True if args.push_github else (False if args.no_github else None)
    only = [part.strip() for part in args.only.split(",")] if args.only else None
    summary = run_once(dry_run=args.dry_run, config_path=args.config, out_path=args.out,
                       telegram=args.telegram, push_github=push,
                       quiet=args.quiet or args.print_json, only=only)
    if args.print_json:
        print(json.dumps({"counts": summary["counts"], "changes": summary.get("changes"),
                          "warnings": summary["warnings"][:5],
                          "errors": summary["errors"][:5],
                          "output": summary.get("output")}, ensure_ascii=False))
    else:
        print("")
        counts = summary["counts"]
        print("요약: 쇼피 %d/8국(쿠키 %s) / 큐텐 %d/%d / 오늘 발표 %d건 / 알림 %d / 경고 %d / 오류 %d"
              % (counts["shopee_regions_ok"], counts["shopee_cookie_state"],
                 counts["qoo10_ok"], (summary["qoo10"].get("counts") or {}).get("total", 0),
                 counts["watch_due"], counts["watch_alerts"],
                 counts["warnings"], counts["errors"]))
        if summary.get("output"):
            print("결과 파일: %s" % summary["output"])
        for alert in (summary["survey_watch"].get("alerts") or []):
            print("  - [%s] %s: %s" % (alert["level"], alert["target"], alert["reason"]))
        for err in summary["errors"][:5]:
            print("  ! %s" % err)

    ok_any = (summary["counts"]["shopee_regions_ok"] > 0
              or summary["counts"]["qoo10_ok"] > 0
              or summary["survey_watch"].get("targets"))
    return 0 if ok_any and not summary["errors"] else 2


if __name__ == "__main__":
    sys.exit(main())
