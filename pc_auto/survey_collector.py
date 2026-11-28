#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
survey_collector.py — 집 PC(C:\\sy_auto) 경품 설문·퀴즈 새 링크·마감일 수집기
==============================================================================

만든 목적
  인1(김승용22 / @kimseungyong22)이 매일 11:30에 하던 '경품 설문·퀴즈 새 링크 찾기'를
  집 PC(C:\\sy_auto, 상시 가동)로 이관해 사용량·시간을 아끼기 위한 수집 스크립트.
  수집만 담당한다. 실제 응모(폼 제출)는 하지 않는다.

수집 소스
  1) https://today-event-news.blogspot.com/feeds/posts/default?alt=rss  (RSS XML)
     - 제목 / 링크 / 발행일 / 본문 HTML / 본문 내 외부 설문 URL / 마감일 / 경품 문구 추출
  2) 스레드 @ddalkkak_event, @professional_jung (경량 GET)
     - meta 태그·내장 JSON 안의 최근 포스트 텍스트와 외부 URL 추출 시도
     - JS 렌더링/로그인 차단으로 본문이 안 오면 warning만 남기고 계속 진행(죽지 않음)
  3) https://today-joopjoop.vercel.app/data/events.json  ('오늘의 줍줍판' JSON)
     - auth == "free"(비로그인) · 마감 전 · 마감 max_days(기본 14일) 이내만 사용
     - 인스타그램/유튜브 URL(SNS 팔로우·댓글·구독 인증), 지역 제한, 댓글·인증샷 게시 요구는 제외
     - 블로그 본문형·퀴즈형은 review(사람 확인)로 분류

출력
  - pc_survey_candidates.json      새 후보(중복 제거 후) + 상태·skip_reason·마감일
  - survey_seen.json               중복 방지 상태(본 guid / 기사 URL / 제목 정규화 / 설문 URL)
  - last_run.json                  마지막 실행 요약(감시 스크립트가 읽기 좋은 형태)
  - (옵션) GitHub sy-dashboard 저장소 pc_survey_candidates.json 에 병합 푸시
  - (옵션, --telegram) 텔레그램 요약 발송

의존성
  Python 3.7+ 표준 라이브러리만 사용. pip install 불필요.

사용 예
  python survey_collector.py --dry-run            # 파싱·필터·JSON 형식 검증(상태 저장·전송 안 함)
  python survey_collector.py                      # 실제 실행(로컬 저장 + 설정 시 GitHub 푸시)
  python survey_collector.py --telegram           # 텔레그램 요약까지
  python survey_collector.py --import-history ..\\history.json --import-seen ..\\rss.json
  python survey_collector.py --print-json         # 감시 스크립트용 결과 JSON을 stdout에 출력

토큰·비밀번호는 코드에 넣지 않는다. config.json 또는 환경변수에서만 읽는다.
  GITHUB_TOKEN / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
