#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shopee_booster.py — 집 PC(C:\\sy_auto) 쇼피 8개국 **무료 부스트 전용** 실행기
============================================================================

만든 목적
  쇼피 8개국 무료 부스트(4시간 쿨다운, 샵당 5슬롯)를 인1(김승용22)이 브라우저로
  직접 누르지 않고 집 PC에서 0원으로 채우기 위한 스크립트.

  · `shopee_cookies.json`의 `SPC_CDS` + `Cookie` 헤더로 셀러센터 JSON API 호출
  · `get_bumped_product_list` 로 빈 슬롯 확인 → 빈 슬롯에만 `boost_product` 실행
  · **유료 Boost/광고는 절대 호출하지 않는다(무료 슬롯 전용). 아래 3중 가드:**
      1) 실행 URL은 `boost_product` 경로 하나만 허용(다른 경로는 예외)
      2) URL/경로에 ads·paid·promotion·purchase 문자열이 있으면 즉시 중단
      3) 설정 파일에서도 상품 ID만 받는다(임의 API 주소 주입 불가)
  · 쿠키 만료/로그인 필요를 감지하면 **즉시 텔레그램(`sy global`)으로 알림**
    → 피클(인1/피비서)이 쿠키 파일만 갱신하면 다음 회차부터 집 PC가 다시 돌린다.

배정 규칙
  · SG·TW: `work/shopee-api/boost-targets-SG-TW.md` 확정 흑자 5종만 사용(대체 상품 안 씀)
  · 나머지 6개국(MY·PH·VN·TH·MX·BR): 지정 목록이 없으므로 최신 5종
    (`get_product_list?page_number=1&page_size=5`)을 사용 — 기존 피비서·인2 회차와 동일 방식
  · `exclude` 목록의 상품은 절대 부스트하지 않는다(역마진 제외 상품용)

중복 실행 방지
  · `boost_state.json`의 마지막 실행 시각을 보고 `min_interval_minutes`(기본 180분) 안에는
    새로 부스트하지 않는다. `--force`로만 무시할 수 있다.
  · 부스트 직후 슬롯을 다시 조회해 실제 반영(5/5)을 확인한다.

의존성
  Python 3.7+ 표준 라이브러리만 사용. `pip install` 불필요.

사용 예
  python shopee_booster.py --dry-run      # 조회만: 빈 슬롯·배정 예정 상품 출력(부스트 안 함)
  python shopee_booster.py                # 실제 무료 부스트 실행
  python shopee_booster.py --telegram     # 결과 요약까지 텔레그램 발송
  python shopee_booster.py --self-test    # 네트워크 없이 로직 점검
  python shopee_booster.py --print-json   # 감시 스크립트용 결과 JSON 한 줄

토큰·쿠키는 코드에 넣지 않는다. `config.json` 또는 환경변수에서만 읽는다.
  GITHUB_TOKEN / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