"""

from __future__ import print_function

import argparse
import base64
import datetime
import hashlib
import email.utils
import html as _html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

VERSION = "1.1.0"
KST = datetime.timezone(datetime.timedelta(hours=9))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 sy_auto-survey-collector/" + VERSION)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_RSS = "https://today-event-news.blogspot.com/feeds/posts/default?alt=rss&max-results={n}"
DEFAULT_THREADS = [
    "https://www.threads.com/@ddalkkak_event",
    "https://www.threads.com/@professional_jung",
]
DEFAULT_JOOPJOOP = "https://today-joopjoop.vercel.app/data/events.json"

DEFAULT_CONFIG = {
    "output_path": "pc_survey_candidates.json",
    "state_path": "survey_seen.json",
    "known_urls_path": "known_urls.json",
    "last_run_path": "last_run.json",
    "rss_max_results": 25,
    "joopjoop": {
        "enabled": True,
        "url": DEFAULT_JOOPJOOP,
        "max_days": 14,        # 오늘부터 이 일수 안에 마감하는 건만 후보로
        "max_items": 400,
        "require_free": True,  # auth == "free"(비로그인) 건만
    },
    "fresh_days": 45,          # 발행 후 이 일수를 넘긴 글은 '오래됨'으로 건너뜀
    "keep_seen_days": 180,     # 중복 상태 보관 기간
    "max_candidates": 120,     # 한 번에 출력할 최대 후보 수(줍줍판 추가로 80→120)
    "profile": {"gender": "남성", "birth_year": 1983, "age_note": "만 43세(1983년생)", "region": "서울 노원구"},
    "github": {
        "enabled": False, "owner": "", "repo": "sy-dashboard",
        "path": "pc_survey_candidates.json", "branch": "main",
        "token_env": "GITHUB_TOKEN", "commit_message": "chore: PC 설문 수집 후보 갱신",
    },
    "telegram": {"enabled": False, "token_env": "TELEGRAM_BOT_TOKEN", "chat_id_env": "TELEGRAM_CHAT_ID"},
}

# ---------------------------------------------------------------------------
# 로그
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


def _iso(dt):
    if dt is None:
        return None
    return dt.astimezone(KST).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# HTTP (표준 라이브러리만)
# ---------------------------------------------------------------------------

def http_request(url, method="GET", data=None, headers=None, timeout=25, retries=2, log=None):
    """돌려주는 값: {"ok": bool, "status": int|None, "body": bytes, "error": str|None, "content_type": str}"""
    hdrs = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
            "Accept-Encoding": "identity", "Cache-Control": "no-cache"}
    if headers:
        hdrs.update(headers)
    last = {"ok": False, "status": None, "body": b"", "error": "not executed", "content_type": ""}
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                return {"ok": True, "status": getattr(resp, "status", 200), "body": body,
                        "error": None, "content_type": resp.headers.get("Content-Type", "")}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read()[:300].decode("utf-8", "replace")
            except Exception:
                pass
            last = {"ok": False, "status": exc.code, "body": b"",
                    "error": "HTTP %s %s" % (exc.code, detail.strip()), "content_type": ""}
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                if log:
                    log.warn("HTTP %s, %d초 후 재시도: %s" % (exc.code, 2 * (attempt + 1), url))
                time.sleep(2 * (attempt + 1))
                continue
            return last
        except Exception as exc:  # URLError, timeout, ssl 등
            last = {"ok": False, "status": None, "body": b"",
                    "error": "%s: %s" % (type(exc).__name__, exc), "content_type": ""}
            if attempt < retries:
                if log:
                    log.warn("%s, 재시도 %d/%d: %s" % (type(exc).__name__, attempt + 1, retries, url))
                time.sleep(1.5 * (attempt + 1))
                continue
            return last
    return last


def decode_body(result):
    charset = ""
    m = re.search(r"charset=([\w\-]+)", result.get("content_type") or "", re.I)
    if m:
        charset = m.group(1)
    raw = result.get("body") or b""
    for enc in [charset, "utf-8", "cp949"]:
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# 텍스트/URL 유틸
# ---------------------------------------------------------------------------

def strip_html(raw_html):
    if not raw_html:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw_html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h[1-6]|li|tr)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = _html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


URL_RE = re.compile(r"https?://[^\s\"'<>()\[\]{}]+", re.I)


def canonical_url(url):
    if not url:
        return ""
    u = _html.unescape(url.strip())
    u = u.rstrip(".,;)]}\"'")
    try:
        parts = urllib.parse.urlsplit(u)
    except Exception:
        return u.lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    query_pairs = []
    for key, val in urllib.parse.parse_qsl(parts.query, keep_blank_values=False):
        if key.lower().startswith("utm_") or key.lower() in ("fbclid", "gclid", "igshid", "ref", "ref_src"):
            continue
        query_pairs.append((key, val))
    query = urllib.parse.urlencode(query_pairs)
    path = parts.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((parts.scheme or "https", host, path, query, ""))


NOISE_HOSTS = (
    "blogger.com", "blogspot.com", "google-analytics.com", "googletagmanager.com",
    "schema.org", "w3.org", "instagram.com", "cdninstagram.com", "facebook.com",
    "threads.com", "threads.net", "twitter.com", "x.com", "youtube.com", "youtu.be",
    "kakao.com", "tistory.com", "google.com/url", "gstatic.com", "googleusercontent.com",
    "naver.com/blog", "m.blog.naver.com", "band.us", "t.me",
)

# 설문·응모로 볼 가능성이 높은 호스트(점수)
FORM_HOST_SCORES = [
    ("forms.gle", 90), ("docs.google.com/forms", 90), ("form.naver.com", 90),
    ("naver.me", 70), ("surveypro.co.kr", 85), ("moaform.com", 85),
    ("epeople.go.kr", 80), ("form.office.naver.com", 85), ("surveymonkey.com", 80),
    ("naver.com", 40), ("kakao.com", 30), ("forms.office.com", 80),
    ("survey.naver.com", 85), ("qualtrics.com", 70), ("typeform.com", 70),
]


def is_noise_url(url):
    low = url.lower()
    if low.startswith("mailto:") or low.startswith("javascript:"):
        return True
    for host in NOISE_HOSTS:
        if host in low:
            return True
    if re.search(r"\.(css|js|png|jpe?g|gif|svg|ico|woff2?|ttf)(\?|$)", low):
        return True
    return False


def extract_urls(raw_html, plain_text=""):
    found = []
    for m in URL_RE.finditer(raw_html or ""):
        found.append(m.group(0))
    for m in URL_RE.finditer(plain_text or ""):
        found.append(m.group(0))
    # href="..." 형태는 위 정규식으로 잡히지만, &amp; 로 인코딩된 경우 대비
    seen, out = set(), []
    for raw in found:
        url = _html.unescape(raw).rstrip(".,;)]}\"'")
        if is_noise_url(url):
            continue
        key = canonical_url(url)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out


def pick_survey_url(urls):
    """설문/응모 링크로 가장 그럴듯한 URL 하나를 고른다(없으면 빈 문자열)."""
    best, best_score = "", -1
    for idx, url in enumerate(urls):
        low = url.lower()
        score = 0
        for host, weight in FORM_HOST_SCORES:
            if host in low:
                score = max(score, weight)
        if re.search(r"(survey|qstnr|response|form|apply|event|thinkbox|quiz)", low):
            score += 15
        if "blogspot" in low or "today-event-news" in low:
            score -= 100
        score -= idx  # 앞에 나온 링크 우선
        if score > best_score:
            best, best_score = url, score
    return best if best_score > -50 else ""


# ---------------------------------------------------------------------------
# 마감일·경품 추출
# ---------------------------------------------------------------------------

def _infer_date(year, month, day, ref_dt):
    """연도가 없는 'M월 D일'을 발행일 기준으로 보정."""
    try:
        dt = datetime.date(year, month, day)
    except ValueError:
        return None
    if year == ref_dt.year and dt < (ref_dt.date() - datetime.timedelta(days=60)):
        try:
            dt = datetime.date(year + 1, month, day)
        except ValueError:
            return None
    return dt


def extract_deadline(title, text, published_dt):
    """제목·본문에서 마감일(YYYY-MM-DD)을 고른다. 못 찾으면 None."""
    ref = published_dt or _now()
    candidates = []

    for m in re.finditer(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", text or ""):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        tail = (text or "")[m.end():m.end() + 25]
        head = (text or "")[max(0, m.start() - 15):m.start()]
        if re.search(r"까지|마감|까지입니다", tail) or re.search(r"마감", head):
            dt = _infer_date(y, mo, d, ref)
            if dt:
                candidates.append(dt)

    for m in re.finditer(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text or ""):
        mo, d = int(m.group(1)), int(m.group(2))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        tail = (text or "")[m.end():m.end() + 22]
        if re.search(r"까지|마감|종료", tail):
            dt = _infer_date(ref.year, mo, d, ref)
            if dt:
                candidates.append(dt)

    # '~12/06', '12.06 마감', '마감: 11/27' 같은 축약 표기
    for m in re.finditer(r"(~\s*)?(\d{1,2})\s*[/.]\s*(\d{1,2})\s*(마감|까지|종료)?", text or ""):
        mo, d = int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        before = (text or "")[max(0, m.start() - 6):m.start()]
        if m.group(1) or m.group(4) or re.search(r"마감\s*[:：]?\s*$", before):
            dt = _infer_date(ref.year, mo, d, ref)
            if dt:
                candidates.append(dt)

    # 제목: "... 11월 29일 마감" / "... ~11/29"
    for m in re.finditer(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", title or ""):
        dt = _infer_date(ref.year, int(m.group(1)), int(m.group(2)), ref)
        if dt:
            candidates.append(dt)
    for m in re.finditer(r"(\d{1,2})\s*/\s*(\d{1,2})\s*(마감|까지)", title or ""):
        dt = _infer_date(ref.year, int(m.group(1)), int(m.group(2)), ref)
        if dt:
            candidates.append(dt)

    if not candidates:
        return None
    # 발행일 근처 이후 날짜 중 가장 늦은 것을 마감일로 본다
    floor = (ref.date() - datetime.timedelta(days=1))
    valid = [c for c in candidates if c >= floor]
    pick = max(valid) if valid else max(candidates)
    return pick.isoformat()


def extract_deadline_text(title, text):
    for source in (title or "", text or ""):
        for m in re.finditer(r"[^\n.]{0,30}?(\d{1,2}\s*월\s*\d{1,2}\s*일[^\n.]{0,20}?(까지|마감))", source):
            return m.group(0).strip()[:80]
        for m in re.finditer(r"[^\n.]{0,30}?(\d{1,2}\s*/\s*\d{1,2}[^\n.]{0,10}?(까지|마감))", source):
            return m.group(0).strip()[:80]
    return ""


PRIZE_NOUN = r"(?:모바일\s*상품권|상품권|기프티콘|기프트콘|교환권|쿠폰|아메리카노|커피|경품|포인트|세트|상품)"


def _prize_snippet(line):
    """경품 관련 낱말 앞 12자부터 90자만 잘라 '리원 가치체계...' 같은 꼬리표를 없앤다."""
    m = re.search(PRIZE_NOUN, line)
    if not m:
        return ""
    start = max(0, m.start() - 12)
    snippet = line[start:start + 100]
    snippet = re.sub(r"^[\s,·\-–]+|\s+", " ", snippet).strip()
    return snippet


def extract_prize(title, text):
    best, best_score = "", 0
    lines = [line.strip() for line in (text or "").split("\n") if line.strip()]
    if title:
        lines.insert(0, title.strip())
    for line in lines:
        if not re.search(PRIZE_NOUN, line):
            continue
        score = 0
        if re.search(r"\d[\d,]*\s*(?:원|만원|천원)", line):
            score += 2
        if re.search(r"\d[\d,]*\s*명", line):
            score += 2
        if re.search(r"(경품|추첨|드립니다|줍니다)", line):
            score += 1
        if line.startswith("주최") or len(line) < 8:
            score -= 2
        if len(line) > 90:
            score -= 1
        if score > best_score:
            snippet = _prize_snippet(line)
            if snippet:
                best, best_score = snippet, score
    return best[:120]


# ---------------------------------------------------------------------------
# 자격 1차 필터 (만 43세 남성 · 서울 노원구 거주 기준)
# ---------------------------------------------------------------------------

LOGIN_SIGNALS = [
    r"로그인\s*필수", r"로그인\s*후", r"로그인한\s*뒤", r"로그인\s*해야", r"로그인\s*하고\s*참여",
    r"간편인증\s*로그인", r"간편인증으로\s*로그인", r"간편인증\s*(필요|인증)", r"본인인증\s*(필수|필요|후|을\s*거쳐)",
    r"PASS\s*인증", r"휴대폰\s*본인인증", r"회원가입\s*(필수|후)\s", r"네이버\s*로그인", r"카카오\s*로그인",
]
# '회원가입 없이'만으로는 로그인 불필요로 보지 않는다(간편인증 로그인은 회원가입 없는 로그인이라 별개)
NO_LOGIN_SIGNALS = [
    r"로그인[·,\s]*인증\s*없", r"로그인\s*없이", r"로그인\s*없음", r"로그인\s*불필요",
    r"인증\s*없이\s*(바로\s*)?(참여|응모)", r"비회원\s*(으로)?\s*(바로|참여|응모)",
]

SKIP_RULES = [
    ("여성 전용·여성 대상", [r"여성\s*전용", r"여성만", r"여성\s*대상", r"여성\s*참여자"]),
    ("청년 연령 제한", [r"청년\s*만", r"청년\s*대상", r"만\s*19\s*[~\-]\s*34\s*세", r"만\s*34\s*세\s*이하",
                        r"만\s*39\s*세\s*이하", r"만\s*35\s*세\s*이하"]),
    ("대학생·재학생 전용", [r"대학생\s*만", r"대학생\s*대상", r"재학생\s*만", r"학생\s*증\s*필수"]),
    ("임산부·출산 가정 전용", [r"임산부\s*(만|대상|전용)", r"임신부\s*대상", r"출산\s*가정\s*대상"]),
    ("지역 주민 제한", [r"[가-힣]{1,5}구\s*주민\s*만", r"[가-힣]{1,5}시\s*주민\s*만", r"거주자\s*만\s*참여",
                        r"지역\s*주민만"]),
    ("유료·구매 필요", [r"구매\s*필수", r"결제\s*후\s*참여", r"유료\s*참여"]),
    ("어린이·미성년 대상", [r"어린이\s*대상", r"초등학생\s*대상", r"미성년자\s*대상"]),
]

REVIEW_RULES = [
    ("연령 상한 확인", [r"만\s*\d{1,2}\s*세\s*(이하|미만)", r"\d{2}\s*세\s*미만"]),
    ("앱 설치·가입 요구 가능", [r"앱\s*설치(?![^.\n]{0,16}없)", r"앱\s*다운로드(?![^.\n]{0,16}없)",
                          r"회원\s*가입(?![^.\n]{0,16}없)"]),
    ("매장 방문 필요 가능", [r"방문\s*필수", r"현장\s*참여", r"오프라인\s*참여"]),
]


def eligibility_check(title, text, profile=None):
    """돌려주는 값: {"status": candidate|review|skip, "skip_reason": str, "flags": [..], "requires_login": bool}"""
    haystack = ((title or "") + "\n" + (text or ""))
    flags = []
    status = "candidate"
    reasons = []

    requires_login = any(re.search(p, haystack, re.I) for p in LOGIN_SIGNALS)
    no_login = any(re.search(p, haystack, re.I) for p in NO_LOGIN_SIGNALS)
    if requires_login and not no_login:
        status = "skip"
        reasons.append("로그인·본인인증 필요(자동응모 불가)")
    flags.append("login_required" if (requires_login and not no_login) else "login_not_required")

    for label, patterns in SKIP_RULES:
        for pat in patterns:
            m = re.search(pat, haystack, re.I)
            if m:
                status = "skip"
                reasons.append("%s('%s')" % (label, m.group(0).strip()))
                flags.append(label.replace(" ", "_"))
                break

    if status != "skip":
        for label, patterns in REVIEW_RULES:
            for pat in patterns:
                m = re.search(pat, haystack, re.I)
                if m:
                    status = "review"
                    reasons.append("%s 확인('%s')" % (label, m.group(0).strip()))
                    flags.append(label.replace(" ", "_"))
                    break

    return {
        "status": status,
        "skip_reason": " / ".join(reasons),
        "flags": flags,
        "requires_login": bool(requires_login and not no_login),
    }


# 줍줍판 구조화 데이터(org/cat/who/url)에 대한 추가 판정
JOOP_REGION_RE = re.compile(
    r"([가-힣]{2,6}(?:특별시|광역시|특별자치시|특별자치도|시|군|구|도))\s*(?:시민|군민|구민|도민|주민|민)(?!\s*누구나)")
JOOP_PUBLIC_POST_RE = re.compile(r"(댓글|인증샷|게시물?\s*인증|구독\s*인증|팔로우\s*인증|구독자\s*이벤트)")


def joop_extra_check(entry):
    """줍줍판 항목의 URL·문구로 응모 불가/검토 사유를 돌려준다(없으면 None)."""
    url = (entry.get("survey_url") or entry.get("link") or "").strip().lower()
    text = " ".join([entry.get("title") or "", entry.get("plain_text") or ""])
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        host = ""
    if "instagram.com" in host:
        return {"status": "skip", "reason": "인스타그램 팔로우·댓글 인증 필요(URL)", "flag": "sns_required"}
    if host.endswith("youtube.com") or host.endswith("youtu.be"):
        return {"status": "skip", "reason": "유튜브 구독·댓글 인증 필요(URL)", "flag": "sns_required"}
    m = JOOP_REGION_RE.search(text)
    if m:
        return {"status": "skip", "reason": "지역 제한('%s')" % m.group(0).strip(), "flag": "region_limited"}
    m = JOOP_PUBLIC_POST_RE.search(text)
    if m:
        return {"status": "skip", "reason": "SNS 댓글·인증샷 게시 필요('%s')" % m.group(0).strip(),
                "flag": "public_post_required"}
    joop = entry.get("joop") or {}
    if "blog.naver.com" in host:
        return {"status": "review", "reason": "블로그 본문에 응모 폼 링크가 있는지 확인 필요", "flag": "blog_link_check"}
    if joop.get("cat") == "퀴즈" or joop.get("quiz"):
        return {"status": "review", "reason": "퀴즈 정답 확인 필요", "flag": "quiz_answer_check"}
    return None


# ---------------------------------------------------------------------------
# 수집: RSS
# ---------------------------------------------------------------------------

def _localname(tag):
    return tag.rsplit("}", 1)[-1] if tag else ""


def _child_text(node, name):
    for child in list(node):
        if _localname(child.tag) == name:
            return (child.text or "")
    return ""


def _child_texts(node, name):
    out = []
    for child in list(node):
        if _localname(child.tag) == name:
            out.append((child.text or "").strip())
    return out


def fetch_rss(url, log):
    res = http_request(url, log=log)
    if not res["ok"]:
        return None, {"name": "today-event-news-rss", "url": url, "ok": False,
                      "status": res.get("status"), "error": res.get("error"), "items": 0}
    text = decode_body(res)
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except Exception as exc:
        return None, {"name": "today-event-news-rss", "url": url, "ok": False,
                      "status": res.get("status"), "error": "XML 파싱 실패: %s" % exc, "items": 0}

    channel = root.find("channel")
    nodes = channel.findall("item") if channel is not None else root.findall(".//item")
    items = []
    for node in nodes:
        guid = _child_text(node, "guid") or _child_text(node, "id")
        link = _child_text(node, "link")
        title = strip_html(_child_text(node, "title"))
        description = _child_text(node, "description")
        pub_raw = _child_text(node, "pubDate")
        published = None
        if pub_raw:
            try:
                published = email.utils.parsedate_to_datetime(pub_raw)
            except Exception:
                published = None
        if published is not None and published.tzinfo is None:
            published = published.replace(tzinfo=datetime.timezone.utc)
        categories = _child_texts(node, "category")
        plain = strip_html(description)
        items.append({
            "guid": guid or link,
            "title": title,
            "link": link,
            "published_at": _iso(published) if published else None,
            "published_dt": published,
            "categories": categories,
            "description_html": description,
            "plain_text": plain,
        })
    return items, {"name": "today-event-news-rss", "url": url, "ok": True,
                   "status": res.get("status"), "error": None, "items": len(items)}


# ---------------------------------------------------------------------------
# 수집: 스레드(경량 GET, 실패 허용)
# ---------------------------------------------------------------------------

THREADS_EVENT_RE = re.compile(r"(설문|퀴즈|경품|이벤트|응모|추첨)")
THREADS_BOILERPLATE = re.compile(r"^(응모\s*링크는\s*프로필|링크는\s*(첫|프로필)|정답은\s*댓글)", re.I)


def _threads_request(url, log):
    """스레드는 기본 UA로는 JS 껍데기만 준다. 검색엔진 봇 UA로 받으면 포스트 JSON이 포함된다."""
    attempts = [
        "Googlebot/2.1 (+http://www.google.com/bot.html)",
        "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
        UA,
    ]
    last = None
    for agent in attempts:
        res = http_request(url, headers={"User-Agent": agent}, timeout=30, retries=1, log=log)
        if res["ok"]:
            text = decode_body(res)
            if '"caption"' in text or '"taken_at"' in text:
                return res, text, agent
            last = (res, text, agent)
        else:
            last = (res, "", agent)
    return last


def _json_string(raw):
    try:
        return json.loads('"' + raw + '"')
    except Exception:
        return None


def collect_threads(url, log):
    """프로필 페이지의 내장 JSON에서 최근 포스트(본문·작성시각·permalink)를 뽑는다."""
    handle = url.rsplit("/", 1)[-1].strip().lstrip("@")
    meta = {"name": "threads:@" + handle, "url": url, "ok": False,
            "status": None, "error": None, "items": 0}
    try:
        res, text, agent = _threads_request(url, log)
    except Exception as exc:
        meta["error"] = "%s: %s" % (type(exc).__name__, exc)
        log.warn("스레드 수집 실패(계속 진행): %s — %s" % (url, meta["error"]))
        return [], meta
    if not res or not res.get("ok"):
        meta["status"] = (res or {}).get("status")
        meta["error"] = (res or {}).get("error") or "요청 실패"
        log.warn("스레드 수집 실패(계속 진행): %s — %s" % (url, meta["error"]))
        return [], meta
    meta["status"] = res.get("status")

    posts, seen_codes = [], set()
    for m in re.finditer(r'"caption":\{"text":"((?:[^"\\]|\\.){10,4000})"', text):
        body = _json_string(m.group(1))
        if not body:
            continue
        if not THREADS_EVENT_RE.search(body) or THREADS_BOILERPLATE.search(body.strip()):
            continue
        prefix = text[max(0, m.start() - 40000):m.start()]
        suffix = text[m.end():m.end() + 3000]
        codes = re.findall(r'"code"\s*:\s*"([A-Za-z0-9_\-]{8,20})"', prefix)
        if not codes:
            codes = re.findall(r'"code"\s*:\s*"([A-Za-z0-9_\-]{8,20})"', suffix)
        times = re.findall(r'"taken_at"\s*:\s*(\d{10})', prefix)
        code = codes[-1] if codes else normalize_title(body)[:24]
        if code in seen_codes:
            continue
        seen_codes.add(code)
        published = None
        if times:
            try:
                published = datetime.datetime.fromtimestamp(int(times[-1]), KST)
            except Exception:
                published = None
        permalink = "https://www.threads.com/@%s/post/%s" % (handle, code) if re.match(r"^[A-Za-z0-9_\-]+$", code) else url
        first_line = re.sub(r"\s+", " ", body.strip())
        posts.append({
            "guid": "threads:" + code,
            "title": first_line[:110],
            "link": permalink,
            "published_at": _iso(published),
            "published_dt": published,
            "categories": ["threads"],
            "description_html": "",
            "plain_text": body,
            "survey_url": pick_survey_url(extract_urls("", body)),
            "other_urls": extract_urls("", body),
        })

    meta["items"] = len(posts)
    meta["ok"] = bool(posts)
    meta["agent"] = agent
    if not posts:
        korean = len(re.findall(r"[가-힣]{2,}", text))
        meta["error"] = ("포스트 0건(응답 %d bytes, 한글 %d자): JS 렌더링/로그인 차단 추정"
                         % (len(text), korean))
        log.warn("스레드 수집 실패(계속 진행): %s — %s" % (url, meta["error"]))
    else:
        log.ok("스레드 %s: 포스트 %d건 수집(%s)" % (handle, len(posts), agent.split("/")[0]))
    return posts, meta


# ---------------------------------------------------------------------------
# 수집: 줍줍판 JSON (오늘의 줍줍판 · 비로그인 이벤트 목록)
# ---------------------------------------------------------------------------

def collect_joopjoop(url, log, max_days=14, max_items=400, require_free=True):
    """events.json을 받아 오늘 기준 마감 전(max_days 이내) 이벤트만 entry 형식으로 돌려준다."""
    meta = {"name": "joopjoop-json", "url": url, "ok": False,
            "status": None, "error": None, "items": 0, "filtered": 0}
    res = http_request(url, log=log)
    if not res.get("ok"):
        meta["status"] = res.get("status")
        meta["error"] = res.get("error") or "요청 실패"
        log.warn("줍줍판 수집 실패(계속 진행): %s" % meta["error"])
        return [], meta
    meta["status"] = res.get("status")
    try:
        data = json.loads(decode_body(res))
    except Exception as exc:
        meta["error"] = "JSON 파싱 실패: %s" % exc
        log.warn("줍줍판 수집 실패(계속 진행): %s" % meta["error"])
        return [], meta
    if isinstance(data, dict):
        data = data.get("events") or data.get("items") or data.get("data") or []
    if not isinstance(data, list):
        meta["error"] = "목록 형식 아님(%s)" % type(data).__name__
        log.warn("줍줍판 수집 실패(계속 진행): %s" % meta["error"])
        return [], meta

    today = _now().date()
    limit_date = today + datetime.timedelta(days=int(max_days))
    out = []
    for ev in data:
        if len(out) >= int(max_items):
            break
        if not isinstance(ev, dict):
            continue
        auth = (ev.get("auth") or "").strip().lower()
        if require_free and auth not in ("free", ""):
            meta["filtered"] += 1
            continue
        deadline = (ev.get("deadline") or "").strip()
        deadline_date = None
        try:
            deadline_date = datetime.date.fromisoformat(deadline[:10])
        except Exception:
            deadline_date = None
        if deadline_date is not None and (deadline_date < today or deadline_date > limit_date):
            meta["filtered"] += 1
            continue
        link = (ev.get("url") or "").strip()
        title = (ev.get("title") or "").strip()
        if not link or not title:
            meta["filtered"] += 1
            continue
        org = (ev.get("org") or "").strip()
        cat = (ev.get("cat") or "").strip()
        prize = (ev.get("prize") or "").strip()
        who = (ev.get("who") or "").strip()
        auth_label = (ev.get("authLabel") or "").strip()
        quiz = ev.get("quiz") if isinstance(ev.get("quiz"), dict) else None
        quiz_text = ""
        if quiz:
            quiz_text = "퀴즈 정답 확인 필요"
        elif ev.get("quizPending"):
            quiz_text = "퀴즈 정답 확인 필요"
        plain = " ".join([x for x in [
            ("기관 " + org) if org else "",
            ("분류 " + cat) if cat else "",
            ("마감 " + deadline) if deadline else "",
            ("경품 " + prize) if prize else "",
            ("대상 " + who) if who else "",
            ("참여방법 " + auth_label) if auth_label else "",
            quiz_text,
        ] if x])
        guid = "joop:" + hashlib.sha1((link + "|" + title).encode("utf-8")).hexdigest()[:16]
        out.append({
            "guid": guid,
            "title": title,
            "link": link,
            "published_at": None,
            "published_dt": None,
            "categories": ["joopjoop"] + ([cat] if cat else []),
            "description_html": "",
            "plain_text": plain,
            "survey_url": link,
            "other_urls": [link],
            "preset_deadline": deadline or None,
            "preset_prize": prize or None,
            "joop": {"org": org, "cat": cat, "who": who, "auth": ev.get("auth"),
                     "dday": ev.get("dday"), "urgent": ev.get("urgent"),
                     "rank": ev.get("rank"), "quiz": bool(quiz or ev.get("quizPending"))},
        })

    meta["items"] = len(out)
    meta["ok"] = True
    if out:
        log.ok("줍줍판: 이벤트 %d건 수집(원본 %d건 중 마감·비로그인 조건 통과)"
               % (len(out), len(data)))
    else:
        log.warn("줍줍판: 조건 통과 0건(원본 %d건)" % len(data))
    return out, meta


# ---------------------------------------------------------------------------
# 상태·설정 파일
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


def load_config(path=None):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if path:
        # 지정 경로는 현재 작업 디렉터리 기준을 먼저 보고, 없으면 스크립트 옆으로 본다
        candidate = os.path.abspath(path)
        config_path = candidate if os.path.exists(candidate) else resolve_path(path)
    else:
        config_path = resolve_path("config.json")
    raw = load_json(config_path, default=None)
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    return cfg, (config_path if isinstance(raw, dict) else None)


def load_state(path):
    state = load_json(path, default=None) or {}
    state.setdefault("version", 1)
    for key in ("seen_ids", "seen_urls", "seen_titles"):
        value = state.get(key)
        if isinstance(value, list):
            state[key] = {str(item): None for item in value}
        elif not isinstance(value, dict):
            state[key] = {}
    state.setdefault("stats", {})
    return state


def prune_state(state, keep_days):
    cutoff = (_now() - datetime.timedelta(days=keep_days)).isoformat(timespec="seconds")
    for key in ("seen_ids", "seen_urls", "seen_titles"):
        bucket = state.get(key) or {}
        for item_key in list(bucket.keys()):
            stamp = bucket.get(item_key)
            if stamp and stamp < cutoff:
                bucket.pop(item_key, None)


# ---------------------------------------------------------------------------
# 인1 데이터 가져오기 (history.json / state/rss.json)
# ---------------------------------------------------------------------------

def collect_known_urls(known_path):
    """known_urls.json + (선택) 인1 history.json 에서 기응모 URL·제목을 모은다."""
    urls, titles, ids = set(), set(), set()

    data = load_json(known_path, default=None)
    if isinstance(data, dict):
        for value in data.get("urls", []) or []:
            for found in (URL_RE.findall(str(value)) or [str(value)]):
                urls.add(canonical_url(found))
        for value in data.get("titles", []) or []:
            titles.add(normalize_title(value))
        for value in data.get("ids", []) or []:
            ids.add(str(value))
    elif isinstance(data, list):
        for value in data:
            if isinstance(value, str):
                for found in (URL_RE.findall(value) or [value]):
                    urls.add(canonical_url(found))
    return {"urls": urls, "titles": titles, "ids": ids}


def import_history(path, log):
    """인1 work/survey-auto/history.json → {urls, titles, ids}"""
    data = load_json(path, default=None)
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        log.warn("history.json 형식이 예상과 달라 건너뜀: %s" % path)
        return {"urls": set(), "titles": set(), "ids": set()}
    urls, titles, ids = set(), set(), set()
    for entry in data["entries"]:
        if not isinstance(entry, dict):
            continue
        if entry.get("id"):
            ids.add(str(entry["id"]))
        if entry.get("title"):
            titles.add(normalize_title(entry["title"]))
        raw = entry.get("url") or ""
        for piece in re.split(r"\s*[()]\s*", raw):
            for found in URL_RE.findall(piece):
                urls.add(canonical_url(found))
        for found in URL_RE.findall(raw):
            urls.add(canonical_url(found))
    log.info("history.json 불러옴: 기응모 %d건(URL %d개)" % (len(data["entries"]), len(urls)))
    return {"urls": urls, "titles": titles, "ids": ids}


def import_seen_rss(path, log):
    """인1 scheduled-actions/.../state/rss.json 의 seen_ids 를 그대로 이어받는다."""
    data = load_json(path, default=None)
    if not isinstance(data, dict):
        log.warn("rss.json 형식이 예상과 달라 건너뜀: %s" % path)
        return set()
    ids = data.get("seen_ids") or []
    if isinstance(ids, dict):
        ids = list(ids.keys())
    log.info("rss.json seen_ids 불러옴: %d건" % len(ids))
    return set(str(item) for item in ids)


def normalize_title(title):
    if not title:
        return ""
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", title).lower()


# ---------------------------------------------------------------------------
# 핵심: 1회 수집
# ---------------------------------------------------------------------------

def _age_days(published_dt):
    if not published_dt:
        return None
    return max(0, (_now() - published_dt).days)


def build_candidate(entry, source_name, log=None):
    title = entry["title"]
    plain = entry.get("plain_text", "")
    published_dt = entry.get("published_dt")
    urls = extract_urls(entry.get("description_html", "") if "description_html" in entry else "", plain)
    extra_urls = entry.get("other_urls") or []
    for extra in extra_urls:
        if extra not in urls:
            urls.append(extra)
    survey_url = entry.get("survey_url") or pick_survey_url(urls)
    deadline = entry.get("preset_deadline") or extract_deadline(title, plain, published_dt)
    check = eligibility_check(title, plain, None)
    if entry.get("joop"):
        extra = joop_extra_check(entry)
        if extra:
            if extra["status"] == "skip":
                check["status"] = "skip"
            elif check["status"] == "candidate":
                check["status"] = "review"
            check["skip_reason"] = " / ".join(
                [x for x in [check["skip_reason"], extra["reason"]] if x])
            check["flags"].append(extra["flag"])
    return {
        "id": "%s-%s" % (source_name, entry.get("guid") or normalize_title(title)[:40]),
        "status": check["status"],
        "skip_reason": check["skip_reason"],
        "flags": check["flags"],
        "requires_login": check["requires_login"],
        "source": source_name,
        "guid": entry.get("guid"),
        "title": title,
        "url": entry.get("link") or entry.get("url"),
        "survey_url": survey_url,
        "other_urls": urls[:8],
        "deadline": deadline,
        "deadline_text": extract_deadline_text(title, plain),
        "prize": entry.get("preset_prize") or extract_prize(title, plain),
        "joop": entry.get("joop"),
        "published_at": entry.get("published_at"),
        "age_days": _age_days(published_dt),
        "categories": entry.get("categories", [])[:15],
        "excerpt": re.sub(r"\s+", " ", plain)[:400],
    }
    if not candidate.get("survey_url") and candidate.get("url", "").find("threads.com") >= 0:
        candidate["note"] = "스레드 글에는 응모 링크가 없고 프로필 링크에 있는 경우가 많음(permalink로 확인)"


def run_once(dry_run=False, config_path=None, out_path=None, telegram=False,
             push_github=None, import_history_paths=None, import_seen_paths=None,
             max_items=None, quiet=False, include_seen=False):
    """1회 수집. sy_auto_watch.py 에서 그대로 호출할 수 있는 진입점.

    돌려주는 값: 실행 요약 dict (JSON 직렬화 가능)
    """
    log = Log(quiet=quiet)
    started = _now()
    cfg, cfg_loaded = load_config(config_path)
    base_dir = os.path.dirname(os.path.abspath(cfg_loaded)) if cfg_loaded else BASE_DIR
    log.info("survey_collector v%s 시작 (%s)" % (VERSION, "DRY-RUN" if dry_run else "LIVE"))

    out_file = resolve_path(out_path or cfg["output_path"], base_dir)
    if dry_run and not out_path:
        root, ext = os.path.splitext(out_file)
        out_file = root + ".dryrun" + (ext or ".json")
    state_file = resolve_path(cfg["state_path"], base_dir)
    known_file = resolve_path(cfg["known_urls_path"], base_dir)
    last_run_file = resolve_path(cfg["last_run_path"], base_dir)
    max_results = int(max_items or cfg.get("rss_max_results", 25))

    state = load_state(state_file)
    known = collect_known_urls(known_file)

    for path in (import_history_paths or []):
        extra = import_history(resolve_path(path), log)
        known["urls"] |= extra["urls"]
        known["titles"] |= extra["titles"]
        known["ids"] |= extra["ids"]
    for path in (import_seen_paths or []):
        for seen_id in import_seen_rss(resolve_path(path), log):
            state["seen_ids"].setdefault(seen_id, _iso(started))

    # ---- 수집 ----
    rss_url = DEFAULT_RSS.format(n=max_results)
    rss_items, rss_meta = fetch_rss(rss_url, log)
    sources = [rss_meta]
    if rss_items is None:
        log.error("RSS 수집 실패: %s" % rss_meta.get("error"))
        rss_items = []
    else:
        log.ok("RSS 파싱 성공: %d건 (%s)" % (len(rss_items), rss_url))

    threads_posts = []
    for turl in DEFAULT_THREADS:
        posts, meta = collect_threads(turl, log)
        sources.append(meta)
        threads_posts.extend(posts)
    if threads_posts:
        log.ok("스레드 포스트 %d건 수집" % len(threads_posts))

    joop_cfg = cfg.get("joopjoop") or {}
    joop_items = []
    if joop_cfg.get("enabled", True):
        joop_items, joop_meta = collect_joopjoop(
            joop_cfg.get("url") or DEFAULT_JOOPJOOP, log,
            max_days=joop_cfg.get("max_days", 14),
            max_items=joop_cfg.get("max_items", 400),
            require_free=joop_cfg.get("require_free", True))
        sources.append(joop_meta)
    else:
        sources.append({"name": "joopjoop-json", "url": joop_cfg.get("url") or DEFAULT_JOOPJOOP,
                        "ok": True, "status": None, "error": None, "items": 0,
                        "note": "config에서 비활성화(joopjoop.enabled=false)"})

    # ---- 후보 생성·중복 제거 ----
    warnings, errors = [], []
    for src in sources:
        if not src.get("ok") and src.get("error"):
            warnings.append("%s: %s" % (src["name"], src["error"]))

    new_candidates, skipped, duplicate_count, stale_count = [], [], 0, 0
    seen_ids = state["seen_ids"]
    seen_urls = state["seen_urls"]
    seen_titles = state["seen_titles"]
    fresh_days = int(cfg.get("fresh_days", 45))

    for source_name, item in ([("today-event-news-rss", i) for i in rss_items]
                              + [("threads", i) for i in threads_posts]
                              + [("joopjoop", i) for i in joop_items]):
        candidate = build_candidate(item, source_name, log)
        guid = str(candidate.get("guid") or "")
        url_key = canonical_url(candidate.get("url") or "")
        title_key = normalize_title(candidate.get("title"))
        survey_key = canonical_url(candidate.get("survey_url") or "")
        published_dt = item.get("published_dt")

        is_duplicate = (guid in seen_ids or (url_key and url_key in seen_urls)
                        or (title_key and title_key in seen_titles)
                        or (survey_key and survey_key in known["urls"])
                        or (survey_key and survey_key in seen_urls)
                        or (title_key and title_key in known["titles"]))
        if is_duplicate and not include_seen:
            duplicate_count += 1
            if not dry_run:  # 기응모·기수집 건도 상태에 남겨 다음 실행에서 또 새 건으로 올라오지 않게
                if guid:
                    seen_ids.setdefault(guid, _iso(started))
                if url_key:
                    seen_urls.setdefault(url_key, _iso(started))
                if title_key:
                    seen_titles.setdefault(title_key, _iso(started))
                if survey_key:
                    seen_urls.setdefault(survey_key, _iso(started))
            continue

        candidate["is_duplicate"] = bool(is_duplicate)
        candidate["first_seen_at"] = seen_ids.get(guid) or _iso(started)
        candidate["last_seen_at"] = _iso(started)

        if published_dt is not None and (not include_seen) and _age_days(published_dt) > fresh_days:
            stale_count += 1
            candidate["status"] = "skip"
            candidate["skip_reason"] = "발행 %d일 경과(오래됨)" % _age_days(published_dt)
            skipped.append(candidate)
            seen_ids.setdefault(guid, _iso(started))
            continue

        if candidate.get("deadline"):
            try:
                deadline_date = datetime.date.fromisoformat(candidate["deadline"])
                if deadline_date < _now().date():
                    candidate["status"] = "skip"
                    candidate["skip_reason"] = ("마감 지남(%s)" % candidate["deadline"])
            except Exception:
                pass

        if candidate["status"] == "skip":
            skipped.append(candidate)
        else:
            new_candidates.append(candidate)

        if not dry_run:
            seen_ids.setdefault(guid, _iso(started))
            if url_key:
                seen_urls.setdefault(url_key, _iso(started))
            if title_key:
                seen_titles.setdefault(title_key, _iso(started))
            if survey_key:
                seen_urls.setdefault(survey_key, _iso(started))

    # 정렬: 바로 응모 가능 → 검토 필요, 마감일 임박 순
    priority = {"candidate": 0, "review": 1, "skip": 2}
    new_candidates.sort(key=lambda c: (priority.get(c["status"], 3),
                                       c.get("deadline") or "9999-12-31",
                                       c.get("published_at") or ""))

    max_candidates = int(cfg.get("max_candidates", 120))
    truncated = new_candidates[max_candidates:]
    new_candidates = new_candidates[:max_candidates]

    counts = {
        "rss_items": len(rss_items),
        "threads_posts": len(threads_posts),
        "joopjoop_items": len(joop_items),
        "new_candidates": len(new_candidates),
        "candidate": sum(1 for c in new_candidates if c["status"] == "candidate"),
        "review": sum(1 for c in new_candidates if c["status"] == "review"),
        "skip": len(skipped) + len(truncated),
        "duplicates": duplicate_count,
        "stale": stale_count,
        "truncated": len(truncated),
        "sources_ok": sum(1 for s in sources if s.get("ok")),
        "sources_failed": sum(1 for s in sources if not s.get("ok")),
        "errors": len(errors),
        "warnings": len(warnings),
    }

    payload = {
        "schema": "pc_survey_candidates/1",
        "collector_version": VERSION,
        "generated_at": _iso(started),
        "mode": "dry_run" if dry_run else "live",
        "respondent": cfg.get("profile"),
        "sources": sources,
        "counts": counts,
        "candidates": new_candidates,
        "skipped": skipped[:20],
        "warnings": warnings,
        "errors": errors,
        "notes": {
            "usage": "candidates = 중복 제거 후 신규 후보. status=candidate(바로 응모 가능) / review(사람 확인) / skip(skip_reason 참조).",
            "dedupe": "survey_seen.json(본 guid·기사 URL·제목) + known_urls.json(기응모 URL·제목) 대조.",
        },
    }

    saved_out = None
    if not dry_run or out_path:
        try:
            save_json_atomic(out_file, payload)
            saved_out = out_file
            log.ok("후보 저장: %s (신규 %d건)" % (out_file, len(new_candidates)))
        except Exception as exc:
            errors.append("후보 저장 실패: %s" % exc)
            log.error(errors[-1])
    else:
        # 드라이런: 상태를 건드리지 않고 별도 파일로만 남긴다
        try:
            save_json_atomic(out_file, payload)
            saved_out = out_file
            log.ok("드라이런 결과 저장: %s (상태 파일은 갱신하지 않음)" % out_file)
        except Exception as exc:
            errors.append("드라이런 결과 저장 실패: %s" % exc)
            log.error(errors[-1])

    # ---- 상태 저장 (live 한정) ----
    if not dry_run:
        prune_state(state, int(cfg.get("keep_seen_days", 180)))
        state["updated_at"] = _iso(started)
        state["stats"] = {"last_run_at": _iso(started), "last_new_candidates": len(new_candidates)}
        try:
            save_json_atomic(state_file, state)
            log.info("중복 상태 저장: %s (seen_ids %d / seen_urls %d)"
                     % (state_file, len(state["seen_ids"]), len(state["seen_urls"])))
        except Exception as exc:
            errors.append("상태 저장 실패: %s" % exc)
            log.error(errors[-1])

    # ---- GitHub 푸시 ----
    push_result = {"requested": False, "ok": None, "detail": "not requested"}
    want_push = cfg["github"].get("enabled", False) if push_github is None else bool(push_github)
    if dry_run:
        if want_push:
            push_result = {"requested": True, "ok": None, "detail": "dry-run: 푸시 생략"}
            log.info("드라이런: GitHub 푸시 생략")
    elif want_push:
        push_result = github_push(payload, cfg["github"], log)
        push_result["requested"] = True
        if push_result.get("ok"):
            log.ok("GitHub 푸시 완료: %s" % push_result.get("url"))
        else:
            warnings.append("GitHub 푸시 실패: %s" % push_result.get("detail"))
            log.warn("GitHub 푸시 실패(계속 진행): %s" % push_result.get("detail"))

    # ---- 텔레그램 ----
    tg_result = {"requested": bool(telegram), "ok": None, "detail": "not requested"}
    if telegram:
        if dry_run:
            tg_result["detail"] = "dry-run: 발송 생략"
            log.info("드라이런: 텔레그램 발송 생략")
        else:
            tg_result = send_telegram(build_telegram_text(payload), cfg["telegram"], log)
            if tg_result.get("ok"):
                log.ok("텔레그램 요약 발송 완료")
            else:
                warnings.append("텔레그램 발송 실패: %s" % tg_result.get("detail"))
                log.warn("텔레그램 발송 실패(계속 진행): %s" % tg_result.get("detail"))

    payload["counts"]["errors"] = len(errors)
    payload["counts"]["warnings"] = len(warnings)
    payload["errors"] = errors
    payload["warnings"] = warnings

    summary = {
        "ok": bool(rss_meta.get("ok")) and not errors,
        "mode": "dry_run" if dry_run else "live",
        "started_at": _iso(started),
        "finished_at": _iso(_now()),
        "elapsed_sec": round((_now() - started).total_seconds(), 1),
        "output": saved_out,
        "state": None if dry_run else state_file,
        "counts": counts,
        "sources": sources,
        "github": push_result,
        "telegram": tg_result,
        "warnings": warnings,
        "errors": errors,
        "top": [
            {"status": c["status"], "title": c["title"], "deadline": c["deadline"],
             "survey_url": c["survey_url"]}
            for c in new_candidates[:5]
        ],
    }

    if not dry_run:
        try:
            save_json_atomic(last_run_file, summary)
        except Exception as exc:
            log.warn("last_run.json 저장 실패: %s" % exc)

    log.ok("완료: 신규 %d건 / 후보 %d · 검토 %d · 제외 %d · 중복 %d · 소스실패 %d"
           % (counts["new_candidates"], counts["candidate"], counts["review"],
              counts["skip"], counts["duplicates"], counts["sources_failed"]))
    summary["log_lines"] = log.lines
    return summary


# ---------------------------------------------------------------------------
# GitHub 병합 푸시 (urllib만 사용, 토큰은 환경변수/설정에서만)
# ---------------------------------------------------------------------------

def _token(cfg_section, key="token_env"):
    env_name = cfg_section.get(key) or ""
    token = os.environ.get(env_name, "") if env_name else ""
    if not token:
        token = cfg_section.get("token") or ""  # config.json 직접 기입(권장하지 않음)
    return token.strip()


def merge_payloads(remote, new_payload):
    remote = remote if isinstance(remote, dict) else {}
    old_items = remote.get("candidates") or []
    merged = {}
    for item in old_items:
        if isinstance(item, dict) and item.get("id"):
            merged[item["id"]] = item
    for item in new_payload.get("candidates") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        previous = merged.get(item["id"])
        if previous and previous.get("first_seen_at"):
            item = dict(item)
            item["first_seen_at"] = previous["first_seen_at"]
        merged[item["id"]] = item
    items = list(merged.values())
    items.sort(key=lambda c: (c.get("deadline") or "9999-12-31", c.get("published_at") or ""), reverse=False)
    items = items[:400]
    out = dict(new_payload)
    out["candidates"] = items
    out["merged_at"] = _iso(_now())
    out["previous_count"] = len(old_items)
    return out


def github_push(payload, gh_cfg, log):
    owner = (gh_cfg.get("owner") or "").strip()
    repo = (gh_cfg.get("repo") or "").strip()
    path = (gh_cfg.get("path") or "pc_survey_candidates.json").strip()
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

    merged = merge_payloads(remote, payload)
    body = {
        "message": gh_cfg.get("commit_message") or "chore: PC 설문 수집 후보 갱신",
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
                return {"ok": True, "detail": "merged %d items" % len(merged.get("candidates") or []),
                        "url": (data.get("commit") or {}).get("html_url") or "",
                        "sha": (data.get("content") or {}).get("sha")}
            except Exception:
                return {"ok": True, "detail": "pushed"}
        if res.get("status") in (409, 422) and attempt == 0:
            log.warn("GitHub 충돌(%s), sha 재조회 후 1회 재시도" % res.get("status"))
            res2 = http_request(api + "?ref=" + urllib.parse.quote(branch), headers=headers, log=log, retries=1)
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


# ---------------------------------------------------------------------------
# 텔레그램 요약
# ---------------------------------------------------------------------------

def build_telegram_text(payload):
    counts = payload.get("counts", {})
    lines = ["[PC 설문 수집] %s" % payload.get("generated_at", ""),
             "신규 %d건 (응모가능 %d · 검토 %d · 제외 %d)" % (
                 counts.get("new_candidates", 0), counts.get("candidate", 0),
                 counts.get("review", 0), counts.get("skip", 0)),
             "소스: RSS %d · 스레드 %d · 줍줍판 %d" % (
                 counts.get("rss_items", 0), counts.get("threads_posts", 0),
                 counts.get("joopjoop_items", 0))]
    for item in payload.get("candidates", [])[:8]:
        if item.get("status") == "skip":
            continue
        deadline = item.get("deadline") or "마감미상"
        lines.append("- [%s] %s (%s)" % (item.get("status"), item.get("title", "")[:70], deadline))
        if item.get("survey_url"):
            lines.append("  %s" % item["survey_url"])
    failed = [s["name"] for s in payload.get("sources", []) if not s.get("ok")]
    if failed:
        lines.append("경고: 수집실패 %s" % ", ".join(failed))
    text = "\n".join(lines)
    return text[:3800]


def send_telegram(text, tg_cfg, log):
    token = _token(tg_cfg, "token_env")
    chat_id = os.environ.get(tg_cfg.get("chat_id_env") or "TELEGRAM_CHAT_ID", "") or tg_cfg.get("chat_id") or ""
    chat_id = str(chat_id).strip()
    if not token or not chat_id:
        return {"ok": False, "detail": "토큰/chat_id 없음(%s, %s)" % (
            tg_cfg.get("token_env", "TELEGRAM_BOT_TOKEN"), tg_cfg.get("chat_id_env", "TELEGRAM_CHAT_ID"))}
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text,
                                   "disable_web_page_preview": "true"}).encode("utf-8")
    res = http_request(url, method="POST", data=data,
                       headers={"Content-Type": "application/x-www-form-urlencoded"}, log=log, retries=1)
    if res["ok"]:
        return {"ok": True, "detail": "sent"}
    return {"ok": False, "detail": res.get("error")}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="집 PC용 경품 설문·퀴즈 새 링크 수집기 (표준 라이브러리만 사용)")
    parser.add_argument("--dry-run", action="store_true", help="상태 저장·GitHub 푸시·텔레그램 없이 파싱만 검증")
    parser.add_argument("--config", help="config.json 경로(기본: 스크립트 옆 config.json)")
    parser.add_argument("--out", help="후보 JSON 출력 경로")
    parser.add_argument("--telegram", action="store_true", help="텔레그램 요약 발송")
    parser.add_argument("--no-github", action="store_true", help="config에서 켜져 있어도 GitHub 푸시 안 함")
    parser.add_argument("--push-github", action="store_true", help="config와 무관하게 GitHub 푸시")
    parser.add_argument("--import-history", action="append", default=[],
                        help="인1 history.json 경로(기응모 URL·제목 중복 제거). 여러 번 지정 가능")
    parser.add_argument("--import-seen", action="append", default=[],
                        help="인1 state/rss.json 경로(seen_ids 이어받기). 여러 번 지정 가능")
    parser.add_argument("--max-items", type=int, help="RSS 최대 수집 건수(기본 25)")
    parser.add_argument("--include-seen", action="store_true", help="이미 본 항목도 후보에 포함")
    parser.add_argument("--print-json", action="store_true", help="실행 요약 JSON을 stdout에 출력")
    parser.add_argument("--quiet", action="store_true", help="로그 최소화")
    parser.add_argument("--version", action="version", version="survey_collector " + VERSION)
    return parser


def main(argv=None):
    try:  # Windows 콘솔(cp949)에서도 한글·기호가 깨지지 않게
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    args = build_parser().parse_args(argv)
    push = True if args.push_github else (False if args.no_github else None)
    summary = run_once(
        dry_run=args.dry_run,
        config_path=args.config,
        out_path=args.out,
        telegram=args.telegram,
        push_github=push,
        import_history_paths=args.import_history,
        import_seen_paths=args.import_seen,
        max_items=args.max_items,
        quiet=args.quiet or args.print_json,
        include_seen=args.include_seen,
    )
    if args.print_json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print("")
        print("요약: 신규 %d건 / 응모가능 %d / 검토 %d / 제외 %d / 중복 %d / 소스실패 %d / 에러 %d"
              % (summary["counts"]["new_candidates"], summary["counts"]["candidate"],
                 summary["counts"]["review"], summary["counts"]["skip"],
                 summary["counts"]["duplicates"], summary["counts"]["sources_failed"],
                 summary["counts"]["errors"]))
        if summary.get("output"):
            print("결과 파일: %s" % summary["output"])
        for item in summary.get("top", []):
            print("  - [%s] %s (%s)" % (item["status"], item["title"][:70], item.get("deadline") or "마감미상"))
            if item["survey_url"]:
                print("    %s" % item["survey_url"])
    return 0 if summary["counts"]["sources_ok"] > 0 else 2


if __name__ == "__main__":
    sys.exit(main())