"""

from __future__ import print_function

import argparse
import base64
import datetime
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1.0.0"
KST = datetime.timezone(datetime.timedelta(hours=9))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 sy_auto-shopee-booster/" + VERSION)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

REGIONS = [
    ("SG", "syglobal.sg", "1938185445", "sg"),
    ("TW", "syglobalwl", "1938234251", "tw"),
    ("MY", "syglobaldx.my", "1938234247", "my"),
    ("PH", "syglobalev.ph", "1938234248", "ph"),
    ("VN", "syglobalkd.vn", "1938234249", "vn"),
    ("TH", "syglobalen.th", "1938234250", "th"),
    ("MX", "syglobaldd.mx", "1938234253", "mx"),
    ("BR", "syglobalne.br", "1938234252", "br"),
]

PATH_BUMPED = "/api/v3/opt/mpsku/list/get_bumped_product_list"
PATH_BOOST = "/api/v3/opt/product/boost_product/"
PATH_PRODUCT_LIST = "/api/v3/opt/mpsku/list/v2/get_product_list"

# 무료 부스트 실행에 허용되는 유일한 경로
ALLOWED_WRITE_PATHS = (PATH_BOOST,)
FORBIDDEN_URL_WORDS = ("ads", "advert", "paid", "promotion/purchase", "purchase",
                       "boost_purchase", "keyword_ads", "topup", "billing", "wallet")

# work/shopee-api/boost-targets-SG-TW.md (2026-11-26 16:12 확정) 내장
DEFAULT_TARGETS = {
    "SG": ["26447109019", "57971534333", "47522011237", "56471987776", "51772020877"],
    "TW": ["56221950415", "56471987779", "53921944202", "42136921794", "47222009156"],
}
# 역마진이라 부스트에서 제외한 상품(확정 목록에도, 최신 5종 대체에서도 제외)
DEFAULT_EXCLUDE = {
    "SG": ["45071535849", "52771481444"],
    "TW": ["45621600325"],
    "PH": [],   # 2026-11-27 07:0x 인상으로 4종 흑자 전환 → 제외 없음
    "VN": [],
}

DEFAULT_CONFIG = {
    "output_path": "pc_boost.json",
    "state_path": "boost_state.json",
    "last_run_path": "last_run_boost.json",
    "shopee": {
        "base_url": "https://seller.shopee.kr",
        "cookies_path": "shopee_cookies.json",
        "timeout": 20,
        "delay_seconds": 0.8,
        "max_boosts_per_run": 40,
        "min_interval_minutes": 180,
        "verify_after_boost": True,
        "fallback_from_product_list": True,
        "fallback_regions": ["MY", "PH", "VN", "TH", "MX", "BR"],
        "product_list_page_size": 5,
    },
    "targets": DEFAULT_TARGETS,
    "exclude": DEFAULT_EXCLUDE,
    "github": {
        "enabled": False, "owner": "cbseng18-glitch", "repo": "sy-dashboard",
        "path": "pc_boost.json", "branch": "main",
        "token_env": "GITHUB_TOKEN", "commit_message": "chore: PC 부스트 회차 갱신",
        "keep_snapshots": 24, "path_boost": "pc_boost.json",
    },
    "telegram": {"enabled": False, "token_env": "TELEGRAM_BOT_TOKEN",
                 "chat_id_env": "TELEGRAM_CHAT_ID",
                 "alert_on_cookie_expired": True},
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


def _hhmm(dt=None):
    return (dt or _now()).astimezone(KST).strftime("%m-%d %H:%M")


def _parse_iso(text):
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def http_request(url, method="GET", data=None, headers=None, timeout=20,
                 retries=1, log=None, max_bytes=1024 * 1024):
    hdrs = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6",
        "Accept-Encoding": "identity",
        "X-Requested-With": "XMLHttpRequest",
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
                return {"ok": True, "status": getattr(resp, "status", 200),
                        "body": resp.read(max_bytes), "error": None,
                        "content_type": resp.headers.get("Content-Type", ""),
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
                time.sleep(2 * (attempt + 1))
                continue
            return last
        except Exception as exc:
            last = {"ok": False, "status": None, "body": b"",
                    "error": "%s: %s" % (type(exc).__name__, exc),
                    "content_type": "", "url": url}
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return last
    return last


def decode_body(result):
    raw = result.get("body") or b""
    m = re.search(r"charset=([\w\-]+)", result.get("content_type") or "", re.I)
    for enc in ([m.group(1).lower()] if m else []) + ["utf-8", "cp949"]:
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


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


def _to_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# 쿠키
# ---------------------------------------------------------------------------

def load_shopee_cookies(path):
    """stats_checker.py와 동일 규격. {ok, cookie, spc_cds, saved_at, source, error}"""
    out = {"ok": False, "cookie": "", "spc_cds": "", "saved_at": None,
           "source": None, "error": None}
    if not path or not os.path.exists(path):
        out["error"] = "파일 없음: %s" % path
        return out
    try:
        with open(path, "r", encoding="utf-8") as fp:
            raw_text = fp.read()
    except Exception as exc:
        out["error"] = "읽기 실패: %s" % exc
        return out
    try:
        data = json.loads(raw_text)
    except Exception:
        data = None

    if isinstance(data, str):
        out["cookie"] = data.strip()
    elif isinstance(data, list):
        out["cookie"] = "; ".join("%s=%s" % (c.get("name"), c.get("value", ""))
                                  for c in data if isinstance(c, dict) and c.get("name"))
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
        out["spc_cds"] = str(data.get("spc_cds") or data.get("SPC_CDS") or "").strip()
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


# ---------------------------------------------------------------------------
# 쇼피 호출 (읽기 + 무료 부스트만)
# ---------------------------------------------------------------------------

class PaidBoostGuard(Exception):
    pass


def assert_free_boost_url(url):
    """무료 부스트 외 쓰기 호출을 원천 차단한다."""
    low = url.lower()
    for word in FORBIDDEN_URL_WORDS:
        if word in low:
            raise PaidBoostGuard("유료/광고로 보이는 URL 차단(%s): %s" % (word, url))
    path = urllib.parse.urlsplit(url).path
    if path not in ALLOWED_WRITE_PATHS:
        raise PaidBoostGuard("허용되지 않은 쓰기 경로: %s" % path)
    return True


def _headers(cookies):
    return {
        "Cookie": cookies.get("cookie") or "",
        "Referer": "https://seller.shopee.kr/portal/product/list/all",
    }


def _looks_like_login_page(text):
    low = (text or "")[:4000].lower()
    return ("<html" in low and "login" in low) or "please login" in low or "로그인이 필요" in low


def classify_response(res):
    """(상태, 메시지) — ok / cookie_expired / api_error / http_error / bad_json"""
    if not res.get("ok"):
        status = res.get("status")
        if status in (401, 403):
            return "cookie_expired", "HTTP %s(로그인/토큰 만료 의심)" % status
        return "http_error", (res.get("error") or "요청 실패")[:160]
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
    if code in (0, "0") or (code is None and "data" in data):
        return "ok", ""
    if code in (401, 403) or re.search(r"csrf|login|session|unauthor|비로그인", msg, re.I):
        return "cookie_expired", "code=%s %s" % (code, msg[:120])
    return "api_error", "code=%s %s" % (code, msg[:140])


def shopee_get(base_url, path, params, cookies, timeout=20, log=None):
    query = dict(params)
    query.setdefault("SPC_CDS_VER", "2")
    if cookies.get("spc_cds"):
        query.setdefault("SPC_CDS", cookies["spc_cds"])
    url = base_url.rstrip("/") + path + "?" + urllib.parse.urlencode(query)
    res = http_request(url, headers=_headers(cookies), timeout=timeout, retries=1, log=log)
    state, detail = classify_response(res)
    payload = None
    if state == "ok":
        try:
            payload = json.loads(decode_body(res))
        except Exception:
            state, detail = "bad_json", "JSON 파싱 실패"
    return {"state": state, "detail": detail, "payload": payload, "status": res.get("status"),
            "url": url, "at": _now()}


def _region_params(region):
    _, _, shop_id, area = region
    return {"cnsc_shop_id": shop_id, "cbsc_shop_region": area}


def parse_slots(payload):
    data = (payload or {}).get("data") or {}
    conf = data.get("config") or {}
    products = data.get("products") or []
    total = _to_int(conf.get("total_slots"), 5) or 5
    cooldowns = [_to_int(p.get("cool_down_seconds")) for p in products if isinstance(p, dict)]
    max_cd = max(cooldowns) if cooldowns else 0
    return {
        "slots_total": total,
        "slots_used": len(products),
        "slots_free": max(total - len(products), 0),
        "cooldown_seconds_config": _to_int(conf.get("cooldown_seconds")),
        "cooldown_remaining_seconds": max_cd,
        "next_free_at": _iso(_now() + datetime.timedelta(seconds=max_cd)) if max_cd else None,
        "product_ids": [str(p.get("id")) for p in products if isinstance(p, dict)],
    }


def extract_product_ids(payload):
    data = (payload or {}).get("data")
    candidates = []
    if isinstance(data, dict):
        for key in ("list", "product_list", "items", "products", "mpsku_list"):
            if isinstance(data.get(key), list):
                candidates = data[key]
                break
    elif isinstance(data, list):
        candidates = data
    ids = []
    for item in candidates:
        if isinstance(item, dict):
            for key in ("id", "product_id", "item_id", "mpsku_id", "productId"):
                value = item.get(key)
                if value not in (None, ""):
                    ids.append(str(value))
                    break
        elif isinstance(item, (int, str)):
            ids.append(str(item))
    seen, unique = set(), []
    for pid in ids:
        if pid not in seen:
            seen.add(pid)
            unique.append(pid)
    return unique


def fetch_latest_ids(base_url, region, cookies, cfg, log):
    section = cfg.get("shopee") or {}
    params = dict(_region_params(region), page_number=1,
                  page_size=_to_int(section.get("product_list_page_size"), 5) or 5)
    res = shopee_get(base_url, PATH_PRODUCT_LIST, params, cookies,
                     _to_int(section.get("timeout"), 20) or 20, log)
    if res["state"] != "ok":
        return [], res
    return extract_product_ids(res["payload"]), res


def plan_ids(region_code, free, boosted_ids, cfg, base_url, region, cookies, log):
    """빈 슬롯에 넣을 상품 ID 목록과 근거를 돌려준다."""
    section = cfg.get("shopee") or {}
    targets = [str(x) for x in (cfg.get("targets") or {}).get(region_code) or []]
    excludes = set(str(x) for x in (cfg.get("exclude") or {}).get(region_code) or [])
    chosen, source, notes = [], [], []
    for pid in targets:
        if pid in boosted_ids:
            notes.append("%s 이미 부스트 중 → 건너뜀" % pid)
            continue
        if pid in excludes:
            notes.append("%s 제외 목록 → 건너뜀" % pid)
            continue
        chosen.append(pid)
    if chosen and targets:
        source.append("확정목록")

    fallback_regions = section.get("fallback_regions") or []
    if (len(chosen) < free and not targets and section.get("fallback_from_product_list", True)
            and region_code in fallback_regions):
        latest, res = fetch_latest_ids(base_url, region, cookies, cfg, log)
        if res["state"] != "ok":
            notes.append("최신 상품 조회 실패(%s): %s" % (res["state"], res["detail"]))
        for pid in latest:
            if len(chosen) >= free:
                break
            if pid in chosen or pid in boosted_ids or pid in excludes:
                continue
            chosen.append(pid)
        if chosen:
            source.append("최신목록")

    return chosen[:free], "+".join(source) or "없음", notes


def boost_one(base_url, region, product_id, cookies, timeout, log):
    path = PATH_BOOST
    params = dict(_region_params(region), version="3.1.0", id=str(product_id))
    query = dict(params)
    query.setdefault("SPC_CDS_VER", "2")
    if cookies.get("spc_cds"):
        query.setdefault("SPC_CDS", cookies["spc_cds"])
    url = base_url.rstrip("/") + path + "?" + urllib.parse.urlencode(query)
    assert_free_boost_url(url)          # ★ 유료/광고 호출 원천 차단
    res = http_request(url, headers=_headers(cookies), timeout=timeout, retries=1, log=log)
    state, detail = classify_response(res)
    return {"product_id": str(product_id), "state": state, "detail": detail,
            "http_status": res.get("status")}


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

def run_once(dry_run=False, config_path=None, force=False, telegram=False,
             push_github=None, quiet=False, only=None):
    log = Log(quiet=quiet)
    cfg, cfg_path = load_config(config_path)
    section = cfg.get("shopee") or {}
    started = _now()
    payload = {
        "schema": "pc_boost/1", "generator": "shopee_booster.py", "version": VERSION,
        "generated_at": _iso(started), "mode": "dry_run" if dry_run else "live",
        "config_path": cfg_path, "cookie_state": None, "cookie_error": None,
        "cookie_saved_at": None, "regions": [],
        "totals": {"regions_total": len(REGIONS), "regions_ok": 0, "free_slots": 0,
                   "planned": 0, "attempted": 0, "boosted": 0, "failed": 0, "skipped": 0},
        "alerts": [], "warnings": [], "errors": [], "github": None, "telegram": None,
    }
    only_set = set(only or [r[0] for r in REGIONS])

    cookies_path = resolve_path(section.get("cookies_path") or "shopee_cookies.json")
    cookies = load_shopee_cookies(cookies_path)
    payload["cookie_saved_at"] = cookies.get("saved_at")
    timeout = _to_int(section.get("timeout"), 20) or 20
    delay = float(section.get("delay_seconds") or 0.8)
    base_url = section.get("base_url") or "https://seller.shopee.kr"
    max_boosts = _to_int(section.get("max_boosts_per_run"), 40) or 40
    state_path = resolve_path(cfg.get("state_path") or "boost_state.json")
    state = load_json(state_path, default=None) or {}
    interval = _to_int(section.get("min_interval_minutes"), 180)

    def cookie_alert_allowed():
        """쿠키 만료 알림은 min_interval_minutes(기본 180분)에 1회만 → 알림 피로 방지."""
        last = _parse_iso(state.get("last_cookie_alert_at")) if isinstance(state, dict) else None
        return (not last) or ((_now() - last).total_seconds() / 60.0 >= interval)

    def mark_cookie_alert():
        if dry_run or not isinstance(state, dict):
            return
        state["last_cookie_alert_at"] = _iso()
        save_json_atomic(state_path, state)

    if not cookies["ok"]:
        payload["cookie_state"] = "cookie_missing"
        payload["cookie_error"] = cookies.get("error")
        payload["alerts"].append({
            "level": "high", "code": "cookie_missing",
            "message": "쇼피 쿠키 파일이 없습니다. `shopee_cookies.json`을 만들어 주세요.",
            "path": cookies_path,
        })
        log.error("쿠키 없음: %s (%s)" % (cookies_path, cookies.get("error")))
        allowed = cookie_alert_allowed()
        if allowed:
            mark_cookie_alert()
        _finish(payload, cfg, dry_run, log, force_alert=allowed, telegram=telegram,
                push_github=push_github)
        return payload

    # 중복 실행 가드
    last_at = _parse_iso(state.get("last_boost_at"))
    if (not dry_run and not force and last_at):
        elapsed = (_now() - last_at).total_seconds() / 60.0
        if elapsed < interval:
            payload["warnings"].append(
                "최근 %d분 전 부스트 실행(기준 %d분) → 이번 회차 건너뜀. 강제 실행은 --force"
                % (int(elapsed), interval))
            log.warn(payload["warnings"][-1])
            payload["cookie_state"] = "ok"
            payload["skipped_reason"] = "recent_run"
            payload["skip_minutes"] = int(elapsed)
            _finish(payload, cfg, dry_run, log, telegram=telegram, push_github=push_github)
            return payload

    expired = False
    attempted = 0
    if not cookies.get("spc_cds"):
        payload["warnings"].append("SPC_CDS 토큰이 쿠키 파일에 없음 → 부스트 목록 조회가 403(csrf)으로 실패할 수 있음")
        log.warn(payload["warnings"][-1])
    for region in REGIONS:
        code = region[0]
        if code not in only_set:
            continue
        item = {"region": code, "shop": region[1], "shop_id": region[2], "area": region[3],
                "ok": False, "slots": {}, "planned_ids": [], "plan_source": None,
                "boosts": [], "warnings": [], "error": None}
        payload["regions"].append(item)

        bl = shopee_get(base_url, PATH_BUMPED, _region_params(region), cookies, timeout, log)
        if bl["state"] == "cookie_expired":
            expired = True
            item["error"] = bl["detail"]
            item["state"] = "cookie_expired"
            log.warn("쇼피 %s: 쿠키 만료/로그인 필요 (%s)" % (code, bl["detail"]))
            continue
        if bl["state"] != "ok":
            item["error"] = bl["detail"]
            item["state"] = bl["state"]
            log.warn("쇼피 %s: 슬롯 조회 실패 %s (%s)" % (code, bl["state"], bl["detail"]))
            continue

        slots = parse_slots(bl["payload"])
        item["slots"] = slots
        item["ok"] = True
        payload["totals"]["regions_ok"] += 1
        payload["totals"]["free_slots"] += slots["slots_free"]

        if slots["slots_free"] <= 0:
            log.info("쇼피 %s: %d/%d 가득%s" % (
                code, slots["slots_used"], slots["slots_total"],
                (" · 해제 %s" % _hhmm(_parse_iso(slots["next_free_at"])))
                if slots["next_free_at"] else ""))
            continue

        remaining_budget = max_boosts - attempted
        free = min(slots["slots_free"], max(remaining_budget, 0))
        if free <= 0:
            item["warnings"].append("회차 최대 부스트 수(%d)에 도달" % max_boosts)
            continue
        ids, source, notes = plan_ids(code, free, set(slots["product_ids"]), cfg,
                                      base_url, region, cookies, log)
        item["planned_ids"] = ids
        item["plan_source"] = source
        item["warnings"].extend(notes)
        payload["totals"]["planned"] += len(ids)
        if not ids:
            item["warnings"].append("배정할 상품 ID 없음(확정목록/최신목록 비어 있음)")
            log.warn("쇼피 %s: 빈 슬롯 %d개지만 배정할 상품 없음" % (code, slots["slots_free"]))
            continue

        log.info("쇼피 %s: 빈 슬롯 %d개 → %s (%s)" % (
            code, slots["slots_free"], ", ".join(ids), source))
        if dry_run:
            item["boosts"] = [{"product_id": pid, "state": "dry_run"} for pid in ids]
            continue

        for pid in ids:
            if attempted >= max_boosts:
                break
            attempted += 1
            payload["totals"]["attempted"] += 1
            result = boost_one(base_url, region, pid, cookies, timeout, log)
            item["boosts"].append(result)
            if result["state"] == "ok":
                payload["totals"]["boosted"] += 1
                log.ok("쇼피 %s: 부스트 성공 %s" % (code, pid))
            elif result["state"] == "cookie_expired":
                expired = True
                payload["totals"]["failed"] += 1
                log.warn("쇼피 %s: 부스트 중 쿠키 만료 (%s)" % (code, result["detail"]))
                break
            else:
                payload["totals"]["failed"] += 1
                log.warn("쇼피 %s: 부스트 실패 %s → %s (%s)" % (
                    code, pid, result["state"], result["detail"]))
            if delay:
                time.sleep(delay * random.uniform(0.6, 1.2))

        if slots["slots_free"] - len(item["boosts"]) > 0:
            payload["totals"]["skipped"] += max(slots["slots_free"] - len(item["boosts"]), 0)
        if section.get("verify_after_boost", True) and payload["totals"]["boosted"]:
            chk = shopee_get(base_url, PATH_BUMPED, _region_params(region), cookies, timeout, log)
            if chk["state"] == "ok":
                after = parse_slots(chk["payload"])
                item["slots_after"] = after
                if after["slots_used"] >= after["slots_total"]:
                    log.ok("쇼피 %s: 재조회 %d/%d 확인" % (code, after["slots_used"], after["slots_total"]))
                else:
                    item["warnings"].append("재조회 %d/%d — 일부 미반영" % (
                        after["slots_used"], after["slots_total"]))
                    log.warn("쇼피 %s: 부스트 후 %d/%d" % (
                        code, after["slots_used"], after["slots_total"]))
            else:
                item["warnings"].append("재조회 실패(%s)" % chk["state"])
        if delay:
            time.sleep(delay * 0.5)

    if expired:
        payload["cookie_state"] = "cookie_expired"
        payload["alerts"].append({
            "level": "high", "code": "cookie_expired",
            "message": "쇼피 쿠키가 만료됐습니다. `shopee_cookies.json`만 갱신해 주세요(피클 인1/피비서)."
                       + ("" if cookies.get("spc_cds") else " SPC_CDS 값도 비어 있습니다."),
            "path": cookies_path,
        })
    elif payload["totals"]["regions_ok"]:
        payload["cookie_state"] = "ok"
    else:
        payload["cookie_state"] = "error"

    if not dry_run and payload["totals"]["boosted"] > 0:
        state = state if isinstance(state, dict) else {}
        state["last_boost_at"] = payload["generated_at"]
        state["last_boosted"] = payload["totals"]["boosted"]
        state["history"] = (state.get("history") or [])[-19:] + [{
            "at": payload["generated_at"], "boosted": payload["totals"]["boosted"],
            "failed": payload["totals"]["failed"],
        }]
        save_json_atomic(state_path, state)

    alert_allowed = bool(expired and cookie_alert_allowed())
    _finish(payload, cfg, dry_run, log, telegram=telegram, push_github=push_github,
            force_alert=alert_allowed)
    if alert_allowed:
        mark_cookie_alert()
    return payload


def resolve_github_cfg(cfg):
    """공용 config.json 에서 path(pc_stats.json)를 그대로 쓰면 통합조회 파일을 덮어쓴다.
    부스트는 항상 pc_boost.json 으로 보낸다."""
    gh_cfg = dict(cfg.get("github") or {})
    if gh_cfg.get("path_boost"):
        gh_cfg["path"] = gh_cfg["path_boost"]
    elif gh_cfg.get("path") in (None, "", "pc_stats.json"):
        gh_cfg["path"] = "pc_boost.json"
    return gh_cfg


def _finish(payload, cfg, dry_run, log, telegram=False, push_github=None, force_alert=False):
    out_path = resolve_path(cfg.get("output_path") or "pc_boost.json")
    if dry_run:
        dry_path = out_path.replace(".json", "") + ".dryrun.json"
        save_json_atomic(dry_path, payload)
        payload["output"] = dry_path
        log.info("드라이런 결과 저장: %s (부스트·전송 없음)" % dry_path)
    else:
        save_json_atomic(out_path, payload)
        payload["output"] = out_path
        save_json_atomic(resolve_path(cfg.get("last_run_path") or "last_run_boost.json"), {
            "generated_at": payload["generated_at"], "cookie_state": payload["cookie_state"],
            "totals": payload["totals"], "alerts": payload["alerts"],
            "warnings": payload["warnings"][:10], "errors": payload["errors"][:10],
        })

    gh_cfg = resolve_github_cfg(cfg)
    want_push = push_github if push_github is not None else bool(gh_cfg.get("enabled"))
    if want_push and not dry_run:
        try:
            payload["github"] = github_push(payload, gh_cfg, log)
        except Exception as exc:
            payload["github"] = {"ok": False, "detail": "%s: %s" % (type(exc).__name__, exc)}
        log.info("GitHub: %s" % (payload["github"] or {}).get("detail"))

    tg_cfg = cfg.get("telegram") or {}
    alert_now = force_alert and bool(tg_cfg.get("alert_on_cookie_expired", True))
    if (telegram or alert_now) and not dry_run:
        try:
            payload["telegram"] = send_telegram(build_telegram_text(payload), tg_cfg, log)
        except Exception as exc:
            payload["telegram"] = {"ok": False,
                                   "detail": "%s: %s" % (type(exc).__name__, exc)}
        log.info("텔레그램: %s" % (payload["telegram"] or {}).get("detail"))

    totals = payload["totals"]
    log.info("완료: 쇼피 %d/8국 · 빈 슬롯 %d · 시도 %d · 성공 %d · 실패 %d · 쿠키 %s" % (
        totals["regions_ok"], totals["free_slots"], totals["attempted"],
        totals["boosted"], totals["failed"], payload["cookie_state"]))


# ---------------------------------------------------------------------------
# GitHub · 텔레그램
# ---------------------------------------------------------------------------

def _compact_snapshot(payload):
    return {
        "generated_at": payload.get("generated_at"),
        "cookie_state": payload.get("cookie_state"),
        "boosted": payload["totals"].get("boosted"),
        "failed": payload["totals"].get("failed"),
        "free_slots": payload["totals"].get("free_slots"),
        "regions": {r["region"]: {"slots": "%s/%s" % ((r.get("slots") or {}).get("slots_used"),
                                                      (r.get("slots") or {}).get("slots_total")),
                                  "boosted": len([b for b in r.get("boosts", [])
                                                  if b.get("state") == "ok"])}
                    for r in payload.get("regions", []) if r.get("ok")},
    }


def merge_payloads(remote, new_payload, keep=24):
    snaps = []
    if isinstance(remote, dict):
        snaps = [s for s in (remote.get("snapshots") or []) if isinstance(s, dict)]
    snap = _compact_snapshot(new_payload)
    snaps = [s for s in snaps if s.get("generated_at") != snap.get("generated_at")]
    snaps.append(snap)
    out = dict(new_payload)
    out["snapshots"] = snaps[-keep:]
    out["merged_at"] = _iso()
    return out


def github_push(payload, gh_cfg, log):
    owner = (gh_cfg.get("owner") or "").strip()
    repo = (gh_cfg.get("repo") or "").strip()
    path = (gh_cfg.get("path") or "pc_boost.json").strip()
    branch = (gh_cfg.get("branch") or "main").strip()
    token = _token(gh_cfg, "token_env")
    if not owner or not repo:
        return {"ok": False, "detail": "config.json github.owner/repo 미설정"}
    if not token:
        return {"ok": False, "detail": "토큰 없음(환경변수 %s 미설정)" % gh_cfg.get("token_env", "GITHUB_TOKEN")}
    api = "https://api.github.com/repos/%s/%s/contents/%s" % (owner, repo, urllib.parse.quote(path))
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": UA}
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
    body = {"message": gh_cfg.get("commit_message") or "chore: PC 부스트 회차 갱신",
            "content": base64.b64encode(json.dumps(merged, ensure_ascii=False, indent=1)
                                        .encode("utf-8")).decode("ascii"),
            "branch": branch}
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
    totals = payload.get("totals") or {}
    lines = ["[PC 부스트] %s (%s)" % (_hhmm(_parse_iso(payload.get("generated_at")) or _now()),
                                      "조회만" if payload.get("mode") == "dry_run" else "실행")]
    if payload.get("cookie_state") == "cookie_expired":
        lines.append("⚠ 쇼피 쿠키 만료 → shopee_cookies.json 갱신 필요")
        lines.append("갱신되면 집 PC가 다음 회차부터 자동으로 다시 채웁니다.")
        return "\n".join(lines)[:3800]
    if payload.get("cookie_state") == "cookie_missing":
        lines.append("⚠ 쇼피 쿠키 파일 없음 → shopee_cookies.json 필요")
        return "\n".join(lines)[:3800]
    if payload.get("skipped_reason") == "recent_run":
        lines.append("직전 실행 %d분 전 → 이번 회차 건너뜀(중복 방지)" % _to_int(
            payload.get("skip_minutes"), 0))
        return "\n".join(lines)[:3800]

    lines.append("빈 슬롯 %d · 시도 %d · 성공 %d · 실패 %d" % (
        totals.get("free_slots", 0), totals.get("attempted", 0),
        totals.get("boosted", 0), totals.get("failed", 0)))
    per_region = []
    for item in payload.get("regions", []):
        slots = item.get("slots") or {}
        after = item.get("slots_after") or slots
        if not item.get("ok"):
            per_region.append("%s ✕" % item["region"])
            continue
        per_region.append("%s %s/%s" % (item["region"], after.get("slots_used"),
                                        after.get("slots_total")))
    if per_region:
        lines.append(" · ".join(per_region))
    releases = []
    for item in payload.get("regions", []):
        nxt = (item.get("slots_after") or item.get("slots") or {}).get("next_free_at")
        if nxt:
            releases.append(_hhmm(_parse_iso(nxt)))
    if releases:
        lines.append("해제 %s~%s" % (min(releases), max(releases)))
    failed = [w for w in payload.get("warnings") or [] if "미반영" in w or "실패" in w]
    if failed:
        lines.append("확인: " + failed[0][:100])
    return "\n".join(lines)[:3800]


# ---------------------------------------------------------------------------
# 자체 점검 (--self-test)
# ---------------------------------------------------------------------------

SAMPLE_LIST = {"code": 0, "data": {"config": {"total_slots": 5, "cooldown_seconds": 14400},
                                   "products": [{"id": 111, "cool_down_seconds": 3000}]}}
SAMPLE_PRODUCT_LIST = {"code": 0, "data": {"list": [{"id": 901}, {"product_id": 902},
                                                    {"item_id": 903}, {"id": "904"}]}}
SAMPLE_CSRF = {"code": 403, "message": "csrf validation fail"}


def _self_test(log):
    import tempfile
    failures = []

    # 쿠키 5형식
    blobs = [
        {"cookie": "SPC_F=abc; SPC_SI=def", "spc_cds": "35738ed8-bd7b-4d2c-b9c9-fe584ea9fc6e"},
        {"Cookie": "SPC_F=abc", "SPC_CDS": "35738ed8-bd7b-4d2c-b9c9-fe584ea9fc6e"},
        {"cookies": {"SPC_F": "abc"}},
        {"cookies": [{"name": "SPC_F", "value": "abc"}]},
    ]
    for blob in blobs:
        path = os.path.join(tempfile.gettempdir(), "ck_boost_test.json")
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(blob, fp, ensure_ascii=False)
        parsed = load_shopee_cookies(path)
        if not parsed["ok"] or "SPC_F=abc" not in parsed["cookie"]:
            failures.append("쿠키 파싱 실패: %s" % list(blob.keys()))

    # 응답 판정
    def fake(status, text, ok=True):
        return {"ok": ok, "status": status, "body": text.encode("utf-8"), "error": None,
                "content_type": "application/json", "url": "u"}
    if classify_response(fake(200, json.dumps(SAMPLE_CSRF)))[0] != "cookie_expired":
        failures.append("csrf → cookie_expired 판정 실패")
    if classify_response(fake(200, json.dumps(SAMPLE_LIST)))[0] != "ok":
        failures.append("정상 응답 판정 실패")
    if classify_response(fake(403, "forbidden", ok=False))[0] != "cookie_expired":
        failures.append("403 판정 실패")
    if classify_response(fake(500, "boom", ok=False))[0] != "http_error":
        failures.append("500 판정 실패")

    # 슬롯·상품 ID 파싱
    slots = parse_slots(SAMPLE_LIST)
    if slots["slots_used"] != 1 or slots["slots_free"] != 4 or slots["slots_total"] != 5:
        failures.append("슬롯 계산 실패: %s" % slots)
    if not slots["next_free_at"]:
        failures.append("해제 시각 계산 실패")
    if extract_product_ids(SAMPLE_PRODUCT_LIST) != ["901", "902", "903", "904"]:
        failures.append("상품 ID 추출 실패: %s" % extract_product_ids(SAMPLE_PRODUCT_LIST))

    # 배정 규칙: SG는 확정목록만, 이미 부스트 중/제외 상품 제외
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    chosen, source, notes = plan_ids("SG", 5, {"26447109019"}, cfg,
                                     "https://seller.shopee.kr",
                                     ("SG", "syglobal.sg", "1938185445", "sg"), {"ok": False}, log)
    if "56471987776" not in chosen or "26447109019" in chosen:
        failures.append("SG 확정목록 배정 실패: %s" % chosen)
    if len(chosen) != 4:
        failures.append("SG 배정 수 실패(기대 4): %s" % chosen)
    cfg2 = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg2["targets"]["SG"] = []
    cfg2["shopee"]["fallback_regions"] = []
    chosen2, _, _ = plan_ids("SG", 5, set(), cfg2, "https://seller.shopee.kr",
                             ("SG", "x", "1", "sg"), {"ok": False}, log)
    if chosen2:
        failures.append("fallback 비활성인데 상품을 배정함: %s" % chosen2)

    # 유료/광고 가드
    allowed = "https://seller.shopee.kr/api/v3/opt/product/boost_product/?id=1"
    if not assert_free_boost_url(allowed):
        failures.append("정상 무료 부스트 URL 차단")
    for bad in ("https://seller.shopee.kr/api/v3/ads/create/?id=1",
                "https://seller.shopee.kr/api/v3/opt/product/boost_purchase/?id=1",
                "https://seller.shopee.kr/api/v3/opt/product/update_product/?id=1"):
        try:
            assert_free_boost_url(bad)
            failures.append("유료/비허용 URL 차단 실패: %s" % bad)
        except PaidBoostGuard:
            pass

    # 상태 파일 중복 실행 가드(경과 계산)
    last = _now() - datetime.timedelta(minutes=30)
    if ((_now() - last).total_seconds() / 60.0) >= 180:
        failures.append("중복 실행 경과 계산 실패")

    # GitHub 경로 충돌 방지
    if resolve_github_cfg({"github": {"path": "pc_stats.json"}})["path"] != "pc_boost.json":
        failures.append("공용 config의 pc_stats.json 경로를 pc_boost.json 으로 바꾸지 못함")
    if resolve_github_cfg({"github": {"path": "x.json", "path_boost": "boost_x.json"}})["path"] != "boost_x.json":
        failures.append("path_boost 우선순위 실패")

    # 병합 스냅샷
    payload = {"generated_at": _iso(), "cookie_state": "ok",
               "totals": {"boosted": 5, "failed": 0, "free_slots": 5},
               "regions": [{"region": "SG", "ok": True, "slots": {"slots_used": 5, "slots_total": 5},
                            "boosts": [{"state": "ok"}]}]}
    merged = merge_payloads({"snapshots": [{"generated_at": "2026-11-27T08:00:00+09:00"}]}, payload)
    if len(merged["snapshots"]) != 2 or merged["snapshots"][-1]["regions"]["SG"]["slots"] != "5/5":
        failures.append("스냅샷 병합 실패")

    if failures:
        for f in failures:
            log.error("자체점검 실패: %s" % f)
        return 1
    log.ok("자체점검 통과: 쿠키 4형식 · 응답판정 4종 · 슬롯/ID 파싱 · 배정규칙 · 유료차단 3종 · "
           "중복가드 · 스냅샷 병합")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="집 PC용 쇼피 8개국 무료 부스트 실행기 (표준 라이브러리만 사용, 유료/광고 호출 금지)")
    parser.add_argument("--dry-run", action="store_true",
                        help="조회만: 빈 슬롯과 배정 예정 상품 출력(부스트 실행 안 함)")
    parser.add_argument("--config", help="config.json 경로(기본: 스크립트 옆 config.json)")
    parser.add_argument("--force", action="store_true",
                        help="min_interval_minutes 중복 실행 가드 무시")
    parser.add_argument("--telegram", action="store_true", help="결과 요약 텔레그램 발송")
    parser.add_argument("--no-github", action="store_true", help="config에서 켜져 있어도 GitHub 푸시 안 함")
    parser.add_argument("--push-github", action="store_true", help="config와 무관하게 GitHub 푸시")
    parser.add_argument("--only", help="쉼표 구분 국가 코드(예: SG,TW). 기본 8개국")
    parser.add_argument("--print-json", action="store_true", help="결과 요약 JSON을 stdout에 출력")
    parser.add_argument("--self-test", action="store_true", help="네트워크 없이 로직 점검")
    parser.add_argument("--quiet", action="store_true", help="로그 최소화")
    parser.add_argument("--version", action="version", version="shopee_booster " + VERSION)
    return parser


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = build_parser().parse_args(argv)

    if args.self_test:
        return _self_test(Log(quiet=args.quiet))

    push = True if args.push_github else (False if args.no_github else None)
    only = [part.strip().upper() for part in args.only.split(",")] if args.only else None
    summary = run_once(dry_run=args.dry_run, config_path=args.config, force=args.force,
                       telegram=args.telegram, push_github=push,
                       quiet=args.quiet or args.print_json, only=only)
    if args.print_json:
        print(json.dumps({"cookie_state": summary["cookie_state"], "totals": summary["totals"],
                          "alerts": summary["alerts"], "warnings": summary["warnings"][:5],
                          "errors": summary["errors"][:5], "output": summary.get("output")},
                         ensure_ascii=False))
    else:
        print("")
        totals = summary["totals"]
        print("요약: 쇼피 %d/8국(쿠키 %s) / 빈 슬롯 %d / 시도 %d / 성공 %d / 실패 %d"
              % (totals["regions_ok"], summary["cookie_state"], totals["free_slots"],
                 totals["attempted"], totals["boosted"], totals["failed"]))
        if summary.get("output"):
            print("결과 파일: %s" % summary["output"])
        for item in summary["regions"]:
            after = item.get("slots_after") or item.get("slots") or {}
            print("  - %s: %s/%s%s%s" % (
                item["region"], after.get("slots_used"), after.get("slots_total"),
                (" · 예정 %s" % ",".join(item["planned_ids"])) if item.get("planned_ids") else "",
                (" · " + "; ".join(item["warnings"])) if item.get("warnings") else ""))
        for alert in summary["alerts"]:
            print("  ! %s: %s" % (alert.get("code"), alert.get("message")))

    if summary["cookie_state"] in ("cookie_expired", "cookie_missing", "error"):
        return 3
    return 0 if not summary["errors"] else 2


if __name__ == "__main__":
    sys.exit(main())
