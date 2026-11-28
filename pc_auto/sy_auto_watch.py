import base64
import csv
import email.utils
import html
import http.cookiejar
import importlib
import io
import json
import mimetypes
import os
import py_compile
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
STATE_FILE = BASE_DIR / "state.json"
LOG_FILE = BASE_DIR / "watch_log.txt"

PPOM_URL = "https://www.ppomppu.co.kr/rss.php?id=ppomppu"
DAISO_BASE = "https://fapi.daisomall.co.kr"
DAISO_DETAIL_URL = DAISO_BASE + "/pd/pdr/pdDtl/selPdDtlInfo"
VOOY_SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/1kf0aCVnY3Ywdd7Xv8MrZ67k_nPeIHq2WbkzAS-0Xb28/export?format=csv&gid=0"
REMOTE_CMD_URL = "https://api.github.com/repos/cbseng18-glitch/sy-dashboard/contents/pc_cmd.json"

# ---------------------------------------------------------------- 설문·퀴즈·특가 레이더
# 2026-11-27 실제 HTTP 200 + XML 파싱 확인된 피드만 등록
EVENT_FEEDS = [
    ("뽐뿌 이벤트", "https://www.ppomppu.co.kr/rss.php?id=event"),
    ("뽐뿌 쿠폰", "https://www.ppomppu.co.kr/rss.php?id=coupon"),
    ("구글뉴스 설문·기프티콘",
     "https://news.google.com/rss/search?q=%22%EC%84%A4%EB%AC%B8%22+%22%EA%B8%B0%ED%94%84%ED%8B%B0%EC%BD%98%22&hl=ko&gl=KR&ceid=KR:ko"),
    ("구글뉴스 선착순·경품",
     "https://news.google.com/rss/search?q=%EC%84%A0%EC%B0%A9%EC%88%9C+%EC%9D%B4%EB%B2%A4%ED%8A%B8+%EA%B8%B0%ED%94%84%ED%8B%B0%EC%BD%98&hl=ko&gl=KR&ceid=KR:ko"),
    ("구글뉴스 만족도조사",
     "https://news.google.com/rss/search?q=%EB%A7%8C%EC%A1%B1%EB%8F%84%EC%A1%B0%EC%82%AC+%EC%BB%A4%ED%94%BC%EC%BF%A0%ED%8F%B0&hl=ko&gl=KR&ceid=KR:ko"),
    ("구글뉴스 앱테크·퀴즈",
     "https://news.google.com/rss/search?q=%EC%95%B1%ED%85%8C%ED%81%AC+%EC%B6%9C%EC%B2%B5+%ED%80%B4%EC%A6%88&hl=ko&gl=KR&ceid=KR:ko"),
]

EVENT_KEYWORDS = [
    ("설문조사", r"설문|만족도\s?조사|서베이|리서치|패널"),
    ("퀴즈", r"퀴즈|정답|출첵|출석\s?체크|앱테크|캐시워크|리브메이트|모니모"),
    ("기프티콘", r"기프티콘|기프티쇼|상품권|금액권|교환권|e?쿠폰|커피쿠폰|스타벅스|메가커피|투썸|배스킨|올리브영"),
    ("선착순/경품", r"선착순|경품|추첨|응모|당첨|증정"),
    ("이벤트", r"이벤트"),
]
# 사고·소송 기사처럼 설문·이벤트와 무관한 잡음을 거른다
EVENT_NOISE = re.compile(r"유출|해킹|사기|피해|과태료|처벌|고발|논란|압수수색|횡령")
EVENT_MAX_AGE_DAYS = 2   # 구글뉴스 항목은 최근 2일 이내만
EVENT_REPORT_LIMIT = 8

DEAL_KEYWORDS = [
    ("기저귀", r"기저귀|하기스|팸퍼스|페넬로페|마미포코|보솜이|리베로|킨도"),
    ("아기용품", r"아기|유아|신생아|분유|물티슈|이유식|젖병|젖꼭지|유모차|카시트|유팡|로토토"),
    ("기프티콘", r"기프티콘|기프티쇼|상품권|금액권|교환권|e쿠폰|컬쳐랜드|해피머니|북앤라이프|스타벅스|배달의민족|배민|올리브영"),
    ("청소기", r"드리미|H12|청소기|로보락|에코백스|나르왈|다이슨"),
]

DEFAULT_DAISO_ITEMS = {
    "1081270": "다이소 주력 감시 1081270",
    "1053550": "포토카드 미니 탑로더",
    "1048346": "빈티지 레이스 마스킹테이프",
    "1064378": "브러시 세척패드",
    "1053545": "홀로그램 슬리브 20매",
    "1047239": "개인정보 지우개(라벤더)",
    "1053551": "두꺼운 슬리브 30매",
    "1069014": "실리콘 립브러시 2p",
    "1064274": "스탠딩 콜렉트북",
    "1044243": "탑로더 보관함",
    "1056440": "헤어롤 41mm",
    "1001868": "홀로그램 스티커 5매",
    "1057923": "커터달린 송장보호 스탬프",
    "1038284": "실리콘 병뚜껑",
    "1080345": "반투명 북마크 4p",
    "1065254": "스티커 핀셋 곡선",
    "1048655": "투명 PET 점착메모",
    "1063551": "물방울 퍼프 2p",
    "1063846": "트윈 수정테이프",
    "1064936": "쿨링시트 4매",
    "1064938": "카카오 쿨링시트 6매",
    "1077742": "포토카드 키링홀더",
    "1064282": "PET 인덱스 30매",
    "1022325": "테라스킨 스팟패치 72",
    "10290961": "6공 바인더 리필",
    "1059409": "컨실러 듀오 브러시",
    "1080403": "십장생 와지 스티커",
    "1047375": "마이멜로디 4분할 케이스",
    "1051898": "메이크업 브러시 6종",
    "1037681": "내열 유리빨대 세트",
    "1075340": "말랑자석 한글놀이",
    "1085452": "[큐텐1호] 지퍼형 포토카드 바인더(핑크) · 구 6공 바인더 대체",
    "1053549": "[큐텐1호] 폴꾸 투명 탑로더 10개입",
    "10374041": "[큐텐1호] 폴꾸 데코 스티커 2매입 A",
    "10374042": "[큐텐1호] 폴꾸 데코 스티커 2매입 B",
    "1078815": "[큐텐1호] 마스킹테이프 3개입",
    "1035718": "[큐텐2호] 헬로키티 떼어쓰는 마스킹테이프 200매입",
    "1046024": "[큐텐2호] 헬로키티 에폭시 스티커 1매입",
    "1035717": "[큐텐2호] 헬로키티 투명 스티커 3매입",
    "1036198": "[큐텐2호] 마이멜로디 유포지 스티커 3매입",
    "1036822": "[큐텐2호] 마이멜로디 점착메모 70매입",
    "10363832": "[큐텐2호] 마이멜로디 캐릭터 볼펜 0.5mm 핑크",
    "1035676": "[큐텐2호] 헬로키티 납작 자수 동전 지갑",
    "1078193": "[큐텐3호] 퀼팅 키링 파우치 · 구 사각 파우치 대체",
    "1058005": "[큐텐3호] T형 카드 스탠드",
    "1073176": "[큐텐3호] 비즈 키링 리본",
    "1075138": "[큐텐3호] 썸머글로시 해파리 비즈 키링",
    "1056955": "[큐텐3호] 볼륨 조임 미니 파우치(블랙) · 구 체크 파우치 대체",
    "1074817": "[큐텐4호] 모트모트 스프링 스터디 플래너 브라운",
    "1055253": "[큐텐4호] 학습용 버튼 타이머(4기능, 무음)",
    "1013422": "[큐텐4호] 파스텔 형광펜 3개입",
    "1079748": "[큐텐4호] 모트모트 데일리 체크 점착 메모지 60매입",
    "1068547": "[큐텐5호] 실리콘 맥세이프 호환용 거치대 링 아이보리",
    "1055672": "[큐텐5호] 로프 손목 안전 스트랩 베이지",
    "1078453": "[큐텐5호] 마이멜로디 투명 구슬 스트랩 키링",
    "1062445": "[큐텐5호] 접착식 아크릴 팝핑거(강아지)",
    "1078200": "[큐텐5호] 윈터홈 강아지 퀼팅 파우치"
}

# ---------------------------------------------------------------- 큐텐 재팬 1~5호 다이소 구성품 (최우선 09:30 경보)
# 2026-11-28 거래처(인1) 확인 기준 구성. 괄호 안은 구성 번호.
#  · 1호 다꾸·트레카 5종 → 1085452(지퍼형 포토카드 바인더, 구 10290951 대체)
#  · 3호 K-POP 6종     → 1078193(퀼팅 키링 파우치, 구 1072876) · 1056955(볼륨 조임 미니 파우치, 구 1066705)
QOO10_DAISO_ITEMS = {
    "1085452": ("1호", "지퍼형 포토카드 바인더(핑크) ①"),
    "1053549": ("1호", "폴꾸 투명 탑로더 10개입 ②"),
    "10374041": ("1호", "폴꾸 데코 스티커 2매입 A ③"),
    "10374042": ("1호·3호", "폴꾸 데코 스티커 2매입 B ③"),
    "1078815": ("1호", "마스킹테이프 3개입 ⑤"),
    "1035718": ("2호", "헬로키티 떼어쓰는 마스킹테이프 200매입 ①"),
    "1046024": ("2호", "헬로키티 에폭시 스티커 1매입 ②"),
    "1035717": ("2호", "헬로키티 투명 스티커 3매입 ③"),
    "1036198": ("2호", "마이멜로디 유포지 스티커 3매입 ④"),
    "1036822": ("2호", "마이멜로디 점착메모 70매입 ⑥"),
    "10363832": ("2호", "마이멜로디 캐릭터 볼펜 0.5mm 핑크 ⑦"),
    "1035676": ("2호", "헬로키티 납작 자수 동전 지갑 ⑧"),
    "1078193": ("3호", "퀼팅 키링 파우치 ①"),
    "1058005": ("3호", "T형 카드 스탠드 ②"),
    "1073176": ("3호", "비즈 키링 리본 ③"),
    "1075138": ("3호", "썸머글로시 해파리 비즈 키링 ④"),
    "1056955": ("3호", "볼륨 조임 미니 파우치(블랙) ⑥"),
    "1074817": ("4호", "모트모트 스프링 스터디 플래너 브라운 ①"),
    "1055253": ("4호", "학습용 버튼 타이머 4기능 ②"),
    "1013422": ("4호", "파스텔 형광펜 3개입 ③"),
    "1079748": ("4호", "모트모트 데일리 체크 점착 메모지 60매입 ④"),
    "1068547": ("5호", "실리콘 맥세이프 거치대 링 ①"),
    "1055672": ("5호", "로프 손목 안전 스트랩 ②"),
    "1078453": ("5호", "마이멜로디 투명 구슬 스트랩 ③"),
    "1062445": ("5호", "접착식 아크릴 팝핑거(강아지) ④"),
    "1078200": ("5호", "윈터홈 강아지 퀼팅 파우치 ⑤"),
}

# 2026-11-28 품절 대체로 더 이상 쓰지 않는 품번 — config.json 에 남아 있어도 감시에서 뺀다.
RETIRED_DAISO_ITEMS = {"10290951", "1072876", "1066705", "1068539", "1049373", "1083290", "1069124"}
QOO10_DAISO_MIN_QTY = 50          # 이 수량 미만이면 '재고 부족' 경보

DAISO_STORES_10KM = ["71773", "70461", "40870", "10366", "71944", "11226", "70240", "10979", "10217", "71813", "10533", "10830", "11195", "70351", "11263", "10254", "11104", "10627", "41184", "10099", "10973", "10818", "10299", "10416", "11175", "10519", "10955", "71684", "71161", "10992", "10594", "10792", "71766", "40782", "10590", "10970", "10637", "10355", "41106", "10522", "11194", "41235", "10054", "10659", "10295", "10518", "10868", "10940", "10175", "10619", "10645", "10999", "10805", "10875", "10286", "10683", "10543", "11097", "71587", "10921", "71792", "11260", "10584", "71816", "10965", "41429", "40978", "41278", "70226"]
ZEROED_ITEMS = set(["1064378", "1044243", "10290961"])

START_TIME = time.time()


def log(msg: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        # ★ 텔레그램 봇 토큰·chat_id 는 코드에 넣지 않는다(공개 저장소 배포본).
        #   집 PC의 config.json 에 실제 값이 이미 있으므로 빈 값이어도 그대로 읽힌다.
        #   새 PC라면 config.json 을 채우거나 환경변수 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 를 쓴다.
        default_cfg = {
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "deal_interval_minutes": 30,
            "daiso_interval_minutes": 60,
            "event_interval_minutes": 10,
            "survey_interval_hours": 2,
            "stats_interval_minutes": 120,
            "boost_interval_minutes": 240,
            "stats_digest_hours": 12,
            "deal_verify": True,
            "survey_push_github": None,
            "survey_import_history": [],
            "survey_import_seen": [],
            "remote_poll_seconds": 60,
            "daiso_items": DEFAULT_DAISO_ITEMS,
        }
        CONFIG_FILE.write_text(
            json.dumps(default_cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return default_cfg
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    cfg.setdefault("remote_poll_seconds", 60)
    cfg.setdefault("event_interval_minutes", 10)
    cfg.setdefault("survey_interval_hours", 2)
    cfg.setdefault("stats_interval_minutes", 120)
    cfg.setdefault("boost_interval_minutes", 240)
    cfg.setdefault("stats_digest_hours", 12)
    # config.json 에 텔레그램 값이 비어 있으면 환경변수로 보완
    if not (cfg.get("telegram_bot_token") or "").strip():
        cfg["telegram_bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not str(cfg.get("telegram_chat_id") or "").strip():
        cfg["telegram_chat_id"] = os.environ.get("TELEGRAM_CHAT_ID", "")
    cfg.setdefault("deal_verify", True)
    cfg.setdefault("survey_push_github", None)
    cfg.setdefault("survey_import_history", [])
    cfg.setdefault("survey_import_seen", [])
    # 57종 전체 동기화 보장 — 아래 순서로 합친다.
    #  ① 기본 57종  ② 옛 품번 제거(2026-11-28 큐텐 대체분)  ③ 큐텐 1~5호 구성품 필수 포함  ④ config.json 사용자 추가분
    merged = dict(DEFAULT_DAISO_ITEMS)
    for old in RETIRED_DAISO_ITEMS:
        merged.pop(old, None)
    merged.update(dict(QOO10_DAISO_ITEMS))
    merged.update(cfg.get("daiso_items") or {})
    for old in RETIRED_DAISO_ITEMS:
        merged.pop(old, None)
    cfg["daiso_items"] = merged
    cfg.setdefault("qoo10_daiso_min_qty", QOO10_DAISO_MIN_QTY)
    cfg.setdefault("qoo10_daiso_interval_minutes", 60)
    cfg.setdefault("dashboard_push_minutes", 60)
    return cfg


def _toss_state_note() -> str:
    """상태 보고용 토스 설정 한 줄(키 값 자체는 절대 찍지 않는다)."""
    try:
        tc = load_toss_config()
        filled = sum(1 for k in ("client_id", "client_secret", "publisher_id") if tc.get(k))
        return "설정됨" if filled == 3 else f"미설정({filled}/3)"
    except Exception:
        return "확인 실패"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            d.setdefault("seen_sheet_rows", [])
            d.setdefault("seen_deals", [])
            d.setdefault("daiso_stock", {})
            d.setdefault("last_deal_check", 0)
            d.setdefault("last_daiso_check", 0)
            d.setdefault("last_full_offline_date", "")
            d.setdefault("last_remote_cmd_id", "")
            d.setdefault("last_tg_update_id", 0)
            d.setdefault("seen_events", [])
            d.setdefault("seen_event_titles", [])
            d.setdefault("last_event_check", 0)
            d.setdefault("last_survey_check", 0)
            d.setdefault("last_stats_check", 0)
            d.setdefault("last_boost_check", 0)
            d.setdefault("last_stats_report", 0)
            d.setdefault("last_stats_cookie_alert", 0)
            d.setdefault("last_boost_cookie_alert", 0)
            d.setdefault("qoo10_daiso_qty", {})
            d.setdefault("qoo10_daiso_alert_sig", "")
            d.setdefault("last_qoo10_daiso_check", 0)
            d.setdefault("last_qoo10_daiso_day", "")
            d.setdefault("last_dash_push", 0)
            d.setdefault("last_dash_fail_alert", 0)
            d.setdefault("dash_sig", "")
            return d
        except Exception:
            pass
    return {
        "seen_deals": [],
        "daiso_stock": {},
        "last_deal_check": 0,
        "last_daiso_check": 0,
        "last_full_offline_date": "",
        "seen_sheet_rows": [],
        "last_remote_cmd_id": "",
        "last_tg_update_id": 0,
        "seen_events": [],
        "seen_event_titles": [],
        "last_event_check": 0,
        "last_survey_check": 0,
        "last_stats_check": 0,
        "last_boost_check": 0,
        "last_stats_report": 0,
        "last_stats_cookie_alert": 0,
        "last_boost_cookie_alert": 0,
        "qoo10_daiso_qty": {},
        "qoo10_daiso_alert_sig": "",
        "last_qoo10_daiso_check": 0,
        "last_qoo10_daiso_day": "",
        "last_dash_push": 0,
        "last_dash_fail_alert": 0,
        "dash_sig": "",
    }


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def send_telegram(cfg: dict, text: str) -> None:
    token = (cfg.get("telegram_bot_token") or "").strip()
    chat_id = (cfg.get("telegram_chat_id") or "").strip()
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:3900]}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
    except Exception as e:
        log(f"텔레그램 전송 실패: {e}")


def _tg_creds(cfg: dict):
    return ((cfg.get("telegram_bot_token") or "").strip(),
            (cfg.get("telegram_chat_id") or "").strip())


def _tg_clean(text, cfg: dict) -> str:
    """오류 문자열에 봇 토큰이 섞이지 않게 가린다."""
    token, _ = _tg_creds(cfg)
    s = str(text or "")
    if token:
        s = s.replace(token, "<token>")
    return s[:300]


def send_telegram_file(cfg: dict, path, caption: str = "", as_photo: bool = True) -> dict:
    """PNG·JPG 등 파일 1개를 텔레그램 방으로 전송 (사진 실패 시 문서로 재시도)."""
    token, chat_id = _tg_creds(cfg)
    p = Path(path)
    if not token or not chat_id:
        return {"ok": False, "error": "텔레그램 토큰/chat_id 없음"}
    if not p.exists():
        return {"ok": False, "error": f"파일 없음: {p.name}"}
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    plan = []
    if as_photo and ctype.startswith("image/"):
        plan.append(("sendPhoto", "photo"))
    plan.append(("sendDocument", "document"))
    payload = p.read_bytes()
    last = {"ok": False, "error": "전송 시도 없음"}
    for method, field in plan:
        try:
            boundary = "----SY" + uuid.uuid4().hex
            parts = []
            for key, val in (("chat_id", chat_id), ("caption", (caption or "")[:1000])):
                parts.append(
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{val}\r\n".encode("utf-8")
                )
            parts.append(
                (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
                 f"filename=\"{p.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode("utf-8")
            )
            parts.append(payload)
            parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/{method}",
                data=b"".join(parts),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                res = json.loads(r.read().decode("utf-8", "replace"))
            res_msg = (res.get("result") or {}) if isinstance(res, dict) else {}
            if isinstance(res, dict) and res.get("ok"):
                return {"ok": True, "method": method, "message_id": res_msg.get("message_id"),
                        "file": p.name, "size": len(payload)}
            last = {"ok": False, "method": method, "error": _tg_clean((res or {}).get("description"), cfg)}
        except Exception as e:
            last = {"ok": False, "method": method, "error": _tg_clean(f"{type(e).__name__}: {e}", cfg)}
        log(f"텔레그램 {method} 실패({p.name}): {last.get('error')}")
    return last


# ---------------------------------------------------------------- 인포그래픽 카드 · 미리캔버스 원격 실행
CARD_OUT_DIR = BASE_DIR / "cards"
MIRI_SCRIPT = BASE_DIR / "miri_auto.py"


def _ensure_pillow() -> bool:
    """카드 생성용 Pillow 확인(없으면 1회 설치 시도)."""
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        pass
    log("Pillow 설치 시도 중... (카드 생성에 필요)")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pillow"],
                       check=False, timeout=300)
    except Exception as e:
        log(f"Pillow 설치 실패: {e}")
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False


def _load_card_maker():
    """card_maker.py 를 지연 import. (모듈이 sys.exit(1) 를 쓰므로 SystemExit 도 방어)"""
    here = str(BASE_DIR)
    if here not in sys.path:
        sys.path.insert(0, here)
    if not _ensure_pillow():
        log("card_maker 로드 실패: Pillow 없음")
        return None
    try:
        import card_maker
        return card_maker
    except SystemExit as e:          # card_maker 가 Pillow/폰트 없을 때 sys.exit(1)
        log(f"card_maker 로드 중단(SystemExit): {e}")
        return None
    except Exception as e:
        log(f"card_maker 로드 실패: {type(e).__name__}: {e}")
        return None


def _card_payload(args: dict) -> dict:
    """원격/텔레그램 인자 → card_maker 가 먹는 카드 데이터(card_data·params 모두 허용)."""
    args = args or {}
    raw = args.get("card_data")
    if raw is None:
        raw = args.get("params")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    data = dict(raw) if isinstance(raw, dict) else {}
    for key in ("badge", "title", "items", "theme", "ratio", "source", "footer", "brand", "filename"):
        if args.get(key) not in (None, ""):
            data[key] = args.get(key)
    items = data.get("items")
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            items = [s.strip() for s in re.split(r"[\n;]+", items) if s.strip()]
    if isinstance(items, list):
        fixed = []
        for it in items:
            if isinstance(it, dict):
                fixed.append({"title": str(it.get("title") or it.get("sub") or ""),
                              "desc": str(it.get("desc") or it.get("text") or "")})
            else:
                fixed.append(str(it))
        items = fixed
    data["items"] = items or []
    return data


def _safe_png_name(name: str) -> str:
    """파일명 정리(경로 이동 문자 제거)."""
    base = re.sub(r"[^0-9A-Za-z가-힣._-]", "_", Path(str(name or "")).name).strip("._-")
    if not base:
        base = "card_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    if not base.lower().endswith(".png"):
        base += ".png"
    return base


def make_card(cfg: dict, args: dict, source: str = "원격") -> dict:
    """원격 카드 데이터(JSON) → PNG 생성 → 텔레그램 전송 → 결과 보고."""
    args = args or {}
    data = _card_payload(args)
    if not str(data.get("title") or "").strip():
        send_telegram(cfg, f"🖼️ [인포그래픽 카드 ({source})] title 이 없어 만들지 못했습니다\n"
                           "card_data(또는 params)에 badge·title·items 를 넣어 주세요")
        return {"ok": False, "error": "title 없음"}
    cm = _load_card_maker()
    if cm is None:
        send_telegram(cfg, f"🖼️ [인포그래픽 카드 ({source})] card_maker.py 또는 Pillow 를 불러오지 못했습니다")
        return {"ok": False, "error": "card_maker 없음"}

    fname = _safe_png_name(args.get("filename") or data.get("filename")
                           or "card_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    out_path = CARD_OUT_DIR / fname
    theme = "navy"
    try:
        cm.init_fonts()
        path, theme = cm.generate(
            data,
            theme=str(data.get("theme") or "navy"),
            out_path=str(out_path),
            ratio=str(args.get("ratio") or data.get("ratio") or "4:5"),
        )
    except Exception as e:
        log(f"카드 생성 실패: {type(e).__name__}: {e}")
        send_telegram(cfg, f"🖼️ [인포그래픽 카드 ({source})] 생성 실패: {type(e).__name__}: {e}")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    except SystemExit as e:          # 폰트 없음 등 card_maker 내부 종료 요청
        log(f"카드 생성 중단(SystemExit): {e}")
        send_telegram(cfg, f"🖼️ [인포그래픽 카드 ({source})] 생성 중단: {e}")
        return {"ok": False, "error": f"SystemExit: {e}"}

    caption = str(args.get("caption") or "").strip()
    if not caption:
        caption = f"🖼️ 인포그래픽 카드 ({source})\n{str(data.get('title'))[:120]}"
    res = send_telegram_file(cfg, path, caption)
    if res.get("ok"):
        msg = (f"🖼️ [인포그래픽 카드 완료 ({source})]\n"
               f"• 파일: {Path(path).name}\n• 테마: {theme}\n"
               f"• 전송: {res.get('method')} · message_id {res.get('message_id')}")
    else:
        msg = (f"🖼️ [인포그래픽 카드 생성됨 · 전송 실패 ({source})]\n"
               f"• 파일: {Path(path).name}\n• 오류: {res.get('error')}")
    log(msg)
    send_telegram(cfg, msg)
    out = {"ok": bool(res.get("ok")), "path": str(path), "theme": theme}
    out.update({k: v for k, v in res.items() if k != "ok"})
    return out


def _miri_out_dirs() -> list:
    """miri_auto.py 가 실제로 저장하는 폴더 후보(윈도우 C:\\sy_auto\\miri_out 우선)."""
    cands = []
    if os.name == "nt":
        cands.append(Path(r"C:\sy_auto\miri_out"))
    cands += [BASE_DIR / "miri_out", BASE_DIR / "work" / "pc-auto" / "miri_out",
              Path("work/pc-auto/miri_out")]
    seen, out = set(), []
    for c in cands:
        try:
            key = str(c.resolve())
        except Exception:
            key = str(c)
        if key in seen:
            continue
        seen.add(key)
        try:
            if c.exists():
                out.append(c)
        except OSError:
            continue
    return out


def _decode_out(raw) -> str:
    if not raw:
        return ""
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def run_miri(cfg: dict, args: dict, source: str = "원격") -> dict:
    """miri_auto.py 를 subprocess 로 실행(생성 모드)하고 파일 수·결과를 텔레그램으로 보고."""
    args = args or {}
    if not MIRI_SCRIPT.exists():
        send_telegram(cfg, f"🎨 [미리캔버스 ({source})] miri_auto.py 가 없습니다 (같은 폴더 확인)")
        return {"ok": False, "error": "miri_auto.py 없음"}
    if args.get("upload") or str(args.get("mode") or "").lower() in ("upload", "auto_upload"):
        send_telegram(cfg, "🎨 [미리캔버스] 원격 업로드 모드는 비밀번호 입력이 필요해 지원하지 않습니다.\n"
                           "집 PC에서 미리캔버스_생성.bat 을 실행해 주세요.")
        return {"ok": False, "error": "upload 모드 미지원"}
    try:
        timeout = int(args.get("timeout") or 600)
    except Exception:
        timeout = 600
    timeout = max(60, min(1800, timeout))

    before = {}
    for d in _miri_out_dirs():
        for f in d.glob("*.png"):
            try:
                before[str(f)] = f.stat().st_mtime
            except OSError:
                continue

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    started = time.time()
    rc, out, err, timedout = 0, "", "", False
    try:
        proc = subprocess.run([sys.executable, str(MIRI_SCRIPT)], cwd=str(BASE_DIR),
                              capture_output=True, timeout=timeout, env=env)
        rc = proc.returncode
        out = _decode_out(proc.stdout)
        err = _decode_out(proc.stderr)
    except subprocess.TimeoutExpired:
        timedout, rc = True, -1
    except Exception as e:
        log(f"미리캔버스 실행 오류: {type(e).__name__}: {e}")
        send_telegram(cfg, f"🎨 [미리캔버스 ({source})] 실행 오류: {type(e).__name__}: {e}")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    elapsed = int(time.time() - started)
    made, total, out_dir = [], 0, ""
    for d in _miri_out_dirs():
        pngs = list(d.glob("*.png"))
        if pngs:
            total = max(total, len(pngs))
            out_dir = out_dir or str(d)
        for f in pngs:
            try:
                mt = f.stat().st_mtime
            except OSError:
                continue
            if str(f) not in before or before[str(f)] != mt:
                made.append(f.name)
    csv_ok = bool(out_dir) and (Path(out_dir) / "miricanvas_metadata.csv").exists()

    ok = (rc == 0) and not timedout
    lines = [f"🎨 [미리캔버스 달력 52종 생성 {'완료' if ok else '실패'} ({source})]",
             f"• 결과: {'성공' if ok else ('시간초과 ' + str(timeout) + '초' if timedout else '종료코드 ' + str(rc))}",
             f"• 소요: {elapsed}초",
             f"• 신규 PNG: {len(made)}개 (폴더 전체 {total}개)",
             f"• 폴더: {out_dir or '생성 폴더 없음'}"]
    if made:
        lines.append("• 예: " + ", ".join(made[:3]) + (" 외" if len(made) > 3 else ""))
    if csv_ok:
        lines.append("• CSV: miricanvas_metadata.csv 있음")
    for t in [x for x in (out or "").splitlines() if x.strip()][-3:]:
        lines.append("  " + t.strip()[:110])
    if err.strip() and not ok:
        lines.append("• stderr: " + err.strip().splitlines()[-1][:150])
    msg = "\n".join(lines)
    log(msg)
    send_telegram(cfg, msg)
    return {"ok": ok, "rc": rc, "elapsed": elapsed, "new_files": len(made),
            "total": total, "dir": out_dir, "made": made[:10]}


# ---------------------------------------------------------------- 쇼츠 공장 · 유튜브 업로드 원격 실행
SHORTS_VIDEO_MAX_MB = 49          # 텔레그램 봇 업로드 한도(50MB)에서 여유 있게
_SHORTS_CLI_OPTS = {              # args 키 → make_shorts.py 옵션 (단일 장면용)
    "title": "--title", "subtitle": "--subtitle", "narration": "--narration",
    "badge": "--badge", "brand": "--brand", "voice": "--voice", "rate": "--rate",
    "seconds": "--seconds", "zoom": "--zoom", "motion": "--motion",
    "theme": "--theme", "dim": "--dim", "music": "--music", "image": "--image",
}
_SHORTS_CHOICES = {"motion": ("in", "out", "pan"),
                   "theme": ("dark", "navy", "mint", "sunset", "white")}


def _shorts_dir() -> Path:
    """shorts_factory 폴더 탐색 (집 PC C:\\sy_auto\\shorts_factory 우선)."""
    cands = []
    if os.name == "nt":
        cands.append(Path(r"C:\sy_auto\shorts_factory"))
    cands += [BASE_DIR / "shorts_factory", BASE_DIR.parent / "shorts_factory",
              Path("work/pc-auto/shorts_factory")]
    seen = set()
    for c in cands:
        try:
            key = str(c.resolve())
        except Exception:
            key = str(c)
        if key in seen:
            continue
        seen.add(key)
        try:
            if (c / "make_shorts.py").exists():
                return c
        except OSError:
            continue
    return BASE_DIR / "shorts_factory"


def _safe_media_name(name, prefix: str = "shorts", ext: str = ".mp4") -> str:
    """파일명 정리(경로 이동 문자 제거) — mp4 용."""
    base = re.sub(r"[^0-9A-Za-z가-힣._-]", "_", Path(str(name or "")).name).strip("._-")
    if not base:
        base = prefix + "_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    if not base.lower().endswith(ext):
        base += ext
    return base


def send_telegram_video(cfg: dict, path, caption: str = "") -> dict:
    """MP4 1개를 sendVideo 로 전송(실패하면 sendDocument 로 재시도)."""
    token, chat_id = _tg_creds(cfg)
    p = Path(path)
    if not token or not chat_id:
        return {"ok": False, "error": "텔레그램 토큰/chat_id 없음"}
    if not p.exists():
        return {"ok": False, "error": f"파일 없음: {p.name}"}
    size_mb = p.stat().st_size / (1024 * 1024)
    if size_mb > SHORTS_VIDEO_MAX_MB:
        return {"ok": False, "too_big": True, "size_mb": round(size_mb, 1),
                "error": f"{size_mb:.1f}MB — 봇 전송 한도 50MB 초과(파일 경로: {p})"}
    payload = p.read_bytes()
    last = {"ok": False, "error": "전송 시도 없음"}
    for method, field in (("sendVideo", "video"), ("sendDocument", "document")):
        try:
            boundary = "----SY" + uuid.uuid4().hex
            fields = [("chat_id", chat_id), ("caption", (caption or "")[:1000])]
            if method == "sendVideo":
                fields.append(("supports_streaming", "true"))
            parts = []
            for key, val in fields:
                parts.append(
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{val}\r\n".encode("utf-8")
                )
            parts.append(
                (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
                 f"filename=\"{p.name}\"\r\nContent-Type: video/mp4\r\n\r\n").encode("utf-8")
            )
            parts.append(payload)
            parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/{method}",
                data=b"".join(parts),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=180) as r:
                res = json.loads(r.read().decode("utf-8", "replace"))
            res_msg = (res.get("result") or {}) if isinstance(res, dict) else {}
            if isinstance(res, dict) and res.get("ok"):
                return {"ok": True, "method": method, "message_id": res_msg.get("message_id"),
                        "file": p.name, "size_mb": round(size_mb, 1)}
            last = {"ok": False, "method": method, "error": _tg_clean((res or {}).get("description"), cfg)}
        except Exception as e:
            last = {"ok": False, "method": method, "error": _tg_clean(f"{type(e).__name__}: {e}", cfg)}
        log(f"텔레그램 {method} 실패({p.name}): {last.get('error')}")
    return last


def _shorts_scene_list(args: dict):
    """args.scenes(리스트/JSON문자열/파일경로) 또는 args.script → (장면 리스트, 오류문자열)."""
    raw = args.get("scenes")
    if raw is None:
        raw = args.get("script")
    if raw is None:
        return None, ""
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None, ""
        if s.startswith("{") or s.startswith("["):
            try:
                raw = json.loads(s)
            except Exception as e:
                return None, f"scenes JSON 해석 실패: {e}"
        else:
            p = Path(s)
            if not p.is_absolute():
                p = _shorts_dir() / p
            if not p.exists():
                return None, f"장면 파일 없음: {s}"
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                return None, f"장면 파일 읽기 실패: {e}"
    if isinstance(raw, dict):
        raw = raw.get("scenes") or raw.get("scene") or []
    if not isinstance(raw, list) or not raw:
        return None, "scenes 가 비어 있습니다"
    scenes = []
    for it in raw:
        if isinstance(it, dict):
            sc = dict(it)
            # 손으로 쓴 JSON에서 "\\n"을 그대로 붙여도 줄바꿈으로 처리
            for key in ("title", "subtitle", "badge", "narration"):
                if isinstance(sc.get(key), str) and "\\n" in sc[key]:
                    sc[key] = sc[key].replace("\\n", "\n")
            scenes.append(sc)
        elif isinstance(it, str) and it.strip():
            scenes.append({"title": it.strip()})
    if not scenes:
        return None, "scenes 항목이 올바르지 않습니다"
    return scenes, ""


_SHORTS_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
_SHORTS_IMG_MAX_MB = 20


def _shorts_download_images(scenes: list, dest_dir: Path) -> tuple[list, int]:
    """장면의 image 가 http(s) URL 이면 실제 파일로 내려받아 로컬 경로로 바꾼다.

    단색 배경이 아닌 실물 사진 장면을 원격 명령 한 줄로 쓰기 위한 처리(2026-11-28 패치).
    반환: (오류 목록, 내려받은 수)
    """
    errors: list[str] = []
    saved = 0
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return [f"이미지 폴더 생성 실패: {type(e).__name__}: {e}"], 0
    for i, sc in enumerate(scenes):
        raw = sc.get("image")
        if not isinstance(raw, str):
            continue
        url = raw.strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (SY-Shorts/1.0)", "Accept": "image/*,*/*"})
            with urllib.request.urlopen(req, timeout=45) as r:
                data = r.read(_SHORTS_IMG_MAX_MB * 1024 * 1024 + 1)
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not data:
                errors.append(f"장면{i + 1}: 빈 응답({url[-60:]})")
                continue
            if len(data) > _SHORTS_IMG_MAX_MB * 1024 * 1024:
                errors.append(f"장면{i + 1}: {_SHORTS_IMG_MAX_MB}MB 초과")
                continue
            if ctype and not ctype.startswith("image/"):
                errors.append(f"장면{i + 1}: 이미지가 아님({ctype})")
                continue
            stem = Path(urllib.parse.urlparse(url).path).stem
            ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
            if ext not in _SHORTS_IMG_EXT:
                ext = {"image/png": ".png", "image/webp": ".webp", "image/bmp": ".bmp"}.get(ctype, ".jpg")
            name = re.sub(r"[^0-9A-Za-z._-]", "", f"s{i + 1:02d}_{stem}")[:60] or f"s{i + 1:02d}"
            path = dest_dir / (name + ext)
            cached = path.exists() and path.stat().st_size == len(data)
            if not cached:
                path.write_bytes(data)
            sc["image"] = str(path)
            sc["image_url"] = url
            saved += 1
            log(f"쇼츠 장면{i + 1} 이미지 {'캐시' if cached else '저장'}: {path.name} ({len(data) // 1024}KB)")
        except Exception as e:
            errors.append(f"장면{i + 1}: {type(e).__name__}: {e}")
    return errors, saved


def _report_pc_result(cfg: dict, key: str, payload: dict) -> None:
    """원격 명령 결과를 저장소 pc_last_result.json 에 남긴다(밖에서 결과를 확인할 수 있게)."""
    try:
        token = _get_github_token(cfg)
        if not token:
            log(f"결과 기록 생략({key}): GitHub 토큰 없음")
            return
        body = dict(payload)
        body["at"] = datetime.now(KST).isoformat(timespec="seconds")
        res = _github_put_json("pc_last_result.json", {key: body}, f"chore: {key} 결과 기록", token)
        log(f"결과 기록({key}): {res}")
    except Exception as e:
        log(f"결과 기록 실패({key}): {type(e).__name__}: {e}")


def _shorts_cli_extra(args: dict, skip=()) -> list:
    """단일 장면 인자 → make_shorts.py 옵션 목록(허용 키·선택지만)."""
    out = []
    for key, flag in _SHORTS_CLI_OPTS.items():
        if key in skip:
            continue
        val = args.get(key)
        if val in (None, ""):
            continue
        sv = str(val)
        if key in _SHORTS_CHOICES and sv not in _SHORTS_CHOICES[key]:
            continue
        if key in ("seconds", "zoom", "dim"):
            try:
                float(sv)
            except Exception:
                continue
        out += [flag, sv]
    return out


def make_shorts(cfg: dict, args: dict, source: str = "원격") -> dict:
    """shorts_factory/make_shorts.py 원격 실행 → MP4 생성 → 텔레그램 전송 → (선택)유튜브 업로드."""
    args = args or {}
    sdir = _shorts_dir()
    make_py = sdir / "make_shorts.py"
    if not make_py.exists():
        msg = f"🎬 [쇼츠 ({source})] make_shorts.py 를 찾지 못했습니다\n• 확인 경로: {sdir}"
        log(msg)
        send_telegram(cfg, msg)
        return {"ok": False, "error": "make_shorts.py 없음"}

    out_dir = sdir / "out"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log(f"쇼츠 out 폴더 생성 실패: {e}")

    scenes, serr = _shorts_scene_list(args)
    if serr:
        msg = f"🎬 [쇼츠 ({source})] 시나리오 오류: {serr}"
        log(msg)
        send_telegram(cfg, msg)
        return {"ok": False, "error": serr}

    # 장면 이미지가 원격 URL 이면 먼저 집 PC 로 내려받는다(실물 사진 장면 지원)
    if scenes and any(isinstance(sc.get("image"), str)
                      and sc["image"].strip().lower().startswith(("http://", "https://"))
                      for sc in scenes):
        derr, dn = _shorts_download_images(scenes, out_dir / "img")
        if derr:
            msg = (f"🎬 [쇼츠 ({source})] 장면 이미지 내려받기 실패 {len(derr)}건 — 만들지 않았습니다\n"
                   + "\n".join(f"• {e}" for e in derr[:5]))
            log(msg)
            send_telegram(cfg, msg)
            _report_pc_result(cfg, "make_shorts", {"ok": False, "stage": "download",
                                                   "errors": derr[:5], "source": source})
            return {"ok": False, "error": "이미지 내려받기 실패", "errors": derr[:5]}
        if dn:
            log(f"쇼츠 이미지 {dn}장 내려받음")

    fname = _safe_media_name(args.get("filename") or "", "shorts")
    out_path = out_dir / fname
    cmd = [sys.executable, str(make_py)]
    script_path = None

    if scenes:
        # 장면 목록은 shorts_factory/out 에 임시 JSON 으로 저장해 --script 로 넘긴다
        script_path = out_dir / (Path(fname).stem + ".scenes.json")
        try:
            script_path.write_text(json.dumps({"scenes": scenes}, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
            cmd += ["--script", str(script_path)]
        except Exception as e:
            msg = f"🎬 [쇼츠 ({source})] 장면 파일 저장 실패: {type(e).__name__}: {e}"
            log(msg)
            send_telegram(cfg, msg)
            return {"ok": False, "error": str(e)}
        # scenes 모드에서도 전체에 걸리는 옵션만 넘긴다(보이스·속도는 장면에 없을 때의 기본값)
        cmd += _shorts_cli_extra(args, skip=("title", "subtitle", "narration", "badge", "image"))
    else:
        extra = _shorts_cli_extra(args)
        if not extra:
            msg = (f"🎬 [쇼츠 ({source})] 만들 내용이 없습니다\n"
                   "• args.scenes(장면 배열) 또는 title·subtitle 같은 문구를 넣어 주세요")
            log(msg)
            send_telegram(cfg, msg)
            return {"ok": False, "error": "입력 없음"}
        cmd += extra
    cmd += ["--out", str(out_path)]

    try:
        gen_timeout = int(args.get("timeout") or 300)
    except Exception:
        gen_timeout = 300
    gen_timeout = max(60, min(1800, gen_timeout))

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    started = time.time()
    rc, out, err, timedout = 0, "", "", False
    try:
        proc = subprocess.run(cmd, cwd=str(sdir), capture_output=True,
                              timeout=gen_timeout, env=env)
        rc, out, err = proc.returncode, _decode_out(proc.stdout), _decode_out(proc.stderr)
    except subprocess.TimeoutExpired:
        timedout, rc = True, -1
    except Exception as e:
        log(f"쇼츠 생성 실행 오류: {type(e).__name__}: {e}")
        send_telegram(cfg, f"🎬 [쇼츠 ({source})] 실행 오류: {type(e).__name__}: {e}")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    elapsed = int(time.time() - started)

    ok = (rc == 0) and not timedout and out_path.exists() and out_path.stat().st_size > 0
    if not ok:
        tail = [x for x in (err or out or "").splitlines() if x.strip()][-4:]
        msg = (f"🎬 [쇼츠 생성 실패 ({source})]\n"
               f"• 결과: {'시간초과 ' + str(gen_timeout) + '초' if timedout else '종료코드 ' + str(rc)}\n"
               f"• 소요: {elapsed}초")
        if tail:
            msg += "\n• " + "\n• ".join(t.strip()[:160] for t in tail)
        log(msg)
        send_telegram(cfg, msg)
        _report_pc_result(cfg, "make_shorts", {"ok": False, "stage": "render", "rc": rc,
                                               "elapsed": elapsed, "scenes": len(scenes or []),
                                               "tail": [t.strip()[:160] for t in tail][:4],
                                               "source": source})
        return {"ok": False, "rc": rc, "elapsed": elapsed, "error": "생성 실패"}

    size_mb = out_path.stat().st_size / (1024 * 1024)
    scene_n = len(scenes) if scenes else 1
    caption = (f"🎬 쇼츠 생성 완료 ({source})\n"
               f"• 파일: {out_path.name}\n"
               f"• 장면 {scene_n}개 · {size_mb:.2f}MB · {elapsed}초")
    vres = send_telegram_video(cfg, out_path, caption)

    lines = [f"🎬 [쇼츠 생성 {'완료' if vres.get('ok') else '완료·전송 실패'} ({source})]",
             f"• 파일: {out_path.name} ({size_mb:.2f}MB)",
             f"• 장면 {scene_n}개 · 소요 {elapsed}초",
             f"• 경로: {out_path}"]
    if vres.get("ok"):
        lines.append(f"• 전송: {vres.get('method')} · message_id {vres.get('message_id')}")
    else:
        lines.append(f"• 전송 오류: {vres.get('error')}")

    result = {"ok": True, "path": str(out_path), "file": out_path.name,
              "size_mb": round(size_mb, 2), "elapsed": elapsed,
              "scenes": scene_n, "video_message_id": vres.get("message_id"),
              "video_sent": bool(vres.get("ok")), "uploaded": False}

    # ---- 유튜브 업로드 (args.upload 가 True 이고 token.json 이 있을 때만)
    upload_py = sdir / "youtube_upload.py"
    raw_upload = args.get("upload")
    if isinstance(raw_upload, bool):
        do_upload = raw_upload
    else:   # "false" 같은 문자열 오타로 공개 업로드가 나가지 않게
        do_upload = str(raw_upload or "").strip().lower() in ("1", "true", "yes", "y", "on")
    if do_upload:
        token_ok = (sdir / "token.json").exists()
        if not upload_py.exists():
            lines.append("• 업로드 생략: youtube_upload.py 없음")
        elif not token_ok:
            lines.append("• 업로드 생략: shorts_factory/token.json 없음 (집 PC에서 1회 인증 필요)")
        else:
            privacy = str(args.get("privacy") or "private").lower()
            if privacy not in ("public", "unlisted", "private"):
                privacy = "private"
            # 영상 언어 — 제목·장면 글자에 가나가 있으면 일본어(자동 자막이 한국어로 잡히지 않게)
            yt_lang = str(args.get("yt_lang") or "").strip().lower()[:8]
            if not yt_lang:
                probe = [str(args.get("yt_title") or ""), str(args.get("yt_description") or "")]
                for sc in (scenes or []):
                    if isinstance(sc, dict):
                        probe += [str(sc.get("title") or ""), str(sc.get("subtitle") or ""),
                                  str(sc.get("narration") or "")]
                yt_lang = "ja" if re.search(r"[\u3040-\u30ff]", " ".join(probe)) else "ko"
            yt_title = str(args.get("yt_title") or "").strip()
            yt_title = re.sub(r"\s+", " ", yt_title.replace("\\n", " ").replace("\n", " ")).strip()
            if not yt_title and scenes:
                first = scenes[0] if isinstance(scenes[0], dict) else {}
                yt_title = re.sub(r"\s+", " ", str(first.get("title") or "").replace("\n", " ")).strip()
            if not yt_title:
                yt_title = str(args.get("title") or "").strip() or f"쇼츠 {datetime.now():%m/%d}"
            ucmd = [sys.executable, str(upload_py), "--video", str(out_path),
                    "--title", yt_title[:100], "--privacy", privacy, "--lang", yt_lang]
            if str(args.get("yt_description") or "").strip():
                ucmd += ["--description", str(args.get("yt_description"))[:4500]]
            tags = args.get("yt_tags")
            if isinstance(tags, (list, tuple)):
                tags = ",".join(str(t).strip() for t in tags if str(t).strip())
            if str(tags or "").strip():
                ucmd += ["--tags", str(tags)[:480]]
            if str(args.get("publish_at") or "").strip():
                ucmd += ["--publish-at", str(args.get("publish_at")).strip()]
            if str(args.get("pinned_comment") or "").strip():
                ucmd += ["--pinned-comment", str(args.get("pinned_comment")).strip()]
            try:
                up_timeout = int(args.get("upload_timeout") or 600)
            except Exception:
                up_timeout = 600
            up_timeout = max(120, min(1800, up_timeout))
            lines.append(f"• 업로드 시작: privacy={privacy} · 제목 '{yt_title[:60]}'")
            send_telegram(cfg, "\n".join(lines))   # 업로드 전 중간 보고(대기 체감 줄이기)
            lines = []
            u_started = time.time()
            urc, uout, uerr, utimed = 0, "", "", False
            try:
                uproc = subprocess.run(ucmd, cwd=str(sdir), capture_output=True,
                                       timeout=up_timeout, env=env)
                urc = uproc.returncode
                uout, uerr = _decode_out(uproc.stdout), _decode_out(uproc.stderr)
            except subprocess.TimeoutExpired:
                utimed, urc = True, -1
            except Exception as e:
                uerr = f"{type(e).__name__}: {e}"
            u_elapsed = int(time.time() - u_started)
            m = re.search(r"https://youtu\.be/[A-Za-z0-9_-]{6,}", uout or "")
            url = m.group(0) if m else ""
            if urc == 0 and url:
                lines.append(f"✅ [유튜브 업로드 완료 ({source})] {url}")
                lines.append(f"• 제목: {yt_title[:80]}")
                lines.append(f"• 공개: {privacy} · 소요 {u_elapsed}초")
                result.update({"uploaded": True, "url": url, "upload_privacy": privacy,
                               "yt_lang": yt_lang, "yt_title": yt_title})
            else:
                tail = [x for x in (uerr or uout or "").splitlines() if x.strip()][-4:]
                head = ("업로드 시간초과 " + str(up_timeout) + "초" if utimed
                        else f"업로드 실패(종료코드 {urc})")
                lines.append(f"⚠️ [유튜브 {head} ({source})] 파일은 정상 생성됨")
                if tail:
                    lines.append("• " + "\n• ".join(t.strip()[:150] for t in tail))
                result.update({"uploaded": False, "upload_error": head})

    # ---- 인스타 릴스 교차 발행(플래그·설정·공개 URL 이 모두 있을 때만 · 기본 드라이런)
    ig_res = _maybe_cross_post_instagram(cfg, args, result, source=source)
    if ig_res is not None:
        result["instagram"] = ig_res
        if ig_res.get("ok") and not ig_res.get("dry_run"):
            lines.append(f"• 인스타 릴스 발행: {ig_res.get('media_id')}")
        elif ig_res.get("dry_run"):
            lines.append("• 인스타: 드라이런(검증만) — ig_confirm=true 로 실제 발행")
        else:
            lines.append(f"• 인스타 생략: {ig_res.get('skipped') or ig_res.get('error')}")

    msg = "\n".join(lines)
    log(msg)
    send_telegram(cfg, msg)
    # 어떤 보이스로 나레이션을 만들었는지(일본어 자동 선택 확인용)를 결과에 남긴다
    result["voices"] = sorted(set(re.findall(r"\[나레이션\] (\S+)", out or "")))
    result["stdout_tail"] = [x.strip()[:160] for x in (out or "").splitlines() if x.strip()][-6:]
    result["source"] = source
    _report_pc_result(cfg, "make_shorts", result)
    return result


# ---------------------------------------------------------------- 지식iN·스레드 후보글 + 무료 Gemini 필터 → pc_targets.json
#  ★ 흐름: (1) 공개 검색으로 최신 후보 글 수집(로그인·쿠키 없음)
#          → (2) gemini_config.json 의 api_key 가 있을 때만 무료 Gemini API 로
#               '진짜 도움이 필요한 글인지' 판별 + 자연스러운 답글·피클 초대 초안 생성
#          → (3) 저장소 pc_targets.json 푸시.
#  ★ 키가 없거나 비어 있으면 수집만 하고 조용히 건너뛴다(예외·알림 없음).
#    키·토큰 원문은 로그에도 저장소에도 남기지 않는다.
#  ★ 지식iN 은 비로그인 공개 검색(list.naver?sort=date)만 읽는다.
#    스레드 공개 검색은 클라이언트 렌더링이라, 공식 keyword_search 토큰이 있을 때만 시도한다.
GEMINI_CONFIG_FILE = BASE_DIR / "gemini_config.json"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
#  ★ 신규 무료 키(`AQ.` 시작)는 gemini-2.5-flash 를 404 로 거절한다 → 별칭 gemini-flash-latest 를 쓴다.
GEMINI_DEFAULT_MODEL = "gemini-flash-latest"
GEMINI_LEGACY_MODELS = ("gemini-2.5-flash",)   # 구 기본값 — 설정에 남아 있으면 자동 보정
TARGETS_PUSH_PATH = "pc_targets.json"
TARGETS_KEEP_MAX = 60            # pc_targets.json 에 남기는 후보 최대 수(최신 우선 롤링)
TARGETS_SEEN_MAX = 400           # state.json 에 기억하는 처리 완료 URL 수
TARGETS_DEFAULT_KIN_QUERIES = [
    "AI 비서 일정 정리 추천",
    "환급금 조회 방법",
    "일정 관리 앱 추천",
    "정리정돈 앱 추천",
]
TARGETS_DEFAULT_THREADS_QUERIES = ["환급금 조회", "일정 정리", "생활 꿀팁"]
TARGETS_DEFAULT_PERSONA = ("Pickle(피클)은 한국어로 대화하는 개인 AI 비서다. 일정·할일 정리, "
                           "환급금·행정 서류 확인, 생활 꿀팁을 도와준다.")
TARGETS_GEMINI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "index": {"type": "INTEGER"},
                    "needs_help": {"type": "BOOLEAN"},
                    "score": {"type": "NUMBER"},
                    "reason": {"type": "STRING"},
                    "reply": {"type": "STRING"},
                    "invite": {"type": "STRING"},
                },
                "required": ["index", "needs_help", "score", "reason", "reply"],
            },
        }
    },
    "required": ["items"],
}


def _resolve_gemini_model(value) -> str:
    """gemini_config.json 의 model 값 → 실제 호출 모델명.

    비어 있거나 구 기본값(gemini-2.5-flash)이면 gemini-flash-latest 로 보정한다.
    사용자가 직접 넣은 값 — gemini-flash-latest 포함 — 은 그대로 쓴다.
    """
    m = str(value or "").strip()
    if not m:
        return GEMINI_DEFAULT_MODEL
    if m.lower() in GEMINI_LEGACY_MODELS:
        return GEMINI_DEFAULT_MODEL
    return m


def load_gemini_config() -> dict:
    """gemini_config.json 로드. 없으면 api_key 빈 템플릿만 만들어 둔다(키는 절대 로그에 안 찍는다)."""
    template = {
        "api_key": "",
        "enabled": True,
        "model": GEMINI_DEFAULT_MODEL,
        "max_candidates": 12,
        "min_score": 0.55,
        "kin_queries": list(TARGETS_DEFAULT_KIN_QUERIES),
        "threads_queries": list(TARGETS_DEFAULT_THREADS_QUERIES),
        "persona": TARGETS_DEFAULT_PERSONA,
        "_help": ("무료 Gemini API 키를 api_key 에 넣으면 지식iN·스레드 후보 글을 AI 로 걸러 "
                  "답글·피클 초대 초안까지 만들어 pc_targets.json 으로 올립니다. "
                  "비워 두면 후보 수집만 하고 푸시하지 않습니다(오류 없음). "
                  "모델은 model 값(기본 gemini-flash-latest)을 따릅니다. "
                  "gemini-2.5-flash 는 신규 무료 키에서 404 라 넣지 마세요."),
    }
    if not GEMINI_CONFIG_FILE.exists():
        try:
            GEMINI_CONFIG_FILE.write_text(json.dumps(template, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
            log("Gemini 설정 템플릿 생성: %s" % GEMINI_CONFIG_FILE)
        except Exception as e:
            log("Gemini 설정 템플릿 생성 실패: %s: %s" % (type(e).__name__, e))
        return dict(template)
    try:
        raw = json.loads(GEMINI_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log("Gemini 설정 해석 실패: %s: %s" % (type(e).__name__, e))
        return dict(template)
    out = dict(template)
    if isinstance(raw, dict):
        out.update(raw)
    out["api_key"] = str(out.get("api_key") or "").strip()
    raw_model = str(out.get("model") or "").strip()
    out["model"] = _resolve_gemini_model(raw_model)
    if raw_model and out["model"] != raw_model:
        log("Gemini 모델 자동 보정: %s → %s (구 기본값, 신규 키에서 404)" % (raw_model, out["model"]))
    out["enabled"] = bool(out.get("enabled", True))
    try:
        out["max_candidates"] = max(1, min(30, int(out.get("max_candidates") or 12)))
    except Exception:
        out["max_candidates"] = 12
    try:
        out["min_score"] = max(0.0, min(1.0, float(out.get("min_score") or 0.55)))
    except Exception:
        out["min_score"] = 0.55
    for key, default in (("kin_queries", TARGETS_DEFAULT_KIN_QUERIES),
                         ("threads_queries", TARGETS_DEFAULT_THREADS_QUERIES)):
        vals = out.get(key)
        if isinstance(vals, str):
            vals = [vals]
        out[key] = [str(v).strip() for v in (vals or []) if str(v).strip()][:8] or list(default)
    return out


def _gemini_ready(gc: dict) -> tuple:
    """(준비됨, 사유). 키가 비면 False — 예외를 던지지 않는다."""
    if not isinstance(gc, dict):
        return False, "gemini_config 없음"
    if not gc.get("enabled", True):
        return False, "gemini_config.enabled=false"
    if not str(gc.get("api_key") or "").strip():
        return False, "api_key 미설정"
    return True, ""


def _targets_gap_seconds(targets_sec: int) -> int:
    """다음 후보글 수집까지 기다릴 초. Gemini 키가 없으면 30분마다 확인한다.

    토큰·키를 19:30 에 넣었는데 정규 주기(기본 180분) 때문에 두 시간을 기다리는 일이 없도록,
    키가 없는 동안에는 짧게(최대 30분) 다시 본다. 키가 들어오면 정규 주기로 돌아간다.
    """
    try:
        ready = _gemini_ready(load_gemini_config())[0]
    except Exception:
        ready = False
    return int(targets_sec) if ready else min(int(targets_sec), 1800)


def _gemini_note() -> str:
    """상태 보고용 한 줄 — 키 값 자체는 절대 찍지 않는다."""
    try:
        ok, why = _gemini_ready(load_gemini_config())
        return "준비됨" if ok else why
    except Exception:
        return "확인 실패"


def _strip_tags(text) -> str:
    s = re.sub(r"<[^>]+>", " ", str(text or ""))
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _kin_candidates(query: str, limit: int = 8) -> list:
    """지식iN 공개 검색(최신순) → 후보 목록. 로그인·쿠키 없이 HTML 만 읽는다."""
    url = ("https://kin.naver.com/search/list.naver?"
           + urllib.parse.urlencode({"query": query, "sort": "date"}))
    req = urllib.request.Request(url, headers={
        "User-Agent": _BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": "https://kin.naver.com/",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        text = r.read(500000).decode("utf-8", "replace")
    out = []
    for block in re.findall(r"<li data-nlog-imp-params=.*?</li>", text, re.S):
        am = re.search(r"<dt>\s*<a href=\"([^\"]+)\"[^>]*>(.*?)</a>", block, re.S)
        if not am:
            continue
        link = html.unescape(am.group(1)).strip()
        title = _strip_tags(am.group(2))
        if not link.startswith("http") or not title:
            continue
        posted, snippet = "", ""
        for cls, body in re.findall(r"<dd([^>]*)>(.*?)</dd>", block, re.S):
            body_txt = _strip_tags(body)
            if "txt_inline" in cls and not posted:
                posted = body_txt
            elif "txt_block" not in cls and not snippet and len(body_txt) > 10:
                snippet = body_txt
        dm = re.search(r"docId=(\d+)", link)
        out.append({
            "id": "kin-%s" % (dm.group(1) if dm else abs(hash(link)) % 10 ** 9),
            "source": "kin",
            "channel": "지식iN",
            "query": query,
            "title": title[:160],
            "snippet": snippet[:400],
            "url": link,
            "posted_at": posted,
        })
        if len(out) >= limit:
            break
    return out


def _threads_search_candidates(query: str, token: str = "", limit: int = 8) -> list:
    """스레드 공개 검색 — 공식 keyword_search 토큰이 있을 때만. 없으면 빈 목록(조용히)."""
    token = str(token or "").strip()
    if not token:
        return []
    params = urllib.parse.urlencode({
        "q": query, "search_type": "RECENT", "limit": str(max(1, min(limit, 25))),
        "fields": "id,text,permalink,timestamp,username",
    })
    req = urllib.request.Request(
        "https://graph.threads.net/v1.0/keyword_search?" + params,
        headers={"Authorization": "Bearer %s" % token,
                 "User-Agent": "SY-PC-Watch/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        res = json.loads(r.read().decode("utf-8", "replace"))
    out = []
    for row in (res.get("data") or []):
        if not isinstance(row, dict):
            continue
        text = re.sub(r"\s+", " ", str(row.get("text") or "")).strip()
        link = str(row.get("permalink") or "").strip()
        if not text or not link:
            continue
        out.append({
            "id": "th-%s" % str(row.get("id") or abs(hash(link)) % 10 ** 9),
            "source": "threads",
            "channel": "스레드",
            "query": query,
            "title": text[:120],
            "snippet": text[:400],
            "url": link,
            "posted_at": str(row.get("timestamp") or ""),
            "author": str(row.get("username") or ""),
        })
        if len(out) >= limit:
            break
    return out


def collect_target_candidates(gc: dict, state: dict) -> tuple:
    """(후보 리스트, 오류 리스트, 쿼리 요약) — 실패한 소스는 기록만 하고 계속 간다."""
    candidates, errors, counts = [], [], {}
    seen = set(str(u) for u in (state.get("targets_seen") or []))
    for query in (gc.get("kin_queries") or []):
        try:
            rows = _kin_candidates(query)
            fresh = [r for r in rows if r["url"] not in seen]
            candidates += fresh
            counts["지식iN"] = counts.get("지식iN", 0) + len(fresh)
        except Exception as e:
            errors.append("지식iN '%s' %s: %s" % (query, type(e).__name__, str(e)[:120]))
    threads_token = ""
    try:
        threads_token = str((load_threads_config() or {}).get("access_token") or "").strip()
    except Exception:
        threads_token = ""
    if threads_token:
        for query in (gc.get("threads_queries") or []):
            try:
                rows = _threads_search_candidates(query, threads_token)
                fresh = [r for r in rows if r["url"] not in seen]
                candidates += fresh
                counts["스레드"] = counts.get("스레드", 0) + len(fresh)
            except Exception as e:
                errors.append("스레드 '%s' %s: %s" % (
                    query, type(e).__name__,
                    _scrub_threads_token(str(e), threads_token)[:120]))
    else:
        counts["스레드"] = 0
    # 같은 URL 중복 제거(쿼리 여러 개가 같은 글을 물어올 수 있다)
    uniq, seen_urls = [], set()
    for row in candidates:
        if row["url"] in seen_urls:
            continue
        seen_urls.add(row["url"])
        uniq.append(row)
    return uniq[:30], errors, counts


def _gemini_generate(gc: dict, prompt: str, timeout: int = 60) -> tuple:
    """Gemini generateContent 1회 → (응답 텍스트, 오류문자열). 키는 헤더로만 보낸다(URL·로그 노출 없음)."""
    key = str(gc.get("api_key") or "").strip()
    if not key:
        return "", "api_key 미설정"
    model = _resolve_gemini_model(gc.get("model"))
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json",
            "responseSchema": TARGETS_GEMINI_SCHEMA,
        },
    }
    req = urllib.request.Request(
        "%s/models/%s:generateContent" % (GEMINI_API_BASE, urllib.parse.quote(model)),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key,
                 "User-Agent": "SY-PC-Watch/1.0"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300].replace(key, "<key>")
        hint = " (무료 할당량 초과 — 잠시 뒤 자동 재시도)" if e.code == 429 else ""
        return "", "HTTP %s%s: %s" % (e.code, hint, detail)
    except Exception as e:
        return "", "%s: %s" % (type(e).__name__, str(e).replace(key, "<key>")[:200])
    for cand in (res.get("candidates") or []):
        for part in (((cand or {}).get("content") or {}).get("parts") or []):
            text = str((part or {}).get("text") or "").strip()
            if text:
                return text, ""
    return "", "응답에 텍스트 없음"


def _targets_prompt(gc: dict, candidates: list) -> str:
    lines = [
        "너는 한국어 커뮤니티(지식iN·스레드)에서 '지금 실제로 도움이 필요한 글'만 골라 답글을 준비하는 도우미다.",
        "아래 후보 글 목록을 읽고 각 글마다 판단해 JSON 으로만 답한다.",
        "",
        "[판단 기준]",
        "- 실제 궁금증·고민·요청이 있어 답변이 도움이 되는 글: needs_help=true",
        "- 광고·홍보·뉴스·단순 잡담·감정 토로·정치/종교 논쟁·연애 상담·의료 진단·법률 판단·투자 권유: false",
        "- 개인정보를 캐묻거나 사행성·불법·자해·미성년 관련: 반드시 false",
        "- score: 0.0~1.0 (도움 필요도·답변 가능성). min_score 미만은 false 로 본다.",
        "- reply: 바로 쓸 수 있는 한국어 답글 초안 2~5문장(500자 이내). 상대를 '님'으로 부르고,",
        "  아는 척·과장·확정적 의료/법률/투자 조언 금지. 모르면 확인 방법을 알려준다.",
        "- reply 문단: 줄바꿈 없는 벽돌 문단 금지. 1~2문장마다 빈 줄을 넣어",
        "  모바일에서 1초 만에 읽히게 쓴다.",
        "- reply 말투: '~습니다' 설명조·AI 같은 상투어·홍보투 금지. 실제 동네 사장님·직장인이",
        "  답장 쓰는 짧고 담백한 구어체(~요/~죠/~더라고요).",
        "- reply 예시:",
        "  맞아요 그 사이사이 잡무가 진짜 시간 다 뺏죠",
        "  ",
        "  저도 답장이랑 정리는 AI 비서한테 넘기고 한숨 돌렸어요. 필요하시면 링크 남겨둘게요",
        "- invite: 답글 뒤에 붙일 피클(Pickle) 초대 한 줄(선택). 광고 문구처럼 보이지 않게. 없으면 빈 문자열.",
        "- reason: 판단 이유 한 줄.",
        "",
        "[페르소나] " + str(gc.get("persona") or TARGETS_DEFAULT_PERSONA),
        "",
        "[후보 글]",
    ]
    for idx, c in enumerate(candidates, 1):
        lines.append("%d. [%s] 제목: %s / 요약: %s / 시각: %s" % (
            idx, c.get("channel") or c.get("source"),
            (c.get("title") or "")[:120], (c.get("snippet") or "")[:200],
            c.get("posted_at") or "-"))
    lines += ["", 'JSON: {"items":[{"index":1,"needs_help":true,"score":0.8,"reason":"...","reply":"...","invite":"..."}]}']
    return "\n".join(lines)


def _parse_gemini_items(text: str) -> list:
    """모델 응답 텍스트 → items 리스트. 코드펜스·앞뒤 설명이 섞여도 버틴다."""
    raw = str(text or "").strip()
    if not raw:
        return []
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    data = None
    try:
        data = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                data = None
    if isinstance(data, list):
        data = {"items": data}
    if not isinstance(data, dict):
        return []
    items = data.get("items")
    return [i for i in (items or []) if isinstance(i, dict)]


def _tidy_reply(text: str) -> str:
    """답글 초안이 벽돌 문단이 되지 않게 1~2문장마다 빈 줄로 나눈다(모델이 규칙을 놓쳐도 보정)."""
    s = str(text or "").replace("\r\n", "\n").strip()
    if not s:
        return ""
    s = re.sub(r"[ \t]+", " ", s)
    paras = [re.sub(r"\s*\n\s*", " ", p).strip() for p in re.split(r"\n\s*\n", s)]
    paras = [p for p in paras if p]
    if len(paras) <= 1:
        sentences = [x.strip() for x in re.findall(r"[^.!?\u3002\u2026\n]+[.!?\u3002\u2026]?", paras[0] if paras else "")
                     if x.strip()]
        if len(sentences) >= 3:
            paras = [" ".join(sentences[i:i + 2]) for i in range(0, len(sentences), 2)]
    return "\n\n".join(paras)[:600]


def filter_targets_with_gemini(gc: dict, candidates: list) -> tuple:
    """(선별 결과 리스트, 오류문자열). 후보를 max_candidates 로 줄여 1회 호출한다."""
    if not candidates:
        return [], ""
    picked = candidates[:int(gc.get("max_candidates") or 12)]
    text, err = _gemini_generate(gc, _targets_prompt(gc, picked))
    if err:
        return [], err
    items = _parse_gemini_items(text)
    if not items:
        return [], "응답 JSON 해석 실패"
    min_score = float(gc.get("min_score") or 0.55)
    out = []
    for it in items:
        try:
            idx = int(it.get("index") or 0) - 1
        except Exception:
            continue
        if not (0 <= idx < len(picked)):
            continue
        try:
            score = float(it.get("score") or 0)
        except Exception:
            score = 0.0
        needs = bool(it.get("needs_help")) and score >= min_score
        row = dict(picked[idx])
        row.update({
            "needs_help": needs,
            "score": round(max(0.0, min(1.0, score)), 2),
            "reason": str(it.get("reason") or "")[:200],
            "reply_draft": _tidy_reply(it.get("reply")) if needs else "",
            "invite_draft": re.sub(r"\s+", " ", str(it.get("invite") or "")).strip()[:200] if needs else "",
        })
        out.append(row)
    return out, ""


def collect_targets(cfg: dict, state: dict, force_report: bool = False) -> dict:
    """후보 수집 → Gemini 필터 → pc_targets.json 푸시. 키가 없으면 조용히 건너뛴다."""
    state["last_targets_check"] = time.time()
    gc = load_gemini_config()
    candidates, errors, counts = collect_target_candidates(gc, state)
    ready, why = _gemini_ready(gc)
    result = {"ok": False, "collected": len(candidates), "counts": counts,
              "skipped": why, "errors": errors[:4], "kept": 0, "pushed": 0}
    if not candidates:
        log("후보글 수집: 신규 후보 없음%s" % (" (수집 오류 %d건)" % len(errors) if errors else ""))
        state["targets_last_error"] = (errors[0] if errors else "")
        return result
    if not ready:
        log("후보글 수집: 신규 %d건 · Gemini %s → AI 필터·푸시 생략(키 넣으면 자동 시작)" % (
            len(candidates), why))
        return result

    kept, ferr = filter_targets_with_gemini(gc, candidates)
    if ferr:
        log("후보글 Gemini 필터 실패(이번 회차 생략): %s" % ferr)
        state["targets_last_error"] = ferr[:300]
        result["errors"].append("gemini: " + ferr[:160])
        return result

    keep_rows = [r for r in kept if r.get("needs_help")]
    keep_rows.sort(key=lambda r: (-float(r.get("score") or 0), r.get("posted_at") or ""))
    token = _get_github_token(cfg)
    now = datetime.now(KST)
    payload_rows = []
    for row in keep_rows:
        payload_rows.append({
            "id": row.get("id"), "source": row.get("source"), "channel": row.get("channel"),
            "title": row.get("title"), "snippet": row.get("snippet"), "url": row.get("url"),
            "posted_at": row.get("posted_at"), "query": row.get("query"),
            "score": row.get("score"), "needs_help": True, "reason": row.get("reason"),
            "reply_draft": row.get("reply_draft"), "invite_draft": row.get("invite_draft"),
            "found_at": now.isoformat(timespec="seconds"),
        })
    result.update({"ok": True, "kept": len(payload_rows)})
    if not payload_rows:
        log("후보글 수집: 신규 %d건 → Gemini 판단 도움 필요 0건(푸시 생략)" % len(candidates))
        state["targets_last_error"] = ""
        _mark_targets_seen(state, candidates)
        return result
    if not token:
        log("후보글 푸시 생략: GitHub 토큰 없음")
        return result

    prev, _sha = _github_get_json(TARGETS_PUSH_PATH, token)
    merged, seen_urls = [], set()
    for row in payload_rows + [p for p in (prev.get("candidates") or []) if isinstance(p, dict)]:
        url = str(row.get("url") or "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        merged.append(row)
    merged = merged[:TARGETS_KEEP_MAX]
    payload = {
        "schema": "pc_targets/1",
        "generator": "sy_auto_watch.py",
        "generated_at": now.isoformat(timespec="seconds"),
        "generated_at_kst": now.strftime("%m-%d %H:%M"),
        "model": gc.get("model"),
        "counts": {"collected": len(candidates), "kept": len(keep_rows),
                   "new": len(payload_rows), "total": len(merged)},
        "sources": counts,
        "candidates": merged,
        "errors": errors[:4],
    }
    res = _github_put_json(TARGETS_PUSH_PATH, payload,
                           "chore: 후보글 %d건 AI 필터 갱신" % len(payload_rows), token)
    result["push"] = res
    if not res.get("ok"):
        log("후보글 푸시 실패: %s" % res.get("detail"))
        state["targets_last_error"] = str(res.get("detail"))[:300]
        return result
    result.update({"pushed": len(payload_rows), "commit": res.get("commit")})
    _mark_targets_seen(state, candidates)
    state["targets_last_error"] = ""
    state["last_targets_ok"] = now.isoformat(timespec="seconds")
    log("후보글 푸시 완료: 신규 %d건 중 도움필요 %d건 → %s" % (
        len(candidates), len(payload_rows), res.get("commit") or res.get("sha")))
    if force_report or payload_rows:
        send_telegram(cfg, _targets_msg(payload))
    return result


def _mark_targets_seen(state: dict, rows: list) -> None:
    seen = [str(u) for u in (state.get("targets_seen") or [])]
    for row in rows:
        url = str(row.get("url") or "")
        if url and url not in seen:
            seen.append(url)
    state["targets_seen"] = seen[-TARGETS_SEEN_MAX:]


def _targets_msg(payload: dict) -> str:
    counts = payload.get("counts") or {}
    lines = ["🎯 [후보글 · Gemini 필터] pc_targets.json 갱신 (%s KST)" % payload.get("generated_at_kst"),
             "• 수집 %s → 도움필요 %s · 신규 %s · 누적 %s" % (
                 counts.get("collected"), counts.get("kept"),
                 counts.get("new"), counts.get("total"))]
    for row in (payload.get("candidates") or [])[:3]:
        lines.append("• [%s] %s (%.2f)" % (
            row.get("channel") or row.get("source"),
            str(row.get("title") or "")[:60], float(row.get("score") or 0)))
    return "\n".join(lines)


# ---------------------------------------------------------------- 인스타(@18.1q2w3e4r) 릴스·피드 교차 발행 틀 (Meta Graph API)
#  ★ threads_config.json 의 ig_user_id·ig_access_token 이 채워졌을 때만 동작한다.
#    th_refresh_token(스레드 토큰 연장) 로직과는 분리: load_threads_config() 는 ig_* 키를 읽지도
#    쓰지도 않고, 이 섹션만 같은 파일에서 ig_* 키만 따로 읽는다.
#  ★ 미설정·빈 값이면 예외 없이 건너뛴다. 실제 발행은 confirm=true 가 있을 때만 하고 기본은 드라이런.
#  ★ Meta Graph 는 '공개 URL' 의 영상·이미지만 받는다(집 PC 로컬 mp4 직접 업로드 불가).
IG_API_VERSION = "v23.0"
IG_API_BASE_DEFAULT = "https://graph.instagram.com/%s" % IG_API_VERSION
IG_FB_API_BASE = "https://graph.facebook.com/%s" % IG_API_VERSION
IG_DEFAULT_WAIT_SECONDS = 180
IG_POLL_SECONDS = 5


def load_instagram_config() -> dict:
    """threads_config.json 에서 ig_* 키만 따로 읽는다(스레드 설정 로더와 분리)."""
    out = {"ig_user_id": "", "ig_access_token": "", "ig_api": "instagram",
           "ig_api_version": "", "ig_api_base": "", "ig_share_to_feed": True}
    if not THREADS_CONFIG_FILE.exists():
        return out
    try:
        raw = json.loads(THREADS_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log("인스타 설정 읽기 실패(스레드 설정 파일 해석 오류): %s: %s" % (type(e).__name__, e))
        return out
    if not isinstance(raw, dict):
        return out
    for key in ("ig_user_id", "ig_access_token", "ig_api", "ig_api_version", "ig_api_base"):
        out[key] = str(raw.get(key) or "").strip()
    if "ig_share_to_feed" in raw:
        out["ig_share_to_feed"] = bool(raw.get("ig_share_to_feed"))
    return out


def _ig_ready(ic: dict) -> tuple:
    """(준비됨, 사유). 값이 비면 False — 예외를 던지지 않는다."""
    if not isinstance(ic, dict):
        return False, "설정 없음"
    if not str(ic.get("ig_user_id") or "").strip():
        return False, "ig_user_id 미설정"
    if not str(ic.get("ig_access_token") or "").strip():
        return False, "ig_access_token 미설정"
    return True, ""


def _ig_note() -> str:
    """상태 보고용 한 줄 — 토큰 값 자체는 절대 찍지 않는다."""
    try:
        ok, why = _ig_ready(load_instagram_config())
        return "준비됨" if ok else why
    except Exception:
        return "확인 실패"


def _ig_mask(value, keep: int = 4) -> str:
    s = str(value or "").strip()
    if not s:
        return "미설정"
    return "설정됨(끝 %s)" % s[-keep:] if len(s) > keep + 4 else "설정됨"


def _ig_base(ic: dict) -> str:
    base = str(ic.get("ig_api_base") or "").strip()
    if base:
        return base.rstrip("/")
    ver = str(ic.get("ig_api_version") or "").strip() or IG_API_VERSION
    if str(ic.get("ig_api") or "").strip().lower() in ("facebook", "fb", "graph.facebook"):
        return "https://graph.facebook.com/%s" % ver
    return "https://graph.instagram.com/%s" % ver


def _ig_request(ic: dict, path: str, method: str = "GET",
                params: dict | None = None, body: dict | None = None,
                timeout: int = 40) -> dict:
    """Graph API 1회 호출 → {ok, data|error, code}. 토큰 원문은 로그·오류문에 남기지 않는다."""
    token = str(ic.get("ig_access_token") or "").strip()
    if not token:
        return {"ok": False, "error": "ig_access_token 미설정", "code": 0}
    url = "%s/%s" % (_ig_base(ic), str(path).lstrip("/"))
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer %s" % token,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "SY-PC-Watch/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"ok": True, "data": json.loads(r.read().decode("utf-8", "replace")),
                    "code": getattr(r, "status", 200)}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300].replace(token, "<token>")
        return {"ok": False, "error": "HTTP %s: %s" % (e.code, detail), "code": e.code}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e).replace(token, "<token>")[:200]),
                "code": 0}


def ig_create_container(ic: dict, media_type: str = "REELS", video_url: str = "",
                        image_url: str = "", caption: str = "",
                        share_to_feed: bool = True, cover_url: str = "",
                        thumb_offset: int | None = None) -> dict:
    """미디어 컨테이너 생성(1단계). 반환 {ok, container_id|error}."""
    uid = str(ic.get("ig_user_id") or "").strip()
    if not uid:
        return {"ok": False, "error": "ig_user_id 미설정"}
    body = {"caption": str(caption or "")[:2200]}
    if media_type == "REELS":
        if not str(video_url or "").startswith(("http://", "https://")):
            return {"ok": False, "error": "REELS 는 공개 video_url 이 필요합니다"}
        body.update({"media_type": "REELS", "video_url": video_url,
                     "share_to_feed": bool(share_to_feed)})
        if cover_url:
            body["cover_url"] = cover_url
        if thumb_offset is not None:
            body["thumb_offset"] = int(thumb_offset)
    else:
        if not str(image_url or "").startswith(("http://", "https://")):
            return {"ok": False, "error": "IMAGE 는 공개 image_url 이 필요합니다"}
        body.update({"image_url": image_url})
    res = _ig_request(ic, "%s/media" % uid, "POST", body=body)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error"), "stage": "container"}
    data = res.get("data") or {}
    cid = str(data.get("id") or "").strip()
    if not cid:
        return {"ok": False, "error": "container id 없음", "stage": "container"}
    return {"ok": True, "container_id": cid, "stage": "container", "raw": data}


def ig_wait_container(ic: dict, container_id: str,
                      wait_seconds: int = IG_DEFAULT_WAIT_SECONDS) -> dict:
    """컨테이너 처리 상태 폴링 → {ok, status}. ERROR/FINISHED 즉시 종료."""
    deadline = time.time() + max(0, int(wait_seconds))
    status = ""
    while True:
        res = _ig_request(ic, str(container_id), "GET", params={"fields": "status_code,status"})
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error"), "status": status}
        data = res.get("data") or {}
        status = str(data.get("status_code") or data.get("status") or "").upper()
        if status in ("FINISHED", "ERROR", "EXPIRED"):
            return {"ok": status == "FINISHED", "status": status,
                    "error": "" if status == "FINISHED" else "컨테이너 상태 %s" % status}
        if time.time() >= deadline:
            return {"ok": False, "status": status or "IN_PROGRESS",
                    "error": "처리 대기 시간 초과(%s초)" % wait_seconds}
        time.sleep(IG_POLL_SECONDS)


def ig_publish_container(ic: dict, container_id: str) -> dict:
    """컨테이너 발행(2단계) → {ok, media_id|error}."""
    uid = str(ic.get("ig_user_id") or "").strip()
    res = _ig_request(ic, "%s/media_publish" % uid, "POST",
                      body={"creation_id": container_id})
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error"), "stage": "publish"}
    mid = str((res.get("data") or {}).get("id") or "").strip()
    if not mid:
        return {"ok": False, "error": "media id 없음", "stage": "publish"}
    return {"ok": True, "media_id": mid, "stage": "publish"}


def publish_instagram_reel(cfg: dict, ic: dict | None = None, video_url: str = "",
                           caption: str = "", share_to_feed: bool = True,
                           dry_run: bool = True, wait_seconds: int = IG_DEFAULT_WAIT_SECONDS,
                           cover_url: str = "", thumb_offset: int | None = None) -> dict:
    """한국어 쇼츠(공개 URL)를 인스타 릴스로 발행하는 틀: 컨테이너 생성 → 상태 대기 → 발행."""
    ic = ic if isinstance(ic, dict) else load_instagram_config()
    ready, why = _ig_ready(ic)
    if not ready:
        log("인스타 릴스 발행 생략: %s" % why)
        return {"ok": False, "skipped": why}
    if not str(video_url or "").startswith(("http://", "https://")):
        log("인스타 릴스 발행 생략: 공개 video_url 없음")
        return {"ok": False, "skipped": "공개 video_url 없음"}
    if dry_run:
        log("인스타 릴스 드라이런(검증만): %s" % str(video_url)[:80])
        return {"ok": True, "dry_run": True, "stage": "validate",
                "video_url": video_url, "caption_len": len(str(caption or ""))}
    made = ig_create_container(ic, "REELS", video_url=video_url, caption=caption,
                               share_to_feed=share_to_feed, cover_url=cover_url,
                               thumb_offset=thumb_offset)
    if not made.get("ok"):
        return {"ok": False, "stage": made.get("stage"), "error": made.get("error")}
    wait = ig_wait_container(ic, made["container_id"], wait_seconds)
    if not wait.get("ok"):
        return {"ok": False, "stage": "status", "container_id": made["container_id"],
                "error": wait.get("error"), "status": wait.get("status")}
    pub = ig_publish_container(ic, made["container_id"])
    if not pub.get("ok"):
        return {"ok": False, "stage": pub.get("stage"), "container_id": made["container_id"],
                "error": pub.get("error")}
    log("인스타 릴스 발행 완료: media %s" % pub.get("media_id"))
    return {"ok": True, "media_id": pub.get("media_id"),
            "container_id": made["container_id"], "stage": "published"}


def publish_instagram_image(cfg: dict, ic: dict | None = None, image_url: str = "",
                            caption: str = "", dry_run: bool = True,
                            wait_seconds: int = 60) -> dict:
    """공개 이미지를 인스타 피드로 발행하는 틀(릴스와 같은 컨테이너 흐름)."""
    ic = ic if isinstance(ic, dict) else load_instagram_config()
    ready, why = _ig_ready(ic)
    if not ready:
        log("인스타 피드 발행 생략: %s" % why)
        return {"ok": False, "skipped": why}
    if not str(image_url or "").startswith(("http://", "https://")):
        return {"ok": False, "skipped": "공개 image_url 없음"}
    if dry_run:
        return {"ok": True, "dry_run": True, "stage": "validate", "image_url": image_url}
    made = ig_create_container(ic, "IMAGE", image_url=image_url, caption=caption)
    if not made.get("ok"):
        return {"ok": False, "stage": made.get("stage"), "error": made.get("error")}
    wait = ig_wait_container(ic, made["container_id"], wait_seconds)
    if not wait.get("ok"):
        return {"ok": False, "stage": "status", "container_id": made["container_id"],
                "error": wait.get("error"), "status": wait.get("status")}
    pub = ig_publish_container(ic, made["container_id"])
    if not pub.get("ok"):
        return {"ok": False, "stage": pub.get("stage"), "error": pub.get("error")}
    return {"ok": True, "media_id": pub.get("media_id"), "stage": "published"}


def instagram_status_msg(cfg: dict) -> str:
    ic = load_instagram_config()
    ready, why = _ig_ready(ic)
    kin = "준비됨" if ready else why
    return ("📸 [인스타 교차 발행 설정]\n"
            "• ig_user_id: %s\n"
            "• ig_access_token: %s\n"
            "• API: %s\n"
            "• 상태: %s\n"
            "• 릴스·피드는 공개 URL 만 발행됩니다(로컬 mp4 직접 업로드 불가)\n"
            "• 설정 위치: %s 의 ig_user_id · ig_access_token" % (
                _ig_mask(ic.get("ig_user_id")), _ig_mask(ic.get("ig_access_token")),
                _ig_base(ic), kin, THREADS_CONFIG_FILE))


def instagram_publish(cfg: dict, args: dict, source: str = "원격",
                      state: dict | None = None) -> dict:
    """원격·텔레그램 명령용 발행 진입점. confirm 이 없으면 드라이런(검증만)이다."""
    args = args or {}
    ic = load_instagram_config()
    ready, why = _ig_ready(ic)
    if not ready:
        msg = ("📸 [인스타 발행 생략] %s\n• threads_config.json 에 ig_user_id·ig_access_token 을 "
               "넣으면 바로 동작합니다." % why)
        log(msg.replace("\n", " "))
        send_telegram(cfg, msg)
        return {"ok": False, "skipped": why}
    caption = str(args.get("caption") or args.get("ig_caption") or "").strip()
    confirm = str(args.get("confirm") or "").strip().lower() in ("1", "true", "yes", "y", "on")
    try:
        wait_seconds = int(args.get("wait_seconds") or IG_DEFAULT_WAIT_SECONDS)
    except Exception:
        wait_seconds = IG_DEFAULT_WAIT_SECONDS
    video_url = str(args.get("video_url") or args.get("reel_url") or "").strip()
    image_url = str(args.get("image_url") or "").strip()
    if video_url:
        res = publish_instagram_reel(cfg, ic, video_url, caption,
                                     share_to_feed=bool(args.get("share_to_feed", ic.get("ig_share_to_feed", True))),
                                     dry_run=not confirm, wait_seconds=wait_seconds)
        kind = "릴스"
    elif image_url:
        res = publish_instagram_image(cfg, ic, image_url, caption,
                                      dry_run=not confirm, wait_seconds=min(wait_seconds, 120))
        kind = "피드"
    else:
        send_telegram(cfg, "📸 [인스타 발행] video_url 또는 image_url 이 필요합니다(공개 URL).")
        return {"ok": False, "error": "url 없음"}
    res.update({"kind": kind, "source": source})
    lines = ["📸 [인스타 %s %s (%s)]" % (kind, "발행" if res.get("ok") and not res.get("dry_run") else "결과", source)]
    if res.get("dry_run"):
        lines.append("• 드라이런(검증만) — 실제 발행하려면 confirm=true")
    if res.get("media_id"):
        lines.append("• media_id: %s" % res.get("media_id"))
    if res.get("error") or res.get("skipped"):
        lines.append("• 사유: %s" % (res.get("error") or res.get("skipped")))
    send_telegram(cfg, "\n".join(lines))
    if res.get("ok") and not res.get("dry_run") and isinstance(state, dict):
        state["last_ig_publish"] = datetime.now(KST).isoformat(timespec="seconds")
    _report_pc_result(cfg, "instagram_publish", res)
    return res


def _maybe_cross_post_instagram(cfg: dict, args: dict, result: dict,
                                source: str = "원격") -> dict | None:
    """쇼츠 생성 뒤 인스타 릴스 교차 발행(플래그·설정·공개 URL 이 모두 있을 때만)."""
    flag = str(args.get("instagram") or "").strip().lower()
    if flag not in ("1", "true", "yes", "y", "on"):
        return None
    ic = load_instagram_config()
    ready, why = _ig_ready(ic)
    if not ready:
        log("인스타 교차 발행 생략: %s" % why)
        return {"ok": False, "skipped": why}
    video_url = str(args.get("ig_video_url") or args.get("video_url") or "").strip()
    if not video_url:
        log("인스타 교차 발행 생략: 공개 영상 URL(args.ig_video_url) 없음")
        return {"ok": False, "skipped": "공개 영상 URL 없음"}
    caption = str(args.get("ig_caption") or args.get("caption") or "").strip()
    confirm = str(args.get("ig_confirm") or "").strip().lower() in ("1", "true", "yes", "y", "on")
    return publish_instagram_reel(cfg, ic, video_url, caption, dry_run=not confirm)


# ---------------------------------------------------------------- 자기 업데이트 (GitHub → 집 PC 파일 교체)
GITHUB_REPO = "cbseng18-glitch/sy-dashboard"
GITHUB_BRANCH = "main"


def _safe_rel_path(local, base: Path = None):
    """BASE_DIR 안쪽 상대 경로만 허용(절대경로·드라이브·.. 탈출 차단). 위반 시 None."""
    base = Path(base or BASE_DIR)
    raw = str(local or "").strip().replace("\\", "/")
    if not raw or "\x00" in raw:
        return None
    if re.match(r"^[A-Za-z]:", raw) or raw.startswith("/") or raw.startswith("~"):
        return None
    try:
        base_r = base.resolve()
        target = (base / raw).resolve()
        target.relative_to(base_r)      # BASE_DIR 밖이면 ValueError
    except Exception:
        return None
    if target == base_r:
        return None
    return target


def _github_fetch(remote: str, token: str = ""):
    """GitHub contents API → (bytes, 오류문자열). 실패하면 raw.githubusercontent 재시도."""
    rel = str(remote or "").strip().lstrip("/")
    if not rel:
        return None, "remote 경로 없음"
    quoted = "/".join(urllib.parse.quote(seg) for seg in rel.split("/") if seg)
    errs = []
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{quoted}?ref={GITHUB_BRANCH}"
        headers = {"User-Agent": "SY-PC-Watch/1.0", "Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
            res = json.loads(r.read().decode("utf-8", "replace"))
        if isinstance(res, dict) and res.get("content"):
            raw = base64.b64decode(res["content"])
            if raw:
                return raw, ""
        errs.append("contents API 응답 비어 있음")
    except Exception as e:
        errs.append(f"contents API {type(e).__name__}: {e}")
    try:
        url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{quoted}"
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "SY-PC-Watch/1.0"}), timeout=20) as r:
            raw = r.read()
        if raw:
            return raw, ""
        errs.append("raw 응답 비어 있음")
    except Exception as e:
        errs.append(f"raw {type(e).__name__}: {e}")
    return None, " / ".join(errs)


def _check_python_source(raw: bytes, name: str) -> str:
    """py_compile 문법 검사 → 오류 문자열(통과하면 빈 문자열)."""
    tmpd = Path(tempfile.mkdtemp(prefix="selfupd_"))
    try:
        src = tmpd / (Path(name).name or "update.py")
        src.write_bytes(raw)
        py_compile.compile(str(src), cfile=str(tmpd / "check.pyc"), doraise=True)
        return ""
    except py_compile.PyCompileError as e:
        msg = str(e).strip().splitlines()
        return "py_compile 실패: " + (msg[-1][:200] if msg else "원인 미상")
    except Exception as e:
        return f"검사 오류 {type(e).__name__}: {e}"
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def self_update(cfg: dict, state: dict, args: dict, source: str = "원격") -> dict:
    """GitHub에서 파일을 내려받아 검사 후 교체하고 필요하면 프로세스를 스스로 재시작한다."""
    args = args or {}
    files = args.get("files")
    if files is None:
        files = args.get("file") or args.get("paths") or ["sy_auto_watch.py"]
    if isinstance(files, str):
        files = [files]
    if not isinstance(files, list) or not files:
        files = ["sy_auto_watch.py"]

    raw_restart = args.get("restart")
    if isinstance(raw_restart, str):
        restart = raw_restart.strip().lower() in ("1", "true", "yes", "y", "on")
    elif raw_restart is None:
        restart = True
    else:
        restart = bool(raw_restart)

    token = _get_github_token(cfg)
    running = Path(__file__).resolve()
    done, failed = [], []

    for item in files[:5]:
        if isinstance(item, str):
            remote, local = item, item
        elif isinstance(item, dict):
            remote = str(item.get("remote") or item.get("path") or "").strip()
            local = str(item.get("local") or Path(remote).name).strip()
        else:
            failed.append("잘못된 항목(문자열/객체 아님)")
            continue
        remote = remote.replace("\\", "/").lstrip("/")
        if not remote:
            failed.append(f"{local or '?'} → remote 경로 없음")
            continue
        target = _safe_rel_path(local)
        if target is None:
            failed.append(f"{local} → 허용되지 않는 경로(BASE_DIR 밖·절대경로·.. 차단)")
            log(f"자기 업데이트 경로 거부: {local}")
            continue

        raw, ferr = _github_fetch(remote, token)
        if raw is None:
            failed.append(f"{local} → 내려받기 실패({remote}): {ferr}")
            continue

        suf = target.suffix.lower()
        if suf == ".py":
            cerr = _check_python_source(raw, target.name)
            if cerr:
                failed.append(f"{local} → {cerr} (기존 파일 유지)")
                log(f"자기 업데이트 문법 검사 실패({local}): {cerr}")
                continue
            if target.name == running.name and (b"def main(" not in raw or b"handle_action" not in raw):
                failed.append(f"{local} → main/handle_action 누락 — 자기 자신 교체 취소")
                log(f"자기 업데이트 필수 함수 없음({local}) — 교체 취소")
                continue
        elif suf == ".json":
            try:
                json.loads(raw.decode("utf-8"))
            except Exception as e:
                failed.append(f"{local} → JSON 검사 실패: {e} (기존 파일 유지)")
                continue

        try:
            if target.exists():
                shutil.copyfile(target, target.with_suffix(target.suffix + ".bak"))
            tmp = target.with_suffix(target.suffix + ".new")
            tmp.write_bytes(raw)
            os.replace(tmp, target)
            done.append(f"{local} ({len(raw)}B)")
            log(f"자기 업데이트 적용: {local} ({len(raw)}B)")
        except Exception as e:
            failed.append(f"{local} → 저장 실패 {type(e).__name__}: {e}")
            try:
                target.with_suffix(target.suffix + ".new").unlink()
            except Exception:
                pass

    will_restart = bool(restart and done and not failed)
    lines = [f"♻️ [자기 업데이트 ({source})]"]
    lines.append("• 성공: " + (", ".join(done) if done else "없음"))
    if failed:
        lines.append("• 실패: " + "\n• 실패: ".join(failed[:5]))
    if will_restart:
        lines.append("• 재시작: 감시 루프를 새 코드로 다시 시작합니다")
    elif restart and done and failed:
        lines.append("• 재시작 보류: 실패 항목이 있어 기존 코드로 계속 동작")
    msg = "\n".join(lines)
    log(msg)
    send_telegram(cfg, msg)

    result = {"ok": bool(done), "updated": done, "failed": failed, "restarted": False}
    _report_pc_result(cfg, "self_update", {"ok": bool(done), "updated": done,
                                           "failed": failed, "restart_planned": will_restart})
    if will_restart:
        try:
            if state is not None:
                save_state(state)      # 재시작 후 같은 원격 명령을 다시 먹지 않게
        except Exception as e:
            log(f"재시작 전 상태 저장 실패: {e}")
        log("자기 업데이트 완료 → 프로세스 재시작")
        try:
            os.execv(sys.executable, [sys.executable, str(running)])
        except Exception as e:
            log(f"재시작 실패: {type(e).__name__}: {e}")
            send_telegram(cfg, f"♻️ 파일은 새로 저장했지만 자동 재시작 실패: {type(e).__name__}: {e}\n"
                               "집 PC에서 실행하기.bat 을 한 번 다시 켜 주세요.")
            return result
        result["restarted"] = True
    return result


def _load_deal_verifier():
    """deal_verifier.py 를 지연 import (없거나 깨져도 핫딜 알림은 그대로 나간다)."""
    try:
        here = str(BASE_DIR)
        if here not in sys.path:
            sys.path.insert(0, here)
        import deal_verifier
        return deal_verifier
    except Exception as e:
        log(f"deal_verifier 로드 실패(생략): {e}")
        return None


def verify_deal_line(title: str) -> str:
    """핫딜 1건 시중 최저가 비교 한 줄. 실패하면 빈 문자열."""
    dv = _load_deal_verifier()
    if dv is None:
        return ""
    try:
        r = dv.verify(title)
        return dv.format_line(r)
    except Exception as e:
        log(f"가격 검증 오류({type(e).__name__}): {title[:40]}")
        return ""


def check_deals(cfg: dict, state: dict, force_report: bool = False) -> None:
    req = urllib.request.Request(
        PPOM_URL,
        headers={"User-Agent": "PersonalDealCheck/1.0", "Accept-Encoding": "identity"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read(524288)
    root = ET.fromstring(raw)
    items = root.findall(".//item")

    seen = set(state.get("seen_deals", []))
    state.setdefault("seen_deals", [])
    first_run = len(seen) == 0
    new_hits = []

    for it in items:
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link or link in seen:
            continue
        seen.add(link)
        state["seen_deals"].append(link)
        if first_run:
            continue
        cats = [c for c, pat in DEAL_KEYWORDS if re.search(pat, title, re.IGNORECASE)]
        if cats:
            new_hits.append((cats, title, link))

    state["seen_deals"] = state["seen_deals"][-400:]
    state["last_deal_check"] = time.time()
    if first_run:
        log(f"핫딜 초기 시드 {len(state['seen_deals'])}건 저장 완료 (다음 회차부터 신규 알림)")
    elif new_hits:
        want_verify = bool(cfg.get("deal_verify", True))
        for cats, title, link in new_hits:
            vline = verify_deal_line(title) if want_verify else ""
            msg = f"🔥 [핫딜 · {'/'.join(cats)}]\n{title}"
            if vline:
                msg += f"\n{vline}"
            msg += f"\n{link}"
            log(msg)
            send_telegram(cfg, msg)
    else:
        log("핫딜 신규 매칭 0건")
        if force_report:
            send_telegram(cfg, "🔥 [핫딜 즉시 점검] 신규 매칭 0건")


def check_daiso(cfg: dict, state: dict, force_report: bool = False) -> None:
    items = cfg.get("daiso_items") or DEFAULT_DAISO_ITEMS
    prev = state.get("daiso_stock", {})
    changes = []
    in_stock_cnt = 0

    for pd_no, name in items.items():
        try:
            body = json.dumps({"pdNo": str(pd_no)}).encode("utf-8")
            req = urllib.request.Request(
                DAISO_DETAIL_URL,
                data=body,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                res = json.loads(r.read().decode("utf-8", "replace"))
            qty = int((res.get("data") or {}).get("stckQy") or 0)
            if qty > 0:
                in_stock_cnt += 1
            old_qty = prev.get(pd_no)
            if old_qty is not None:
                if old_qty == 0 and qty > 0:
                    changes.append(f"🟢 재입고: {name} ({pd_no}) → {qty}개")
                elif old_qty > 0 and qty == 0:
                    changes.append(f"🔴 품절: {name} ({pd_no}) → 0개")
            prev[pd_no] = qty
            time.sleep(0.15)
        except Exception as e:
            log(f"다이소 조회 오류 ({pd_no}): {e}")

    state["daiso_stock"] = prev
    state["last_daiso_check"] = time.time()
    if changes:
        msg = "📦 [다이소 온라인 재고 변동]\n" + "\n".join(changes)
        log(msg)
        send_telegram(cfg, msg)
    else:
        log(f"다이소 온라인 {len(items)}종 변동 없음 (재고보유 {in_stock_cnt}종)")
        if force_report:
            send_telegram(cfg, f"📦 [다이소 온라인 즉시 점검] {len(items)}종 중 {in_stock_cnt}종 재고 있음 (변동 없음)")


def _ensure_cryptography():
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        return Cipher, algorithms, modes
    except ImportError:
        log("cryptography 모듈 설치 시도 중...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "cryptography"], check=False)
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        return Cipher, algorithms, modes


def check_daiso_full_offline(cfg: dict, state: dict, force_report: bool = False) -> None:
    items = cfg.get("daiso_items") or DEFAULT_DAISO_ITEMS
    try:
        Cipher, algorithms, modes = _ensure_cryptography()
    except Exception as e:
        log(f"오프라인 10km 전수 검사 생략 (cryptography 없음): {e}")
        return

    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/157.0.0.0 Safari/537.36"

    def call(url, data=None, headers=None):
        h = {"User-Agent": ua, "Accept": "application/json", "Accept-Language": "ko-KR,ko;q=0.9"}
        if data is not None:
            h["Content-Type"] = "application/json"
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
        with op.open(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")

    def bearer(token):
        iv = bytes(random.getrandbits(8) for _ in range(16))
        pad = 16 - (len(token.encode()) % 16)
        raw = token.encode() + bytes([pad]) * pad
        enc = Cipher(algorithms.AES(b"PRE_AUTH_ENC_KEY"), modes.CBC(iv)).encryptor()
        ct = enc.update(raw) + enc.finalize()
        b = lambda x: base64.b64encode(x).decode()
        return b(iv) + b(ct)

    online = state.get("daiso_stock", {})
    _, hdr, token = call(DAISO_BASE + "/auth/request")
    token = token.strip()
    uid = hdr.get("x-dm-uid") or hdr.get("X-DM-UID") or ""

    pairs = [[pd, s] for s in DAISO_STORES_10KM for pd in items.keys()]
    offline_sum = {pd: 0 for pd in items.keys()}
    for i in range(0, len(pairs), 120):
        chunk = pairs[i:i+120]
        body = json.dumps([{"pdNo": p, "strCd": s} for p, s in chunk]).encode()
        try:
            _, _, raw = call(DAISO_BASE + "/pd/pdh/selStrPkupStck", data=body,
                             headers={"Authorization": "Bearer " + bearer(token), "X-DM-UID": uid})
            res = json.loads(raw)
            if res.get("success"):
                for r in (res.get("data") or []):
                    offline_sum[str(r.get("pdNo"))] = offline_sum.get(str(r.get("pdNo")), 0) + int(r.get("stck") or 0)
        except Exception:
            pass
        time.sleep(0.1)

    newly_out = []
    restocked = []
    for pd, nm in items.items():
        on_q = online.get(pd, -1)
        off_q = offline_sum.get(pd, 0)
        if pd in ZEROED_ITEMS:
            if off_q >= 5:
                restocked.append(f"🟢 재입고: {nm}({pd}) 온라인 {on_q} / 10km매장 {off_q}개")
        else:
            if on_q == 0 and off_q == 0:
                newly_out.append(f"🔴 완전품절: {nm}({pd}) 온라인 0 / 10km매장 0개")

    state["last_full_offline_date"] = datetime.now().strftime("%Y-%m-%d")
    if newly_out or restocked:
        lines = newly_out + restocked
        send_telegram(cfg, f"📦 [다이소 57종 온+오프 10km 전수 점검]\n" + "\n".join(lines[:20]))
    else:
        log(f"다이소 57종 온+오프 10km 전수 점검 이상 없음")
        if force_report:
            send_telegram(cfg, f"📦 [다이소 57종 온+오프 10km 전수 점검] 69개 매장 확인 완료 — 신규 품절/재입고 없음")


# ---------------------------------------------------------------- 큐텐 1~5호 다이소 최우선 점검 (09:30)
# 57종 전체 점검과 별도로, 큐텐 구성품 품번만 먼저 훑어 완전품절/재고 50개 미만이면 단독 경보.
def _daiso_online_qty(pd_no: str):
    """다이소몰 상품 상세 API → (재고수량, 오류문자열). 실패하면 (None, 사유)."""
    try:
        body = json.dumps({"pdNo": str(pd_no)}).encode("utf-8")
        req = urllib.request.Request(
            DAISO_DETAIL_URL,
            data=body,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            res = json.loads(r.read().decode("utf-8", "replace"))
        return int((res.get("data") or {}).get("stckQy") or 0), ""
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _qoo10_alert_count(state: dict, cfg: dict) -> int:
    """현재 상태 기준 큐텐 1~5호 위험(완전품절·재고 50개 미만) 품번 수."""
    threshold = int(cfg.get("qoo10_daiso_min_qty") or QOO10_DAISO_MIN_QTY)
    n = 0
    for q in (state.get("qoo10_daiso_qty") or {}).values():
        if isinstance(q, int) and q < threshold:
            n += 1
    return n


def check_qoo10_daiso(cfg: dict, state: dict, force_report: bool = False) -> dict:
    """큐텐 재팬 1~5호 다이소 구성품만 먼저 점검 → 완전품절·재고 50개 미만 단독 경보.

    force_report=True(매일 09:30 회차·/큐텐재고 명령)면 상태가 그대로여도 1회 요약을 보낸다.
    """
    threshold = int(cfg.get("qoo10_daiso_min_qty") or QOO10_DAISO_MIN_QTY)
    prev = state.get("qoo10_daiso_qty")
    if not isinstance(prev, dict):
        prev = {}
    qty_now, errors = {}, []
    danger, low, recovered = [], [], []

    for code, (set_no, name) in QOO10_DAISO_ITEMS.items():
        qty, err = _daiso_online_qty(code)
        if qty is None:
            errors.append(f"{set_no} {name}({code}) 조회 실패: {err}")
            continue
        qty_now[code] = qty
        if qty <= 0:
            danger.append((code, set_no, name, qty))
        elif qty < threshold:
            low.append((code, set_no, name, qty))
        old = prev.get(code)
        if isinstance(old, int) and old <= 0 and qty > 0:
            recovered.append((code, set_no, name, qty))
        time.sleep(0.15)

    state["qoo10_daiso_qty"] = qty_now
    state["last_qoo10_daiso_check"] = time.time()

    problems = danger + low
    sig = ";".join(f"{c}:{'out' if q <= 0 else 'low'}" for c, s, n, q in sorted(problems))
    log("큐텐 1~5호 다이소 점검: %d/%d종 · 완전품절 %d · %d개 미만 %d · 회복 %d · 조회실패 %d" % (
        len(qty_now), len(QOO10_DAISO_ITEMS), len(danger), threshold, len(low),
        len(recovered), len(errors)))

    if not problems:
        state["qoo10_daiso_alert_sig"] = ""
        if recovered:
            lines = [f"🟢 [큐텐 1~5호 재입고] {len(recovered)}종"]
            for code, set_no, name, qty in recovered:
                lines.append(f"• {set_no} {name} ({code}) → {qty:,}개")
            send_telegram(cfg, "\n".join(lines))
        if force_report:
            send_telegram(cfg, "🟠 [큐텐 1~5호 다이소 최우선 점검] %d종 모두 재고 %,d개 이상 — 경보 없음"
                               % (len(qty_now), threshold))
        return {"ok": True, "checked": len(qty_now), "danger": 0, "low": 0}

    if not force_report and sig == str(state.get("qoo10_daiso_alert_sig") or ""):
        log("큐텐 1~5호 경보 상태 변동 없음 → 텔레그램 생략")
        return {"ok": True, "checked": len(qty_now), "danger": len(danger), "low": len(low), "silent": True}

    state["qoo10_daiso_alert_sig"] = sig
    head = "🚨 [큐텐 1~5호 다이소 품절 경보]"
    if force_report:
        now_hm = datetime.now().strftime("%H:%M")
        head += (" 09:30 최우선 점검" if now_hm == "09:30" else f" 최우선 점검({now_hm})")
    lines = [head]
    if danger:
        lines.append(f"■ 완전품절 {len(danger)}종")
        for code, set_no, name, qty in danger:
            lines.append(f"• {set_no} {name} ({code}) → 0개")
    if low:
        lines.append(f"■ 재고 {threshold:,}개 미만 {len(low)}종")
        for code, set_no, name, qty in low:
            lines.append(f"• {set_no} {name} ({code}) → {qty:,}개")
    lines.append("• 큐텐 상품 재고는 5개뿐입니다. 매장 확보·대체 품번 검토가 필요합니다")
    if errors:
        lines.append(f"• 조회 실패 {len(errors)}종: " + errors[0][:80])
    msg = "\n".join(lines)
    log(msg.replace("\n", " | "))
    send_telegram(cfg, msg)
    return {"ok": True, "checked": len(qty_now), "danger": len(danger), "low": len(low)}


# ---------------------------------------------------------------- 네이버 카페 인기글 (비로그인)
# 2026-11-28 지시: '정가거부'·'월급쟁이 재테크 연구(월재연)' 최신글 중 인기글 Top 5.
# ★ 원칙 — 쿠키·로그인 일절 사용 금지. 공개 목록 API(ArticleListV2.json)만 호출한다.
#   실측(2026-11-28): 둘 다 비로그인 HTTP 200, 제목/조회(readCount)/댓글(commentCount)/
#   추천(likeItCount)/작성시각 정상. menuid=0 은 500 오류 → 실제 게시판 menuid 필수.
#   search.queryType 은 LAST 만 동작(다른 값은 조용히 무시). 인기글 전용 API 는 없음
#   → 여러 게시판 최신글을 모아 조회·댓글·추천으로 자체 랭킹(최근 글 가중).
CAFE_API = "https://apis.naver.com/cafe-web/cafe2/ArticleListV2.json"
CAFE_REMOTE_PATH = "pc_cafe_hot.json"
CAFE_SLOTS = ((8, 30), (14, 30))     # 매일 08:30·14:30 KST 자동 수집
CAFE_TOP_N = 5
CAFE_MAX_AGE_HOURS = 72              # 최근 3일 글만 인기글 후보로
CAFE_PAGE_SIZE = 50                  # 게시판당 최신 50건
CAFE_FETCH_MAX = 10                  # 카페당 최대 게시판 수(과호출 방지)

CAFE_TARGETS = [
    {
        "name": "정가거부",
        "full_name": "정가를 거부하는 사람들 (알뜰폰·절약·재테크)",
        "clubid": 30786704,
        "slug": "wjdrkrjqn",
        "menus": [(1, "자유"), (33, "짠테크"), (21, "쇼핑핫딜"), (9, "이벤트"),
                  (49, "꿀팁"), (52, "명예의전당"), (84, "퀴즈정답"),
                  (20, "카드"), (75, "편의점")],
    },
    {
        "name": "월재연",
        "full_name": "월급쟁이 재테크 연구 (예금·적금·절약·보험·주식·ETF·부동산)",
        "clubid": 22290117,
        "slug": "onepieceholicplus",
        "menus": [(146, "와글 자유"), (107, "재테크 Q&A"), (550, "절약 BEST"),
                  (288, "재테크 BEST"), (489, "선착 핫딜"), (314, "주식 수다"),
                  (122, "ETF 수다"), (615, "유머")],
    },
]


def _cafe_clean_text(text) -> str:
    """제목 정리 — 줄바꿈/연속공백 압축 + 주요 HTML 엔티티 복원(텔레그램·JSON 공통)."""
    t = str(text or "")
    for a, b in (("&quot;", '"'), ("&apos;", "'"), ("&#39;", "'"), ("&lt;", "<"),
                 ("&gt;", ">"), ("&nbsp;", " "), ("&amp;", "&")):
        t = t.replace(a, b)
    t = re.sub(r"<[^>]{1,40}>", "", t)          # 말머리 태그 잔여물 제거
    return re.sub(r"\s+", " ", t).strip()


def _cafe_fetch_menu(clubid: int, menuid: int, page: int = 1, per: int = CAFE_PAGE_SIZE):
    """게시판 최신글 목록 1페이지 — (글 리스트, 오류문자열). 비로그인 공개 API."""
    params = urllib.parse.urlencode({
        "search.clubid": int(clubid),
        "search.menuid": int(menuid),
        "search.page": int(page),
        "search.perPage": int(per),
        "search.boardtype": "L",
        "search.queryType": "LAST",
    })
    req = urllib.request.Request(
        CAFE_API + "?" + params,
        headers={
            "User-Agent": _BROWSER_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Referer": f"https://cafe.naver.com/{clubid}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8", "replace")
        js = json.loads(raw)
        msg = js.get("message") or {}
        if str(msg.get("status")) != "200":
            err = ((msg.get("error") or {}).get("msg") or "상태 이상")
            return [], f"menu {menuid}: {err}"
        arts = (msg.get("result") or {}).get("articleList") or []
        return [a for a in arts if isinstance(a, dict)], ""
    except Exception as e:
        return [], f"menu {menuid}: {type(e).__name__}: {e}"


def _cafe_score(art: dict, now_ms: float) -> float:
    """인기 점수 = 조회 + 댓글*10 + 추천*5, 최근 글일수록 가중(36시간 주기 감쇠)."""
    age_h = max(0.0, (now_ms - float(art.get("writeDateTimestamp") or 0)) / 3600000.0)
    base = (int(art.get("readCount") or 0)
            + int(art.get("commentCount") or 0) * 10
            + int(art.get("likeItCount") or 0) * 5)
    return base / ((1.0 + age_h / 36.0) ** 1.15), age_h


def collect_cafe_hot(target: dict, top_n: int = CAFE_TOP_N) -> dict:
    """카페 1곳 → 게시판별 최신글 병합·중복제거 후 인기 Top N. (쿠키 없음)"""
    now_ms = time.time() * 1000.0
    seen_ids, pool, errors, scanned = set(), [], [], 0
    for menuid, mlabel in list(target.get("menus") or [])[:CAFE_FETCH_MAX]:
        arts, err = _cafe_fetch_menu(target["clubid"], menuid)
        if err:
            errors.append(err)
            continue
        scanned += len(arts)
        for a in arts:
            aid = int(a.get("articleId") or 0)
            if not aid or aid in seen_ids:
                continue
            seen_ids.add(aid)
            pool.append(a)
        time.sleep(0.25)        # 예의상 간격(과호출 방지)

    # 같은 제목 중복(게시판 교차 게시) → 높은 점수 1건만
    best = {}
    for a in pool:
        if a.get("blindArticle"):
            continue
        title = _cafe_clean_text(a.get("subject"))
        if not title:
            continue
        score, age_h = _cafe_score(a, now_ms)
        if age_h > CAFE_MAX_AGE_HOURS:
            continue
        key = re.sub(r"[\s\W]+", "", title.lower())[:80]
        if key and (key not in best or score > best[key][0]):
            best[key] = (score, age_h, a, title)

    ranked = sorted(best.values(), key=lambda x: -x[0])[:max(1, int(top_n))]
    rows = []
    for i, (score, age_h, a, title) in enumerate(ranked, 1):
        rows.append({
            "rank": i,
            "title": title,
            "url": f"https://cafe.naver.com/{target['slug']}/{int(a['articleId'])}",
            "article_id": int(a["articleId"]),
            "board": _cafe_clean_text(a.get("menuName")),
            "read": int(a.get("readCount") or 0),
            "comment": int(a.get("commentCount") or 0),
            "like": int(a.get("likeItCount") or 0),
            "age_hours": round(age_h, 1),
            "written_at_kst": datetime.fromtimestamp(
                float(a.get("writeDateTimestamp") or 0) / 1000.0).strftime("%m-%d %H:%M"),
        })
    return {
        "name": target["name"],
        "full_name": target.get("full_name", ""),
        "clubid": int(target["clubid"]),
        "slug": target["slug"],
        "menu_count": len(target.get("menus") or []),
        "scanned": scanned,
        "candidates": len(best),
        "ok": bool(rows),
        "top": rows,
        "errors": errors[:4],
    }


def _cafe_msg(results: list, stamp: str, res: dict = None, source: str = "자동") -> str:
    lines = [f"☕ [네이버 카페 인기글 ({source})] {stamp} KST · 최근 {CAFE_MAX_AGE_HOURS}시간 상위 {CAFE_TOP_N}"]
    for r in results:
        if not r.get("ok"):
            lines.append(f"■ {r.get('name')} — 수집 실패: {(r.get('errors') or ['원인 미상'])[0][:90]}")
            continue
        lines.append(f"■ {r['name']} · 최신 {r['scanned']}건 중")
        for row in r["top"]:
            lines.append(f"{row['rank']}. {row['title'][:70]}")
            lines.append(f"   조회 {row['read']:,} · 댓글 {row['comment']} · 추천 {row['like']} "
                         f"· {row['age_hours']:.0f}시간 전")
            lines.append(f"   {row['url']}")
    if isinstance(res, dict):
        if res.get("ok"):
            lines.append(f"📄 {CAFE_REMOTE_PATH} 저장 (커밋 {res.get('commit') or res.get('sha') or '-'})")
        else:
            lines.append(f"📄 {CAFE_REMOTE_PATH} 저장 실패: {str(res.get('detail'))[:100]}")
    return "\n".join(lines)


def check_cafe_hot(cfg: dict, state: dict, force_report: bool = False, source: str = "자동") -> dict:
    """두 카페 인기글 Top5 수집 → 텔레그램 발송 + GitHub pc_cafe_hot.json 저장."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    results = []
    for target in CAFE_TARGETS:
        try:
            results.append(collect_cafe_hot(target))
        except Exception as e:      # 한 카페가 깨져도 다른 카페·본 루프는 계속
            log(f"[카페 인기글] {target.get('name')} 수집 오류: {type(e).__name__}: {e}")
            results.append({"name": target.get("name"), "ok": False, "top": [],
                            "errors": [f"{type(e).__name__}: {e}"], "scanned": 0})

    payload = {
        "cafe_hot": {
            "filled_at_kst": stamp,
            "window_hours": CAFE_MAX_AGE_HOURS,
            "top_n": CAFE_TOP_N,
            "auto_slots_kst": [f"{h:02d}:{m:02d}" for h, m in CAFE_SLOTS],
            "login_used": False,
            "api": "apis.naver.com/cafe-web/cafe2/ArticleListV2.json",
            "cafes": results,
        }
    }
    res = {"ok": False, "detail": "GitHub 토큰 없음"}
    token = _get_github_token(cfg)
    if token:
        try:
            res = _github_put_json(CAFE_REMOTE_PATH, payload,
                                   f"cafe: 인기글 Top{CAFE_TOP_N} ({stamp} KST)", token)
        except Exception as e:
            res = {"ok": False, "detail": f"{type(e).__name__}: {e}"}
        if not res.get("ok"):
            log(f"[카페 인기글] GitHub 저장 실패: {res.get('detail')}")
    else:
        log("[카페 인기글] GitHub 토큰 없음 — JSON 저장 생략")

    if any(r.get("ok") for r in results) or force_report:
        send_telegram(cfg, _cafe_msg(results, stamp, res, source=source))
    else:
        log("[카페 인기글] 두 카페 모두 수집 실패 — 발송 생략(로그만)")
    try:
        state["last_cafe_check"] = time.time()
        state["last_cafe_top"] = {r.get("name"): [row.get("article_id") for row in r.get("top") or []]
                                  for r in results}
    except Exception:
        pass
    return {"ok": any(r.get("ok") for r in results), "cafes": results, "push": res}


# ---------------------------------------------------------------- 대시보드 수치 직접 푸시
# 2026-11-28 지시: 집 PC가 모은 수치를 sy-dashboard 에 직접 올린다.
# 절대 원칙 — index.html·전략·메모 영역은 건드리지 않는다. pc_stats.json 의 "dashboard" 키만
#   GET(sha) → 우리 키만 덮어쓰기 → PUT. SHA 충돌(409/422)이면 한 번 다시 읽어 재시도.
DASH_LOCAL_FILE = BASE_DIR / "pc_dashboard.json"
DASH_REMOTE_PATH = "pc_stats.json"
DASH_PUSH_MIN_GAP = 6 * 3600        # 내용이 같으면 6시간에 1회만 커밋(커밋 폭주 방지)


def _read_json_file(path, default=None):
    """로컬 JSON 읽기 — 없거나 깨졌으면 default."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _first_json(paths):
    """여러 후보 중 처음으로 쓸 만한 dict."""
    for p in paths:
        d = _read_json_file(p)
        if isinstance(d, dict) and d:
            return d
    return {}


def _pc_shopee_block(stats: dict) -> dict:
    shopee = (stats.get("shopee") or {}) if isinstance(stats, dict) else {}
    regions = []
    for r in (shopee.get("regions") or []):
        if not isinstance(r, dict):
            continue
        m = r.get("metrics") or {}
        regions.append({
            "region": r.get("region"),
            "ok": bool(r.get("ok")),
            "uv": int(m.get("uv") or 0),
            "pv": int(m.get("pv") or 0),
            "clicks": int(m.get("product_clicks") or 0),
            "orders": int(m.get("orders") or 0),
            "units": int(m.get("units") or 0),
            "sales": m.get("sales") or 0,
        })
    t = shopee.get("totals") or {}
    return {
        "source_at": stats.get("generated_at") or stats.get("merged_at") or "",
        "cookie_state": shopee.get("cookie_state") or "",
        "regions_ok": int(t.get("regions_ok") or len([x for x in regions if x["ok"]])),
        "regions_total": int(t.get("regions_total") or len(regions)),
        "uv": int(t.get("uv") or sum(x["uv"] for x in regions)),
        "pv": int(t.get("pv") or sum(x["pv"] for x in regions)),
        "clicks": int(t.get("product_clicks") or sum(x["clicks"] for x in regions)),
        "orders": int(t.get("orders") or sum(x["orders"] for x in regions)),
        "units": int(t.get("units") or sum(x["units"] for x in regions)),
        "sales": t.get("sales_sum") or 0,
        "regions": regions,
    }


def build_dashboard_payload(cfg: dict, state: dict) -> dict:
    """집 PC가 모은 수치를 대시보드 바인딩용 JSON 한 덩어리로 만든다(개인정보·토큰 미포함)."""
    stats = _first_json([BASE_DIR / "pc_stats.json", STATS_STATE_FILE])
    boost = _first_json([BASE_DIR / "pc_boost.json", BOOST_LAST_FILE])
    survey = _read_json_file(BASE_DIR / "pc_survey_candidates.json", {}) or {}
    toss = _read_json_file(BASE_DIR / "toss_stats.json", {}) or {}
    yt = _read_json_file(BASE_DIR / "pc_youtube.json", {}) or {}
    blog = _read_json_file(BLOG_STATS_FILE, {}) or {}
    th = _read_json_file(THREADS_STATS_FILE, {}) or {}
    threshold = int(cfg.get("qoo10_daiso_min_qty", QOO10_DAISO_MIN_QTY) or QOO10_DAISO_MIN_QTY)

    qty = state.get("qoo10_daiso_qty") or {}
    danger, low = [], []
    for code, raw_q in qty.items():
        info = QOO10_DAISO_ITEMS.get(code)
        if not info:
            continue
        try:
            n = int(raw_q)
        except Exception:
            continue
        row = {"code": code, "set": info[0], "name": info[1], "qty": n}
        if n <= 0:
            danger.append(row)
        elif n < threshold:
            low.append(row)
    danger.sort(key=lambda x: (x["set"], x["code"]))
    low.sort(key=lambda x: x["qty"])

    stock = state.get("daiso_stock") or {}
    in_stock = sum(1 for v in stock.values() if isinstance(v, int) and v > 0)

    bt = boost.get("totals") or {}
    next_free = ""
    for r in (boost.get("regions") or []):
        nf = ((r.get("slots") or {}).get("next_free_at") if isinstance(r, dict) else "") or ""
        if nf and str(nf) > next_free:
            next_free = str(nf)

    sc = survey.get("counts") or {}
    now = datetime.now(KST)
    payload = {
        "schema": "pc_dashboard/1",
        "generator": "sy_auto_watch.py",
        "filled_at": now.isoformat(timespec="seconds"),
        "filled_at_kst": now.strftime("%m-%d %H:%M"),
        "shopee": _pc_shopee_block(stats),        "boost": {
            "source_at": boost.get("generated_at") or "",
            "regions_ok": int(bt.get("regions_ok") or 0),
            "regions_total": int(bt.get("regions_total") or 0),
            "boosted": int(bt.get("boosted") or 0),
            "planned": int(bt.get("planned") or 0),
            "free_slots": int(bt.get("free_slots") or 0),
            "failed": int(bt.get("failed") or 0),
            "cost_krw": 0,
            "next_free_at": next_free,
        },
        "survey": {
            "source_at": survey.get("generated_at") or "",
            "new_candidates": int(sc.get("new_candidates") or 0),
            "candidates": int(sc.get("candidate") or 0),
            "review": int(sc.get("review") or 0),
            "duplicates": int(sc.get("duplicates") or 0),
            "candidates_total": len(survey.get("candidates") or []),
            "sources_ok": int(sc.get("sources_ok") or 0),
            "sources_failed": int(sc.get("sources_failed") or 0),
        },
        "qoo10": {
            "threshold": threshold,
            "checked_at": (datetime.fromtimestamp(float(state.get("last_qoo10_daiso_check") or 0), KST)
                           .strftime("%m-%d %H:%M") if state.get("last_qoo10_daiso_check") else ""),
            "checked": len(qty),
            "alert_count": len(danger) + len(low),
            "danger": danger,
            "low": low,
        },
        "daiso": {
            "total": len(stock),
            "in_stock": in_stock,
            "offline_date": state.get("last_full_offline_date") or "",
        },
        "toss": ({
            "source_at": toss.get("fetched_at") or "",
            "from": toss.get("from") or "",
            "to": toss.get("to") or "",
            "month_clicks": int(toss.get("clickCount") or 0),
            "month_sold": int(toss.get("soldQuantity") or 0),
            "month_sales_krw": int(toss.get("salesAmount") or 0),
            "expected_krw": int(toss.get("expectedCommissionAmount") or 0),
        } if toss.get("fetched_at") else None),
        "youtube": ({
            "source_at": yt.get("generated_at") or "",
            "videos": int(yt.get("videos") or 0),
            "views": yt.get("views"),
            "subscribers": yt.get("subscribers"),
        } if yt else None),
        "blog": ({
            "source_at": blog.get("fetched_at") or "",
            "post_count": int(blog.get("post_count") or 0),
            "post_count_source": blog.get("post_count_source") or "",
            "feed_items": int(blog.get("feed_items") or 0),
            "posts_today": int(blog.get("posts_today") or 0),
            "latest_title": blog.get("latest_title") or "",
            "latest_link": blog.get("latest_link") or "",
            "latest_pub": blog.get("latest_pub_kst") or "",
            "blog_url": blog.get("blog_url") or "",
        } if blog.get("fetched_at") else None),
        "threads": ({
            "source_at": th.get("fetched_at") or "",
            "followers": th.get("followers"),
            "posts_total": th.get("posts_total"),
            "views_30d": th.get("views_30d"),
            "views_at": th.get("insights_at") or "",
            "profile_source": th.get("profile_source") or "",
            "handle": "@18.1q2w3e4r",
        } if th.get("fetched_at") else None),
    }
    return payload


def _github_get_json(path: str, token: str) -> tuple:
    """공개 저장소의 JSON 1개를 읽어 (dict, sha) 로 돌려준다. 실패는 ({}, "")."""
    api = "https://api.github.com/repos/%s/contents/%s?ref=%s" % (
        GITHUB_REPO, urllib.parse.quote(path), GITHUB_BRANCH)
    try:
        req = urllib.request.Request(api, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "SY-PC-Watch/1.0",
        })
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.loads(r.read().decode("utf-8", "replace"))
        data = json.loads(base64.b64decode(res["content"]).decode("utf-8")) if res.get("content") else {}
        return (data if isinstance(data, dict) else {}), str(res.get("sha") or "")
    except Exception:
        return {}, ""


def _github_put_json(path: str, payload_obj: dict, message: str, token: str) -> dict:
    """지정한 JSON 파일 하나만 GET(sha)→병합→PUT. 다른 파일·index.html 은 건드리지 않는다."""
    api = "https://api.github.com/repos/%s/contents/%s" % (GITHUB_REPO, urllib.parse.quote(path))
    base_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "SY-PC-Watch/1.0",
    }
    for attempt in (0, 1):
        sha, remote = "", {}
        try:
            req = urllib.request.Request(f"{api}?ref={GITHUB_BRANCH}", headers=base_headers)
            with urllib.request.urlopen(req, timeout=20) as r:
                res = json.loads(r.read().decode("utf-8", "replace"))
            sha = str(res.get("sha") or "")
            if res.get("content"):
                try:
                    remote = json.loads(base64.b64decode(res["content"]).decode("utf-8"))
                except Exception:
                    remote = {}
        except urllib.error.HTTPError as e:
            if e.code != 404:
                return {"ok": False, "detail": f"조회 HTTP {e.code}"}
        except Exception as e:
            return {"ok": False, "detail": f"조회 {type(e).__name__}: {e}"}
        if not isinstance(remote, dict):
            remote = {}
        merged = dict(remote)
        merged.update(payload_obj)          # 우리 키만 덮어쓴다(나머지 필드는 그대로 보존)
        body = {
            "message": message,
            "content": base64.b64encode(
                json.dumps(merged, ensure_ascii=False, indent=1).encode("utf-8")).decode("ascii"),
            "branch": GITHUB_BRANCH,
        }
        if sha:
            body["sha"] = sha
        req = urllib.request.Request(
            api, data=json.dumps(body).encode("utf-8"),
            headers={**base_headers, "Content-Type": "application/json"}, method="PUT")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                out = json.loads(r.read().decode("utf-8", "replace"))
            return {"ok": True,
                    "sha": str((out.get("content") or {}).get("sha") or "")[:8],
                    "commit": str((out.get("commit") or {}).get("sha") or "")[:8]}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            if e.code in (409, 422) and attempt == 0:
                log(f"GitHub PUT 충돌({e.code}) → 최신파일 다시 읽고 재시도: {path}")
                time.sleep(1.5)
                continue
            return {"ok": False, "detail": f"PUT HTTP {e.code}: {detail}"}
        except Exception as e:
            return {"ok": False, "detail": f"PUT {type(e).__name__}: {e}"}
    return {"ok": False, "detail": "SHA 충돌 재시도 실패"}


def _dash_msg(payload: dict, res: dict) -> str:
    sh = payload.get("shopee") or {}
    bo = payload.get("boost") or {}
    su = payload.get("survey") or {}
    q10 = payload.get("qoo10") or {}
    lines = [f"📊 [대시보드 수치 푸시] pc_stats.json dashboard 갱신 ({payload.get('filled_at_kst')} KST)"]
    lines.append(f"• 쇼피 {sh.get('regions_ok')}/{sh.get('regions_total')}국 · 방문 {sh.get('uv')} · PV {sh.get('pv')} · 클릭 {sh.get('clicks')} · 주문 {sh.get('orders')}")
    lines.append(f"• 부스트 {bo.get('boosted')}/{bo.get('planned')} · 실패 {bo.get('failed')} · 0원")
    lines.append(f"• 설문 후보 신규 {su.get('new_candidates')} · 중복 {su.get('duplicates')}")
    lines.append(f"• 큐텐 1~5호 위험 {q10.get('alert_count')}종(완전품절 {len(q10.get('danger') or [])})")
    bl = payload.get("blog") or {}
    if bl:
        lines.append(f"• 블로그 {bl.get('post_count')}편(오늘 {bl.get('posts_today')}편) · 최신 {bl.get('latest_pub') or '-'}")
    th = payload.get("threads") or {}
    if th:
        lines.append(f"• 스레드 팔로워 {th.get('followers')} · 게시 {th.get('posts_total')} · 30일 조회 {th.get('views_30d') if th.get('views_30d') is not None else '미수집'}")
    if res.get("commit") or res.get("sha"):
        lines.append(f"• 커밋 {res.get('commit') or res.get('sha')}")
    return "\n".join(lines)


def push_dashboard(cfg: dict, state: dict, force: bool = False, source: str = "자동") -> dict:
    """집 PC 수치를 pc_stats.json 의 dashboard 키로 올린다(수치 동일하면 6시간에 1회만)."""
    token = _get_github_token(cfg)
    if not token:
        log("대시보드 푸시 생략: GitHub 토큰 없음")
        if force:
            send_telegram(cfg, "📊 [대시보드 푸시] GitHub 토큰이 없어 올리지 못했습니다\n"
                               "• config.json 의 github_token 또는 survey_config.json github.token 확인")
        return {"ok": False, "error": "토큰 없음"}

    payload = build_dashboard_payload(cfg, state)
    try:
        DASH_LOCAL_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log(f"pc_dashboard.json 저장 실패: {e}")

    sig_src = {k: v for k, v in payload.items() if k not in ("filled_at", "filled_at_kst")}
    # 블로그·스레드는 수집 시각(source_at)만 바뀌어도 커밋이 늘어나므로, 서명에서는 숫자·링크만 본다.
    for _k in ("blog", "threads"):
        if isinstance(sig_src.get(_k), dict):
            sig_src[_k] = {kk: vv for kk, vv in sig_src[_k].items()
                           if kk not in ("source_at", "views_at")}
    sig = json.dumps(sig_src, ensure_ascii=False, sort_keys=True)
    gap = time.time() - float(state.get("last_dash_push", 0) or 0)
    if (not force) and state.get("dash_sig") == sig and gap < DASH_PUSH_MIN_GAP:
        # stats_checker 가 pc_stats.json 을 새로 올리면 dashboard 키가 사라진다 → 그때는 다시 박는다
        remote, _ = _github_get_json(DASH_REMOTE_PATH, token)
        if isinstance(remote.get("dashboard"), dict):
            log("대시보드 푸시 생략: 수치 변동 없음(최근 푸시 6시간 이내 · 원격 dashboard 키 유지)")
            return {"ok": True, "skipped": True}
        log("원격 pc_stats.json 에 dashboard 키가 없음 → 생성 푸시")

    put_obj = {"dashboard": payload}
    if payload.get("toss"):
        # 최상위 toss 키도 같이 덮어쓴다 — 예전에 pc_stats.json의 toss를 읽던 쪽도 null이 아니게.
        put_obj["toss"] = payload["toss"]
    res = _github_put_json(DASH_REMOTE_PATH, put_obj,
                           f"pc: dashboard numbers {payload['filled_at_kst']} KST ({source})", token)
    if res.get("ok"):
        state["last_dash_push"] = time.time()
        state["dash_sig"] = sig
        log("대시보드 푸시 완료: pc_stats.json dashboard 갱신 (commit %s)"
            % (res.get("commit") or res.get("sha") or "?"))
        if force:
            send_telegram(cfg, _dash_msg(payload, res))
        return res

    log(f"대시보드 푸시 실패: {res.get('detail')}")
    prev_fail = float(state.get("last_dash_fail_alert", 0) or 0)
    if force or (time.time() - prev_fail) >= 6 * 3600:
        state["last_dash_fail_alert"] = time.time()
        send_telegram(cfg, "📊 [대시보드 푸시] 실패 — %s" % _tg_clean(str(res.get("detail")), cfg)[:200])
    return res


# ---------------------------------------------------------------- 설문·퀴즈·특가 레이더 본체
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SURVEY_CONFIG_FILE = BASE_DIR / "survey_config.json"
SURVEY_LAST_FILE = BASE_DIR / "last_check_surveys.json"


def _read_url(url: str, timeout: int = 25, limit: int = 700000):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _BROWSER_UA,
            "Accept": "application/rss+xml,application/xml,text/xml,*/*",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Referer": "https://www.ppomppu.co.kr/",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(limit)


def _parse_rss_items(raw: bytes):
    """RSS/Atom XML -> [(제목, 링크, 발행시각 or None)]"""
    root = ET.fromstring(raw)
    items = []
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not link:
            link = (it.findtext("guid") or "").strip()
        pub = None
        pub_txt = (it.findtext("pubDate") or "").strip()
        if pub_txt:
            try:
                pub = email.utils.parsedate_to_datetime(pub_txt)
            except Exception:
                pub = None
        items.append((title, link, pub))
    return items


def _event_categories(title: str) -> list:
    return [name for name, pat in EVENT_KEYWORDS if re.search(pat, title, re.IGNORECASE)]


def _norm_event_title(title: str) -> str:
    """구글뉴스의 '본문 - 언론사' 꼬리표를 떼어 같은 사안의 중복 알림을 줄인다."""
    t = re.sub(r"\s+-\s+[^-]{2,25}$", "", title or "").strip()
    t = re.sub(r"\s+|\[[^\]]*\]|\([^\)]*\)", "", t)
    return t.lower()[:120]


def _format_event_lines(hits, limit: int = EVENT_REPORT_LIMIT) -> str:
    lines = []
    for cats, title, link in hits[:limit]:
        lines.append(f"• [{('/'.join(cats))}] {title}\n  {link}")
    if len(hits) > limit:
        lines.append(f"…외 {len(hits) - limit}건")
    return "\n".join(lines)


def check_events(cfg: dict, state: dict, force_report: bool = False) -> None:
    """설문·퀴즈·만족도조사·기프티콘·선착순 이벤트 신규 글을 중복 없이 알린다."""
    seen = set(state.get("seen_events") or [])
    seen_titles = set(t for t in (state.get("seen_event_titles") or []) if t)
    first_run = not state.get("events_seeded")
    hits, seeds, stats, fails = [], [], [], []
    now_utc = datetime.now(timezone.utc)

    for name, url in EVENT_FEEDS:
        try:
            status, raw = _read_url(url)
            items = _parse_rss_items(raw)
        except Exception as e:
            fails.append(f"{name}({type(e).__name__})")
            log(f"이벤트 피드 오류 ({name}): {e}")
            continue
        matched = 0
        limit_dt = now_utc - timedelta(days=EVENT_MAX_AGE_DAYS)
        for title, link, pub in items:
            if not title or not link:
                continue
            if "news.google.com" in url and pub is not None:
                try:
                    if pub.tzinfo is None:
                        pub = pub.replace(tzinfo=timezone.utc)
                    if pub < limit_dt:
                        continue
                except Exception:
                    pass
            if EVENT_NOISE.search(title):
                continue
            cats = _event_categories(title)
            if not cats:
                continue
            matched += 1
            key_title = _norm_event_title(title)
            if link in seen or (key_title and key_title in seen_titles):
                continue
            seen.add(link)
            if key_title:
                seen_titles.add(key_title)
            state.setdefault("seen_events", []).append(link)
            state.setdefault("seen_event_titles", []).append(key_title)
            (seeds if first_run else hits).append((cats, title, link))
        stats.append(f"{name}={len(items)}건/매칭{matched}")
        time.sleep(0.4)

    state["seen_events"] = (state.get("seen_events") or [])[-800:]
    state["seen_event_titles"] = (state.get("seen_event_titles") or [])[-800:]
    state["last_event_check"] = time.time()

    if first_run:
        state["events_seeded"] = True
        log(f"이벤트 레이더 초기 시드 {len(seeds)}건 저장 (다음 회차부터 신규 알림) | " + ", ".join(stats))
        if force_report:
            head = f"🎯 [설문·퀴즈 레이더 즉시 점검] 초기 스캔 매칭 {len(seeds)}건 (앞으로는 신규만 알림)"
            if fails:
                head += f"\n⚠️ 실패 피드: {', '.join(fails)}"
            send_telegram(cfg, head + ("\n" + _format_event_lines(seeds) if seeds else ""))
        return

    if hits:
        msg = f"🎯 [설문·퀴즈·기프티콘 신규 {len(hits)}건]\n" + _format_event_lines(hits)
        log(msg)
        send_telegram(cfg, msg)
    else:
        log("설문·퀴즈 레이더 신규 0건 | " + ", ".join(stats))
        if force_report:
            msg = "🎯 [설문·퀴즈 레이더 즉시 점검] 신규 0건\n" + ", ".join(stats)
            if fails:
                msg += f"\n⚠️ 실패 피드: {', '.join(fails)}"
            send_telegram(cfg, msg)


def _load_survey_collector():
    """survey_collector.py 를 필요할 때만 import (없거나 깨져도 본 루프는 계속)."""
    try:
        here = str(BASE_DIR)
        if here not in sys.path:
            sys.path.insert(0, here)
        import survey_collector  # noqa: F401  (같은 폴더 모듈)
        return survey_collector
    except Exception as e:
        log(f"survey_collector 로드 실패(건너뜀): {e}")
        return None


def _ago(ts) -> str:
    """마지막 실행 시각을 'n분 전' 형태로."""
    try:
        ts = float(ts or 0)
    except Exception:
        return "기록 없음"
    if ts <= 0:
        return "미실행"
    m = int((time.time() - ts) / 60)
    if m < 1:
        return "방금 전"
    if m < 60:
        return f"{m}분 전"
    return f"{m // 60}시간 {m % 60}분 전"


def _survey_state_note() -> str:
    """survey_collector 중복 상태 파일 크기 요약(없으면 미실행)."""
    try:
        p = BASE_DIR / "survey_seen.json"
        if not p.exists():
            return "미실행"
        d = json.loads(p.read_text(encoding="utf-8"))
        return f"{len(d.get('seen_ids') or {})}건"
    except Exception:
        return "확인실패"


def _survey_msg(summary: dict, force_report: bool) -> str:
    c = summary.get("counts") or {}
    head = (
        f"🎁 [경품 설문·퀴즈 수집] 신규 {c.get('new_candidates', 0)}건 "
        f"(응모가능 {c.get('candidate', 0)} · 검토 {c.get('review', 0)} · "
        f"중복 {c.get('duplicates', 0)} · 소스실패 {c.get('sources_failed', 0)})"
    )
    lines = [head]
    for t in (summary.get("top") or [])[:5]:
        dl = t.get("deadline") or "마감 미확인"
        lines.append(f"• [{t.get('status')}·{dl}] {t.get('title')}")
        if t.get("survey_url"):
            lines.append(f"  {t.get('survey_url')}")
    gh = (summary.get("github") or {}).get("detail")
    if gh and gh not in ("not requested",):
        lines.append(f"• GitHub: {gh}")
    for w in (summary.get("warnings") or [])[:2]:
        lines.append(f"• ⚠️ {w}")
    if not summary.get("top") and force_report:
        lines.append("• 새 후보 없음 — 다음 주기까지 대기")
    return "\n".join(lines)


def check_surveys(cfg: dict, state: dict, force_report: bool = False) -> None:
    """survey_collector.run_once() 로 경품 설문·퀴즈 후보(pc_survey_candidates.json)를 모아 알린다."""
    state["last_survey_check"] = time.time()
    sc = _load_survey_collector()
    if sc is None:
        if force_report:
            send_telegram(cfg, "🎁 [설문 수집] survey_collector.py 를 불러오지 못했습니다 (같은 폴더에 있는지 확인)")
        return

    # GitHub 토큰 주입 (config.json 의 github_token/github_pat 또는 survey_config.json 의 github.token)
    gh_tok = cfg.get("github_token") or cfg.get("github_pat")
    if not gh_tok and SURVEY_CONFIG_FILE.exists():
        try:
            _scfg = json.loads(SURVEY_CONFIG_FILE.read_text(encoding="utf-8"))
            gh_tok = (_scfg.get("github") or {}).get("token")
        except Exception:
            pass
    if gh_tok and not os.environ.get("GITHUB_TOKEN"):
        os.environ["GITHUB_TOKEN"] = str(gh_tok).strip()

    # survey_config.json 이 없으면 기본값(cbseng18-glitch/sy-dashboard)으로 자동 생성
    if not SURVEY_CONFIG_FILE.exists():
        try:
            _default_scfg = {
                "profile": {"gender": "남성", "birth_year": 1983, "age_note": "만 43세(1983년생)", "region": "서울 노원구"},
                "github": {
                    "enabled": True,
                    "owner": "cbseng18-glitch",
                    "repo": "sy-dashboard",
                    "path": "pc_survey_candidates.json",
                    "branch": "main",
                    "token_env": "GITHUB_TOKEN",
                    "commit_message": "chore: PC 설문 수집 후보 갱신",
                },
            }
            SURVEY_CONFIG_FILE.write_text(
                json.dumps(_default_scfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    push = cfg.get("survey_push_github")
    if push is None:
        push = bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
    history_paths = [p for p in (cfg.get("survey_import_history") or []) if Path(p).exists()]
    seen_paths = [p for p in (cfg.get("survey_import_seen") or []) if Path(p).exists()]
    try:
        summary = sc.run_once(
            dry_run=False,
            config_path=str(SURVEY_CONFIG_FILE) if SURVEY_CONFIG_FILE.exists() else None,
            push_github=bool(push),
            telegram=False,
            import_history_paths=history_paths,
            import_seen_paths=seen_paths,
            quiet=True,
        )
    except Exception as e:
        log(f"설문 수집 예외(본 루프 계속): {type(e).__name__}: {e}")
        if force_report:
            send_telegram(cfg, f"🎁 [설문 수집] 실행 오류: {type(e).__name__}: {e}")
        return

    try:
        SURVEY_LAST_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log(f"last_check_surveys.json 저장 실패: {e}")

    c = summary.get("counts") or {}
    log("설문 수집 완료: 신규 %s건(응모가능 %s · 검토 %s · 중복 %s · 소스실패 %s)" % (
        c.get("new_candidates"), c.get("candidate"), c.get("review"),
        c.get("duplicates"), c.get("sources_failed")))
    new_n = int(c.get("new_candidates") or 0)
    if new_n > 0 or force_report:
        send_telegram(cfg, _survey_msg(summary, force_report))


# ---------------------------------------------------------------- 쇼피 통합조회 · 무료부스트 (stats_checker / shopee_booster)
# 2026-11-27 인1(@kimseungyong22) 집 PC용 2종을 이 감시 루프에 연결.
#  · check_stats : 쇼피 8개국 유입·부스트 슬롯 + 큐텐 5종 + 설문 당첨 발표 (기본 120분)
#  · run_boost   : shopee_cookies.json 이 있을 때만 무료 부스트 (기본 240분 = 4시간 쿨다운)
PC_AUTO_DIRS = (BASE_DIR, BASE_DIR / "pc_auto", BASE_DIR / "pc-scripts")
STATS_STATE_FILE = BASE_DIR / "last_check_stats.json"
BOOST_LAST_FILE = BASE_DIR / "last_run_boost.json"
SHOPEE_COOKIE_CANDIDATES = tuple(d / "shopee_cookies.json" for d in PC_AUTO_DIRS)
STATS_COOKIE_ALERT_HOURS = 6      # 쿠키 만료 알림 최소 간격(알림 피로 방지)
BOOST_COOKIE_ALERT_HOURS = 12     # 쿠키 없음 알림 최소 간격


def _pc_auto_module(name: str):
    """stats_checker.py / shopee_booster.py 를 필요할 때만 import (없거나 깨져도 본 루프는 계속)."""
    if name in sys.modules:
        return sys.modules[name]
    for folder in PC_AUTO_DIRS:
        here = str(folder)
        if here not in sys.path:
            sys.path.insert(0, here)
    try:
        return importlib.import_module(name)
    except Exception as e:
        log(f"{name} 로드 실패(건너뜀): {type(e).__name__}: {e}")
        return None


def _inject_github_token(cfg: dict) -> bool:
    """stats_checker·shopee_booster 는 env GITHUB_TOKEN 을 읽는다 → config 값(survey_config 포함)으로 주입."""
    if (os.environ.get("GITHUB_TOKEN") or "").strip():
        return True
    tok = _get_github_token(cfg)
    if tok:
        os.environ["GITHUB_TOKEN"] = tok
        return True
    return False


def _shopee_cookie_path():
    """shopee_cookies.json 이 있는 위치(BASE_DIR·pc_auto·pc-scripts 순)."""
    for p in SHOPEE_COOKIE_CANDIDATES:
        if p.exists():
            return p
    return None


def _pc_stamp(payload: dict) -> str:
    raw = str(payload.get("generated_at") or "")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%m-%d %H:%M")
    except Exception:
        return datetime.now().strftime("%m-%d %H:%M")


def _stats_msg(payload: dict, full: bool = False) -> str:
    shopee = payload.get("shopee") or {}
    totals = shopee.get("totals") or {}
    qoo10 = payload.get("qoo10") or {}
    qc = qoo10.get("counts") or {}
    watch = payload.get("survey_watch") or {}
    cookie = shopee.get("cookie_state")
    lines = [f"📊 [PC 통합조회 {_pc_stamp(payload)}]"]

    if cookie == "ok":
        lines.append(
            f"• 쇼피 {totals.get('regions_ok', 0)}/{totals.get('regions_total', 0)}국 · "
            f"방문 {totals.get('uv', 0)} · 클릭 {totals.get('product_clicks', 0)} · "
            f"주문 {totals.get('orders', 0)} · 부스트 {totals.get('boost_used', 0)}/{totals.get('boost_total', 0)}"
        )
    elif cookie == "cookie_expired":
        lines.append("• ⚠️ 쇼피 쿠키 만료 → shopee_cookies.json 갱신 필요(피클)")
    elif cookie == "cookie_missing":
        lines.append("• ⚠️ 쇼피 쿠키 파일 없음(shopee_cookies.json) — 쇼피·부스트는 쿠키 세팅 후부터")
    else:
        lines.append(f"• ⚠️ 쇼피 조회 실패({cookie or 'error'})")

    if qc.get("total"):
        if qc.get("blocked") == qc.get("total"):
            lines.append(f"• 큐텐 {qc['total']}종 전부 차단(이 네트워크) — 집 PC(주거용 IP)에서는 열립니다")
        else:
            lines.append(
                f"• 큐텐 {qc.get('ok', 0)}/{qc.get('total', 0)} 정상 · 가격 {qc.get('price_found', 0)}건 확인"
                + (f" · 불일치 {qc['price_mismatch']}" if qc.get("price_mismatch") else "")
            )

    due = (watch.get("due") or {}).get("due") or []
    if due:
        titles = [str(e.get("title") or "")[:16] for e in due[:3]]
        lines.append(f"• 오늘 발표 예정 {len(due)}건: " + ", ".join(titles))

    for a in (watch.get("alerts") or []):
        if not (a.get("is_new") or full):
            continue
        if len(lines) > 8:
            break
        mark = "‼️" if a.get("level") == "high" else "·"
        lines.append(f"{mark} {a.get('target')}: {a.get('reason')}")

    changes = payload.get("changes") or {}
    if changes.get("has_changes"):
        parts = []
        for region, delta in (changes.get("regions") or {}).items():
            if "uv" in delta:
                parts.append(f"{region} 방문 {delta['uv']:+d}")
            if "product_clicks" in delta:
                parts.append(f"{region} 클릭 {delta['product_clicks']:+d}")
            if "boost_slots" in delta:
                parts.append(f"{region} 부스트 {delta['boost_slots']:+d}")
        for q in (changes.get("qoo10") or [])[:2]:
            label = q.get("label") or q.get("no")
            if "price_from" in q or "price_to" in q:
                parts.append(f"큐텐 {label} 가격 {q.get('price_from')}→{q.get('price_to')}")
            else:
                parts.append(f"큐텐 {label} {q.get('from')}→{q.get('to')}")
        if parts:
            lines.append("• 변화: " + ", ".join(parts[:6]))
    elif full:
        lines.append("• 직전 회차 대비 변화 없음")

    if full:
        g = payload.get("github") or {}
        if g.get("detail"):
            lines.append(f"• GitHub: {'✔ ' if g.get('ok') else '✕ '}{str(g.get('detail'))[:80]}")
        for w in (payload.get("warnings") or [])[:2]:
            lines.append(f"• ⚠️ {str(w)[:90]}")
        for e in (payload.get("errors") or [])[:2]:
            lines.append(f"• ✕ {str(e)[:90]}")
    return "\n".join(lines)[:3500]


def _boost_msg(payload: dict, full: bool = False) -> str:
    totals = payload.get("totals") or {}
    cookie = payload.get("cookie_state")
    stamp = _pc_stamp(payload)
    if cookie == "cookie_expired":
        return (f"🚀 [PC 무료부스트 {stamp}]\n"
                "• ⚠️ 쇼피 쿠키 만료 → shopee_cookies.json 갱신하면 다음 회차부터 자동 재개")
    if cookie == "cookie_missing":
        return (f"🚀 [PC 무료부스트 {stamp}]\n"
                "• ⚠️ shopee_cookies.json 없음 → 쿠키 넣으면 4시간마다 자동으로 채웁니다")
    if payload.get("skipped_reason") == "recent_run":
        return (f"🚀 [PC 무료부스트 {stamp}]\n"
                f"• 직전 실행 {payload.get('skip_minutes', 0)}분 전 → 이번 회차 건너뜀(중복 방지)")
    lines = [
        f"🚀 [PC 무료부스트 {stamp}] 쇼피 {totals.get('regions_ok', 0)}/8국 · "
        f"빈 슬롯 {totals.get('free_slots', 0)} · 성공 {totals.get('boosted', 0)} · 실패 {totals.get('failed', 0)}"
    ]
    if full:
        per = []
        for r in payload.get("regions") or []:
            after = r.get("slots_after") or r.get("slots") or {}
            if r.get("ok"):
                per.append(f"{r.get('region')} {after.get('slots_used', '?')}/{after.get('slots_total', '?')}")
            else:
                per.append(f"{r.get('region')} ✕")
        if per:
            lines.append("• " + " · ".join(per))
        for w in (payload.get("warnings") or [])[:2]:
            lines.append(f"• ⚠️ {str(w)[:90]}")
    return "\n".join(lines)[:3500]


def check_stats(cfg: dict, state: dict, force_report: bool = False) -> None:
    """stats_checker.run_once() 로 쇼피 8개국·큐텐 5종·설문 당첨 발표를 조회해 보고한다.

    텔레그램은 소란을 줄이기 위해 (1) 변동·신규 알림·쿠키 문제 (2) 12시간마다 1회 요약
    (3) /통계 명령(force_report) 일 때만 보낸다.
    """
    state["last_stats_check"] = time.time()
    mod = _pc_auto_module("stats_checker")
    if mod is None:
        if force_report:
            send_telegram(cfg, "📊 [PC 통합조회] stats_checker.py 를 불러오지 못했습니다 (pc_auto/pc-scripts 폴더 확인)")
        return

    has_tok = _inject_github_token(cfg)
    push = cfg.get("stats_push_github")
    if push is None:
        push = has_tok
    try:
        payload = mod.run_once(dry_run=False, push_github=bool(push), telegram=False, quiet=True)
    except Exception as e:
        log(f"통합조회 예외(본 루프 계속): {type(e).__name__}: {e}")
        send_telegram(cfg, f"📊 [PC 통합조회] 실행 오류: {type(e).__name__}: {_tg_clean(e, cfg)}")
        return

    try:
        STATS_STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log(f"last_check_stats.json 저장 실패: {e}")

    c = payload.get("counts") or {}
    shopee = payload.get("shopee") or {}
    qc = (payload.get("qoo10") or {}).get("counts") or {}
    log("통합조회 완료: 쇼피 %s/8국(쿠키 %s) · 큐텐 %s/%s · 오늘발표 %s · 알림 %s · 오류 %s" % (
        c.get("shopee_regions_ok"), c.get("shopee_cookie_state"),
        qc.get("ok", 0), qc.get("total", 0), c.get("watch_due"), c.get("watch_alerts"), c.get("errors")))

    cookie_state = shopee.get("cookie_state")
    cookie_bad = cookie_state in ("cookie_expired", "error")
    digest_due = (time.time() - float(state.get("last_stats_report", 0) or 0)) >= \
        float(cfg.get("stats_digest_hours", 12)) * 3600
    report = bool(force_report or digest_due
                  or int(c.get("watch_new_alerts") or 0) > 0
                  or (payload.get("changes") or {}).get("has_changes"))
    if cookie_bad and (time.time() - float(state.get("last_stats_cookie_alert", 0) or 0)) >= \
            STATS_COOKIE_ALERT_HOURS * 3600:
        report = True
        state["last_stats_cookie_alert"] = time.time()
    if report:
        send_telegram(cfg, _stats_msg(payload, full=bool(force_report or digest_due)))
        state["last_stats_report"] = time.time()


def run_boost(cfg: dict, state: dict, force_report: bool = False) -> None:
    """shopee_booster.run_once() 로 무료 부스트를 채운다.

    shopee_cookies.json 이 있을 때만 실행하고, 없거나 만료일 때만 1줄로 알린다.
    정상 완료도 1줄 요약만 보낸다(사용량·알림 절감).
    """
    state["last_boost_check"] = time.time()
    cookie_path = _shopee_cookie_path()
    if cookie_path is None:
        log("무료 부스트 건너뜀: shopee_cookies.json 없음")
        last = float(state.get("last_boost_cookie_alert", 0) or 0)
        if force_report or (time.time() - last) >= BOOST_COOKIE_ALERT_HOURS * 3600:
            state["last_boost_cookie_alert"] = time.time()
            send_telegram(cfg, "🚀 [PC 무료부스트] shopee_cookies.json 이 없어 실행하지 않았습니다.\n"
                               "• 셀러센터 쿠키를 한 번 넣어 주면 4시간마다 자동으로 채웁니다.")
        return

    mod = _pc_auto_module("shopee_booster")
    if mod is None:
        if force_report:
            send_telegram(cfg, "🚀 [PC 무료부스트] shopee_booster.py 를 불러오지 못했습니다 (pc_auto/pc-scripts 폴더 확인)")
        return

    has_tok = _inject_github_token(cfg)
    push = cfg.get("boost_push_github")
    if push is None:
        push = has_tok
    try:
        payload = mod.run_once(dry_run=False, force=False, telegram=False,
                               push_github=bool(push), quiet=True)
    except Exception as e:
        log(f"무료부스트 예외(본 루프 계속): {type(e).__name__}: {e}")
        send_telegram(cfg, f"🚀 [PC 무료부스트] 실행 오류: {type(e).__name__}: {_tg_clean(e, cfg)}")
        return

    try:
        BOOST_LAST_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log(f"last_run_boost.json 저장 실패: {e}")

    totals = payload.get("totals") or {}
    log("무료부스트: 쇼피 %s/8국 · 빈 슬롯 %s · 시도 %s · 성공 %s · 실패 %s · 쿠키 %s" % (
        totals.get("regions_ok"), totals.get("free_slots"), totals.get("attempted"),
        totals.get("boosted"), totals.get("failed"), payload.get("cookie_state")))

    cookie_state = payload.get("cookie_state")
    if cookie_state in ("cookie_expired", "cookie_missing"):
        last = float(state.get("last_boost_cookie_alert", 0) or 0)
        if force_report or (time.time() - last) >= BOOST_COOKIE_ALERT_HOURS * 3600:
            state["last_boost_cookie_alert"] = time.time()
            send_telegram(cfg, _boost_msg(payload, full=True))
        return
    if payload.get("skipped_reason") == "recent_run" and not force_report:
        return
    send_telegram(cfg, _boost_msg(payload, full=bool(force_report)))


def check_url_alive(url: str) -> str:
    if not url or not url.startswith("http"):
        return "URL없음"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as r:
            return f"생존({r.status})"
    except Exception:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
            with urllib.request.urlopen(req, timeout=10) as r:
                return f"생존({r.status})"
        except Exception as e:
            return f"확인실패({type(e).__name__})"


def check_vooy_sheet(cfg: dict, state: dict, force_report: bool = False) -> None:
    req = urllib.request.Request(VOOY_SHEET_CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        text = r.read().decode("utf-8", "replace")
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) <= 1:
        if force_report:
            send_telegram(cfg, "📊 [부이_조사함] 아직 데이터 행이 없음")
        return
    seen = set(state.get("seen_sheet_rows", []))
    first_run = len(seen) == 0
    new_lines = []
    for row in rows[1:]:
        if len(row) < 4:
            continue
        key = "|".join(c.strip() for c in row[:5])
        if not key or key in seen:
            continue
        seen.add(key)
        state["seen_sheet_rows"].append(key)
        if not first_run:
            cat = row[1].strip() if len(row) > 1 else ""
            title = row[2].strip() if len(row) > 2 else ""
            url = row[4].strip() if len(row) > 4 else (row[3].strip() if len(row) > 3 else "")
            status = check_url_alive(url)
            new_lines.append(f"• [{cat}] {title}\n  {status} | {url}")
    state["seen_sheet_rows"] = state["seen_sheet_rows"][-500:]
    if first_run:
        log(f"부이 시트 초기 {len(state['seen_sheet_rows'])}행 저장 완료")
    elif new_lines:
        msg = "📊 [부이_조사함 신규 항목 + 링크 검증]\n" + "\n".join(new_lines[:10])
        log(msg)
        send_telegram(cfg, msg)
    else:
        log("부이 시트 신규 행 없음")
        if force_report:
            send_telegram(cfg, f"📊 [부이_조사함 즉시 점검] 신규 행 없음 (누적 {len(rows)-1}행)")


# ---------------------------------------------------------------- 토스 쉐어링크 Open API · 공인 IP
#  ★ 보안 원칙: client_id·client_secret·publisher_id 는 집 PC 의 toss_config.json 에만 둔다.
#    코드·공개 저장소에는 어떤 키도 하드코딩/커밋하지 않는다.
#    공인 IP 도 공개 저장소(pc_stats.json 등)에 올리지 않고 비공개 텔레그램 방으로만 보낸다.
TOSS_CONFIG_FILE = BASE_DIR / "toss_config.json"
TOSS_TOKEN_FILE = BASE_DIR / "toss_token.json"      # 액세스 토큰 재사용용(집 PC 로컬 전용)
TOSS_TOKEN_URL = "https://oauth2.cert.toss.im/token"
TOSS_API_BASE = "https://sharelink.toss.im/openapi"
TOSS_SCOPE = "sharelink:read sharelink:write"
TOSS_TOKEN_MARGIN = 300                                # 만료 5분 전에 미리 재발급
TOSS_STATS_FILE = BASE_DIR / "toss_stats.json"          # /performance 마지막 집계(대시보드 바인딩용)
TOSS_PRODUCTS_FILE = BASE_DIR / "toss_products.json"    # 상품 조회·할인율 결과
TOSS_COOKIE_FILE = BASE_DIR / "toss_cookies.json"       # 내부 curation API 용 로그인 쿠키(선택)
TOSS_INTERNAL_BASE = "https://sharelink.toss.im"
TOSS_CURATION_SECTIONS = ("TODAY_DEAL", "BEST_SELLING", "TRENDING", "CATEGORY_BEST",
                          "MOST_ISSUED", "GOOD_PRICE", "HIGH_CONVERSION")
IP_LOOKUP_URLS = [
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
    "https://ifconfig.me/ip",
]
KST = timezone(timedelta(hours=9))


def load_toss_config() -> dict:
    """toss_config.json 로드. 파일이 없으면 빈 템플릿만 만들어 두고 빈 값으로 돌려준다."""
    template = {"client_id": "", "client_secret": "", "publisher_id": ""}
    if not TOSS_CONFIG_FILE.exists():
        try:
            TOSS_CONFIG_FILE.write_text(
                json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"토스 설정 템플릿 생성: {TOSS_CONFIG_FILE}")
        except Exception as e:
            log(f"토스 설정 템플릿 생성 실패: {type(e).__name__}: {e}")
        return dict(template)
    try:
        raw = json.loads(TOSS_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"토스 설정 해석 실패: {type(e).__name__}: {e}")
        return dict(template)
    out = dict(template)
    if isinstance(raw, dict):
        for key in template:
            out[key] = str(raw.get(key) or "").strip()
    return out


def _toss_ready(cfg: dict):
    """설정이 다 채워져 있으면 설정 dict, 아니면 텔레그램 안내 후 None."""
    tc = load_toss_config()
    missing = [k for k in ("client_id", "client_secret", "publisher_id") if not tc.get(k)]
    if missing:
        send_telegram(
            cfg,
            "🔑 [토스 쉐어링크] 설정이 비어 있습니다\n"
            "toss_config.json에 client_id, client_secret, publisher_id를 입력해 주세요\n"
            f"• 파일: {TOSS_CONFIG_FILE}\n"
            f"• 비어 있는 값: {', '.join(missing)}",
        )
        log(f"토스 설정 미완료({', '.join(missing)}) — API 호출 생략")
        return None
    return tc


def _toss_configured() -> bool:
    """toss_config.json 이 다 채워졌는지 조용히 확인한다(텔레그램 알림 없음·파일 생성만)."""
    try:
        tc = load_toss_config()
    except Exception:
        return False
    return all(tc.get(k) for k in ("client_id", "client_secret", "publisher_id"))


def refresh_toss_stats(cfg: dict, state: dict) -> None:
    """pc_stats.json 의 toss 수치가 늘 최신이 되도록 토스 실적을 조용히 갱신한다.

    - toss_config.json 이 없거나 비어 있으면 아무 알림 없이 건너뛴다(본 루프는 계속).
    - 성공하면 집 PC 로컬 toss_stats.json 이 갱신되고, 다음 대시보드 푸시에 toss 로 실린다.
    - 실패해도 예외를 밖으로 던지지 않는다(감시 루프 보호).
    """
    state["last_toss_check"] = time.time()      # 재시도 폭주 방지: 시도 시각부터 간격 계산
    if not _toss_configured():
        log("토스 실적 자동 갱신 건너뜀: toss_config.json 미완료(설정 안내는 /토스통계 때만)")
        return
    try:
        r = cmd_toss_stats(cfg, {}, source="자동", quiet=True)
        if isinstance(r, dict) and r.get("ok"):
            log("토스 실적 자동 갱신 완료: %s~%s 클릭 %s·판매 %s" % (
                r.get("from"), r.get("to"), r.get("clickCount"), r.get("soldQuantity")))
        else:
            log("토스 실적 자동 갱신 실패: %s" % ((r or {}).get("error") if isinstance(r, dict) else r))
    except Exception as e:
        log(f"토스 실적 자동 갱신 오류(본 루프 계속): {type(e).__name__}: {e}")


# ---------------------------------------------------------------- 네이버 블로그 RSS · 스레드 공개 프로필 (비로그인 경량 수집)
#  ★ 원칙: 로그인·쿠키 없이 공개 주소만 읽는다. 스레드 30일 조회수 같은 인사이트는 공식 Threads API
#    토큰이 집 PC threads_config.json 에 있을 때만 조용히 시도하고, 없으면 알림 없이 건너뛴다.
#    토큰·비밀값은 이 파일·공개 저장소에 절대 남기지 않는다(로그에도 찍지 않는다).
BLOG_ID = "sy_global-"
BLOG_RSS_URL = "https://rss.blog.naver.com/%s.xml" % BLOG_ID
BLOG_TOTAL_URL = ("https://blog.naver.com/PostTitleListAsync.naver?blogId=%s"
                  "&viewdate=&currentPage=1&categoryNo=0&parentCategoryNo=&countPerPage=1" % BLOG_ID)
BLOG_STATS_FILE = BASE_DIR / "blog_rss_stats.json"
THREADS_PROFILE_URL = "https://www.threads.com/@18.1q2w3e4r"
THREADS_STATS_FILE = BASE_DIR / "threads_stats.json"
THREADS_CONFIG_FILE = BASE_DIR / "threads_config.json"
THREADS_API_BASE = "https://graph.threads.net/v1.0"
THREADS_CRAWLER_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
# 60일 장기 토큰 자동 연장 — 수동 재발급 없이 계속 돌리기 위한 최소 장치(2026-11-28 패치)
THREADS_REFRESH_URL = "https://graph.threads.net/refresh_access_token"
THREADS_TOKEN_REFRESH_DAYS = 25       # 기본 연장 주기(일) — 60일 만료 35일 전에 미리 연장
THREADS_REFRESH_RETRY_HOURS = 24      # 연장 실패 후 최소 재시도 간격(호출·로그 폭주 방지)
THREADS_REFRESH_TIMEOUT = 25          # 연장 요청 타임아웃(초)


def _http_bytes(url: str, ua: str, timeout: int = 25, limit: int = 900000) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": ua,
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Accept": "application/rss+xml,application/xml,text/xml,text/html,*/*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit)


def _num_kr(txt) -> int | None:
    """'32'·'1,234'·'1.2만' 같은 표기를 int 로. 못 읽으면 None."""
    t = str(txt or "").strip().replace(",", "").replace(" ", "")
    if not t:
        return None
    mult = 1
    for suf, m in (("억", 100000000), ("만", 10000), ("천", 1000)):
        if t.endswith(suf):
            mult, t = m, t[:-1]
            break
    try:
        return int(round(float(t) * mult))
    except Exception:
        return None


def _clean_rss_link(link: str) -> str:
    """네이버 RSS 꼬리표(?fromRss=true&trackingCode=rss) 제거."""
    link = (link or "").strip()
    if link.startswith("https://blog.naver.com/"):
        link = link.split("?")[0]
    return link


def refresh_blog_rss(cfg: dict, state: dict) -> None:
    """블로그 RSS(최신 글·오늘 발행 수) + 전체 글 수를 조용히 갱신한다.

    - 실패해도 예외를 밖으로 던지지 않고 텔레그램도 보내지 않는다(감시 루프 보호).
    - 결과는 집 PC 로컬 blog_rss_stats.json → 다음 대시보드 푸시의 blog 블록으로 실린다.
    """
    state["last_blog_check"] = time.time()
    now = datetime.now(KST)
    out = {"fetched_at": now.isoformat(timespec="seconds"), "ok": False,
           "blog_id": BLOG_ID, "blog_url": "https://blog.naver.com/%s" % BLOG_ID}
    items = []
    try:
        items = _parse_rss_items(_http_bytes(BLOG_RSS_URL, _BROWSER_UA))
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
        log("블로그 RSS 수집 실패(본 루프 계속): %s" % out["error"])
    if items:
        today_kst = now.strftime("%Y-%m-%d")
        posts_today = 0
        for _t, _l, pub in items:
            if pub is None:
                continue
            try:
                p = pub.astimezone(KST) if pub.tzinfo else pub.replace(tzinfo=KST)
                if p.strftime("%Y-%m-%d") == today_kst:
                    posts_today += 1
            except Exception:
                continue
        title, link, pub = items[0]
        out.update({
            "ok": True,
            "feed_items": len(items),
            "feed_capped": len(items) >= 50,       # 네이버 RSS 는 최근 50편까지만 준다
            "latest_title": re.sub(r"\s+", " ", title or "").strip()[:180],
            "latest_link": _clean_rss_link(link),
            "latest_pub": (pub.astimezone(KST).isoformat(timespec="seconds") if pub else ""),
            "latest_pub_kst": (pub.astimezone(KST).strftime("%m-%d %H:%M") if pub else ""),
            "posts_today": posts_today,
            "today": today_kst,
        })
    total = None
    try:
        raw = _http_bytes(BLOG_TOTAL_URL, _BROWSER_UA, timeout=20, limit=200000).decode("utf-8", "replace")
        m = re.search(r'"totalCount"\s*:\s*"?(\d+)', raw)
        if m:
            total = int(m.group(1))
    except Exception as e:
        log("블로그 전체 글 수 조회 실패(본 루프 계속): %s: %s" % (type(e).__name__, e))
    if total is not None:
        out["post_count"] = total
        out["post_count_source"] = "blog.naver.com totalCount"
        out["ok"] = True
    elif out.get("feed_items"):
        out["post_count"] = out["feed_items"]
        out["post_count_source"] = "rss_items(최근 50편 한도)"
    try:
        BLOG_STATS_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log("블로그 RSS 결과 저장 실패: %s: %s" % (type(e).__name__, e))
    if out.get("ok"):
        log("블로그 RSS 갱신: %s편(오늘 %s편) · 최신 %s" % (
            out.get("post_count"), out.get("posts_today"), out.get("latest_pub_kst")))


def load_threads_config() -> dict:
    """threads_config.json 로드(없으면 빈 템플릿만 만들어 둔다). enabled=false 면 인사이트 수집 안 함."""
    template = {
        "enabled": True,
        "user_id": "",
        "access_token": "",
        "token_refreshed_at": "",        # 마지막 연장 성공(또는 최초 설정) 시각 — 이 값 기준으로 연장 주기를 계산
        "token_expires_in": 0,           # 마지막 연장 응답의 expires_in(초, 있으면)
        "token_refresh_days": THREADS_TOKEN_REFRESH_DAYS,   # 연장 주기(일) — 1~55 범위
        "_help": ("스레드 30일 조회수는 공식 Threads API 토큰이 있어야만 읽힙니다. "
                  "developers.facebook.com 에서 앱 생성 → threads_basic·threads_manage_insights 권한으로 "
                  "장기 액세스 토큰 발급 → user_id(스레드 사용자 ID)와 함께 이 파일에 넣고 저장하세요. "
                  "비워 두면 팔로워·스레드 수만 공개 페이지에서 읽습니다(알림 없음). "
                  "access_token 을 넣으면 25일마다 자동 연장(th_refresh_token)되고 "
                  "token_refreshed_at 에 마지막 연장 시각이 기록됩니다."),
    }
    if not THREADS_CONFIG_FILE.exists():
        try:
            THREADS_CONFIG_FILE.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
            log("스레드 설정 템플릿 생성: %s" % THREADS_CONFIG_FILE)
        except Exception as e:
            log("스레드 설정 템플릿 생성 실패: %s: %s" % (type(e).__name__, e))
        return dict(template)
    try:
        raw = json.loads(THREADS_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log("스레드 설정 해석 실패: %s: %s" % (type(e).__name__, e))
        return dict(template)
    out = dict(template)
    if isinstance(raw, dict):
        for key in ("user_id", "access_token", "token_refreshed_at",
                    "token_last_attempt_at", "token_refresh_error"):
            out[key] = str(raw.get(key) or "").strip()
        try:
            out["token_expires_in"] = int(raw.get("token_expires_in") or 0)
        except Exception:
            out["token_expires_in"] = 0
        out["token_refresh_days"] = raw.get("token_refresh_days", THREADS_TOKEN_REFRESH_DAYS)
        out["enabled"] = bool(raw.get("enabled", True))
    return out


def _atomic_write_json(path, obj) -> None:
    """JSON 1개를 임시파일→교체로 원자적으로 저장한다(토큰 파일이 깨지지 않게)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    data = json.dumps(obj, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    try:
        os.chmod(tmp, 0o600)          # 토큰 파일 권한(윈도우에서는 무시됨)
    except Exception:
        pass
    os.replace(tmp, path)


def _iso_kst(raw):
    """ISO 문자열 → KST aware datetime. 비었거나 못 읽으면 None."""
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    try:
        return dt.astimezone(KST)
    except Exception:
        return None


def _scrub_threads_token(text, token) -> str:
    """로그·오류 문자열에서 토큰 원문을 지운다(원문은 어떤 경로로도 남기지 않는다)."""
    s = str(text if text is not None else "")
    t = str(token or "").strip()
    if t and t in s:
        s = s.replace(t, "<token>")
    return s


def _threads_refresh_days(tc: dict = None, cfg: dict = None) -> float:
    """연장 주기(일) — threads_config.json token_refresh_days → config.json → 기본 25일."""
    for src, key in (((tc or {}), "token_refresh_days"), ((cfg or {}), "threads_token_refresh_days")):
        try:
            v = float(src.get(key))
        except Exception:
            continue
        if 1 <= v <= 55:
            return v
    return float(THREADS_TOKEN_REFRESH_DAYS)


def _threads_token_refresh_plan(tc: dict, now: datetime = None, interval_days: float = None) -> dict:
    """토큰 연장이 필요한지 판단한다(토큰 원문은 읽지 않고 시각만 본다).

    - token_refreshed_at 기준 interval_days(기본 25일) 이상 지났으면 연장 시점.
    - 마지막 시도(token_last_attempt_at) 후 24시간이 안 지났으면 재시도하지 않는다.
    반환: {due, reason, age_days, retry_in_hours, interval_days}
    """
    now = now or datetime.now(KST)
    interval = float(interval_days or _threads_refresh_days(tc))
    plan = {"due": False, "reason": "", "age_days": None,
            "retry_in_hours": 0.0, "interval_days": interval}
    if not str(tc.get("access_token") or "").strip():
        plan["reason"] = "access_token 없음"
        return plan
    last_ok = _iso_kst(tc.get("token_refreshed_at"))
    age = (now - last_ok).total_seconds() / 86400.0 if last_ok else None
    plan["age_days"] = round(age, 2) if age is not None else None
    if age is not None and age < interval:
        plan["reason"] = "아직 연장 시점 아님(%.1f일/%d일)" % (age, int(interval))
        return plan
    last_try = _iso_kst(tc.get("token_last_attempt_at"))
    if last_try is not None:
        waited_h = (now - last_try).total_seconds() / 3600.0
        if waited_h < THREADS_REFRESH_RETRY_HOURS:
            plan["retry_in_hours"] = round(THREADS_REFRESH_RETRY_HOURS - waited_h, 1)
            plan["reason"] = "직전 시도 %.1f시간 전 → %.1f시간 뒤 재시도" % (
                waited_h, plan["retry_in_hours"])
            return plan
    plan["due"] = True
    if age is None:
        plan["reason"] = "token_refreshed_at 없음 → 지금 연장 확인"
    else:
        plan["reason"] = "마지막 연장 %.0f일 경과(기준 %d일)" % (age, int(interval))
    return plan


def _threads_refresh_call(token: str) -> tuple:
    """Meta th_refresh_token 1회 호출 → (json, http_code, 오류문자열). 토큰 원문은 남기지 않는다."""
    params = urllib.parse.urlencode({"grant_type": "th_refresh_token", "access_token": token})
    req = urllib.request.Request(
        THREADS_REFRESH_URL + "?" + params,
        headers={"User-Agent": "SY-PC-Watch/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=THREADS_REFRESH_TIMEOUT) as r:
            code = int(getattr(r, "status", 0) or 0)
            raw = r.read(300000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code = int(getattr(e, "code", 0) or 0)
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            raw = ""
        log("스레드 토큰 연장 응답 HTTP %s" % code)
    except Exception as e:
        return {}, 0, _scrub_threads_token("%s: %s" % (type(e).__name__, e), token)[:240]
    try:
        js = json.loads(raw) if raw.strip() else {}
    except Exception:
        js = {"_raw": raw[:200]}
    if not isinstance(js, dict):
        js = {}
    err = ""
    eobj = js.get("error")
    if isinstance(eobj, dict):
        err = " ".join(str(x) for x in (eobj.get("type"), eobj.get("code"),
                                        eobj.get("message"), eobj.get("error_user_msg")) if x)
    elif isinstance(eobj, str):
        err = eobj
    if not err and code >= 400:
        err = "HTTP %s" % code
    return js, code, _scrub_threads_token(err, token)[:240]


def _update_threads_config(patch: dict) -> None:
    """threads_config.json 에 patch 키만 덮어써 원자적으로 저장한다(다른 키·_help 보존)."""
    base = {}
    try:
        if THREADS_CONFIG_FILE.exists():
            base = json.loads(THREADS_CONFIG_FILE.read_text(encoding="utf-8")) or {}
    except Exception as e:
        log("스레드 설정 읽기 실패(받은 값만 저장): %s: %s" % (type(e).__name__, e))
        base = {}
    if not isinstance(base, dict):
        base = {}
    base.update(patch)
    _atomic_write_json(THREADS_CONFIG_FILE, base)


def refresh_threads_token(cfg: dict, state: dict = None, tc: dict = None,
                          force: bool = False) -> dict:
    """Threads 60일 장기 토큰을 도래 시 Meta 공식 엔드포인트(th_refresh_token)로 자동 연장한다.

    - access_token 이 없거나 enabled=false 면 조용히 건너뛴다(알림 없음).
    - 도래 판단: token_refreshed_at 기준 token_refresh_days(기본 25일). 값이 없으면 지금 한 번 확인.
    - 성공: 새 토큰을 메모리(tc)와 threads_config.json 에 원자적으로 저장하고 token_refreshed_at 갱신.
    - 실패: 기존 access_token 을 절대 지우지 않고 token_last_attempt_at 만 남겨 24시간 뒤 재시도.
    - 반환값·로그·결과 JSON·텔레그램에 토큰 원문은 넣지 않는다.
    """
    now = datetime.now(KST)
    try:
        if not isinstance(tc, dict):
            tc = load_threads_config()
    except Exception as e:
        log("스레드 토큰 연장 건너뜀(설정 읽기 실패): %s: %s" % (type(e).__name__, e))
        return {"ok": False, "skipped": True, "reason": "설정 읽기 실패"}

    token = str(tc.get("access_token") or "").strip()
    if not token:
        return {"ok": True, "skipped": True, "reason": "access_token 없음"}
    if not tc.get("enabled", True):
        return {"ok": True, "skipped": True, "reason": "enabled=false"}

    interval = _threads_refresh_days(tc, cfg)
    plan = _threads_token_refresh_plan(tc, now, interval)
    if not plan["due"] and not force:
        return {"ok": True, "skipped": True, "reason": plan["reason"],
                "age_days": plan["age_days"], "interval_days": interval,
                "retry_in_hours": plan["retry_in_hours"]}

    at = now.isoformat(timespec="seconds")
    js, code, err = _threads_refresh_call(token)
    new_token = str((js or {}).get("access_token") or "").strip()
    if new_token:
        try:
            expires_in = int((js or {}).get("expires_in") or 0)
        except Exception:
            expires_in = 0
        patch = {"access_token": new_token, "token_refreshed_at": at,
                 "token_last_attempt_at": at, "token_refresh_error": "",
                 "token_expires_in": expires_in}
        tc.update(patch)          # 메모리 즉시 반영 → 같은 회차 인사이트가 새 토큰을 쓴다
        saved = True
        try:
            _update_threads_config(patch)
        except Exception as e:
            saved = False
            log("스레드 토큰 연장은 됐지만 설정 저장 실패: %s: %s" % (type(e).__name__, e))
        if isinstance(state, dict):
            try:
                state["threads_token_refreshed_at"] = at
            except Exception:
                pass
        days = round(expires_in / 86400.0, 1) if expires_in else None
        log("스레드 토큰 자동 연장 완료: %s (만료 %s일, 설정저장 %s)" % (
            at, days if days is not None else "?", "성공" if saved else "실패"))
        return {"ok": True, "refreshed": True, "saved": saved, "refreshed_at": at,
                "expires_in": expires_in, "expires_days": days, "reason": "연장 완료"}

    # 실패 — 기존 토큰은 그대로 둔다(access_token 키를 쓰지 않으므로 덮어쓰지 않는다)
    etext = _scrub_threads_token(err or ("HTTP %s" % code), token)[:240].strip() or "이유 미상"
    try:
        _update_threads_config({"token_last_attempt_at": at, "token_refresh_error": etext})
    except Exception as e:
        log("스레드 토큰 연장 실패 기록 저장 오류: %s: %s" % (type(e).__name__, e))
    log("스레드 토큰 자동 연장 실패(기존 토큰 유지): %s — %s시간 뒤 재시도" % (
        etext, THREADS_REFRESH_RETRY_HOURS))
    return {"ok": False, "refreshed": False, "error": etext, "http": code,
            "retry_in_hours": THREADS_REFRESH_RETRY_HOURS}


def _threads_public_profile() -> dict:
    """비로그인 공개 프로필(og:description)에서 팔로워·스레드 수를 읽는다."""
    text = _http_bytes(THREADS_PROFILE_URL, THREADS_CRAWLER_UA, timeout=25).decode("utf-8", "replace")
    m = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"', text)
    if not m:
        raise ValueError("og:description 없음(페이지 형식 변경 의심)")
    desc = html.unescape(m.group(1))
    fm = re.search(r"팔로워\s*([\d.,]*\d)\s*(억|만|천)?\s*명", desc)
    pm = re.search(r"스레드\s*([\d.,]*\d)\s*(억|만|천)?\s*개", desc)
    out = {
        "profile_followers": _num_kr((fm.group(1) + (fm.group(2) or "")) if fm else ""),
        "profile_posts": _num_kr((pm.group(1) + (pm.group(2) or "")) if pm else ""),
        "profile_text": desc[:200],
    }
    if out["profile_followers"] is None and out["profile_posts"] is None:
        raise ValueError("팔로워·스레드 수 파싱 실패: %s" % desc[:120])
    return out


def _threads_api_insights(tc: dict) -> dict:
    """공식 Threads API(선택) — 최근 30일 views 합계·팔로워 수. 토큰 없으면 {}."""
    token = str(tc.get("access_token") or "").strip()
    if not token:
        return {}
    uid = str(tc.get("user_id") or "").strip() or "me"
    now = datetime.now(KST)
    out = {}

    def _call(params: dict) -> dict:
        url = "%s/%s/threads_insights?%s" % (
            THREADS_API_BASE, urllib.parse.quote(uid), urllib.parse.urlencode(params))
        try:
            return json.loads(_http_bytes(url, "SY-PC-Watch/1.0", timeout=25, limit=300000)
                              .decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            log("스레드 인사이트 HTTP %s: %s" % (e.code, _tg_clean(_scrub_threads_token(detail, token), {})))
        except Exception as e:
            # 예외 메시지에 요청 URL(토큰 포함)이 섞이는 경우가 있어 반드시 가린다
            log("스레드 인사이트 오류: %s: %s" % (type(e).__name__, _scrub_threads_token(e, token)))
        return {}

    res = _call({"metric": "views", "period": "day",
                 "since": str(int((now - timedelta(days=30)).timestamp())),
                 "until": str(int(now.timestamp())),
                 "access_token": token})
    for row in (res.get("data") or []):
        if not isinstance(row, dict) or row.get("name") != "views":
            continue
        vals = [v.get("value") for v in (row.get("values") or []) if isinstance(v, dict)]
        vals = [v for v in vals if isinstance(v, (int, float))]
        if vals:
            out["views_30d"] = int(sum(vals))
            out["views_days"] = len(vals)

    res2 = _call({"metric": "followers_count", "access_token": token})
    for row in (res2.get("data") or []):
        if not isinstance(row, dict) or row.get("name") != "followers_count":
            continue
        total = (row.get("total_value") or {}).get("value") if isinstance(row.get("total_value"), dict) else None
        if total is None:
            vals = [v.get("value") for v in (row.get("values") or []) if isinstance(v, dict)]
            vals = [v for v in vals if isinstance(v, (int, float))]
            total = vals[-1] if vals else None
        if isinstance(total, (int, float)):
            out["followers_api"] = int(total)
    return out


def refresh_threads_stats(cfg: dict, state: dict) -> None:
    """스레드 팔로워·게시물 수(공개 페이지) + 토큰이 있으면 30일 조회수 → threads_stats.json."""
    state["last_threads_check"] = time.time()
    now = datetime.now(KST)
    prev = _read_json_file(THREADS_STATS_FILE, {}) or {}
    out = dict(prev) if isinstance(prev, dict) else {}
    out["fetched_at"] = now.isoformat(timespec="seconds")
    ok = False
    try:
        prof = _threads_public_profile()
        if prof.get("profile_followers") is not None:
            out["followers"] = prof["profile_followers"]
        if prof.get("profile_posts") is not None:
            out["posts_total"] = prof["profile_posts"]
        out["profile_at"] = out["fetched_at"]
        out["profile_source"] = "threads.com 공개 프로필(og:description, 비로그인)"
        ok = True
        log("스레드 공개 프로필: 팔로워 %s · 스레드 %s" % (
            prof.get("profile_followers"), prof.get("profile_posts")))
    except Exception as e:
        log("스레드 공개 프로필 수집 실패(본 루프 계속): %s: %s" % (type(e).__name__, e))
    try:
        tc = load_threads_config()
        if str(tc.get("access_token") or "").strip() and tc.get("enabled", True):
            # ① 60일 장기 토큰 자동 연장(기본 25일 주기) — 인사이트보다 먼저 돌려야 같은 회차에 새 토큰을 쓴다.
            #    실패해도 기존 토큰을 지우지 않고 본 루프를 막지 않는다(내부에서 24시간 재시도 제한).
            tr = refresh_threads_token(cfg, state, tc=tc)
            out["token_refresh"] = {
                "checked_at": out["fetched_at"],
                "ok": bool(tr.get("ok")),
                "refreshed": bool(tr.get("refreshed")),
                "skipped": bool(tr.get("skipped")),
                "reason": str(tr.get("reason") or tr.get("error") or "")[:160],
                "age_days": tr.get("age_days"),
                "expires_days": tr.get("expires_days"),
                "refreshed_at": str(tc.get("token_refreshed_at") or ""),
                "last_attempt_at": str(tc.get("token_last_attempt_at") or ""),
            }
            ins = _threads_api_insights(tc)
            if ins:
                out.update(ins)
                out["insights_at"] = out["fetched_at"]
                out["insights_source"] = "graph.threads.net threads_insights"
                ok = True
                log("스레드 인사이트 갱신: 30일 조회 %s · 팔로워 %s" % (
                    ins.get("views_30d"), ins.get("followers_api")))
        else:
            out["insights_skip"] = "threads_config.json 토큰 없음 → 30일 조회수 미수집(공개 값만)"
    except Exception as e:
        log("스레드 인사이트 건너뜀: %s: %s" % (type(e).__name__, e))
    out["ok"] = ok
    try:
        THREADS_STATS_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log("스레드 결과 저장 실패: %s: %s" % (type(e).__name__, e))


def get_public_ip():
    """집 PC 현재 공인 IPv4 → (ip, 사용한 조회 URL). 실패하면 ("", "")."""
    for url in IP_LOOKUP_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SY-PC-Watch/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read(200).decode("utf-8", "replace")
            found = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", body)
            if found:
                return found.group(0), url
            log(f"공인 IP 응답 해석 실패({url})")
        except Exception as e:
            log(f"공인 IP 조회 실패({url}): {type(e).__name__}: {e}")
    return "", ""


def cmd_get_ip(cfg: dict, args: dict | None = None, source: str = "원격") -> dict:
    """공인 IP 는 비공개 텔레그램 방으로만 보낸다(공개 저장소·외부 전송 금지)."""
    ip, used = get_public_ip()
    if not ip:
        send_telegram(cfg, "🌐 [집 PC 공인 IP] 조회 실패 — 조회처 3곳 모두 응답이 없습니다\n"
                           "집 PC 인터넷 연결을 확인한 뒤 /ip 를 다시 보내 주세요.")
        return {"ok": False, "ip": ""}
    send_telegram(cfg, f"🌐 [집 PC 공인 IP] {ip} (토스 허용 IP 등록용)")
    log(f"공인 IP 조회 완료({used}) → 비공개 텔레그램으로만 전송")
    return {"ok": True, "ip": ip, "source": used}


def _toss_err_text(js) -> str:
    """토스 공통 오류 규약(error.errorCode / error.reason)에서 사유 문자열을 뽑는다."""
    if not isinstance(js, dict):
        return ""
    err = js.get("error")
    if isinstance(err, dict):
        code = str(err.get("errorCode") or "").strip()
        reason = str(err.get("reason") or err.get("message") or "").strip()
        return " ".join(x for x in (code, reason) if x)
    if isinstance(err, str) and err.strip():
        return err.strip()
    return str(js.get("errorCode") or "").strip()


def _toss_err_hint(err: str) -> str:
    """자주 겪는 오류에 대한 다음 조치 한 줄."""
    e = err or ""
    if "SHARELINK_OPENAPI_ACCESS_DENIED" in e:
        return ("• 출발지 IP 가 등록되지 않았거나 인증 정보가 다릅니다. 쉐어링크 크리에이터 어드민 → "
                "연동 → API 키 발급에서 집 PC 공인 IP(/ip)를 등록해 주세요.")
    if "PARTNER_NOT_AUTHORIZED" in e:
        return "• 상품 조회 API 는 토스쇼핑 인증 크리에이터만 쓸 수 있습니다(재시도해도 같음)."
    if "INVALID_ARGUMENT" in e:
        return "• 날짜 형식·기간(한 번에 31일)·subTagId 값을 확인해 주세요."
    if "token" in e.lower() or "토큰" in e or "401" in e:
        return "• toss_config.json 의 client_id·client_secret 을 확인해 주세요."
    return ""


def _toss_token(cfg: dict, tc: dict, force: bool = False) -> str:
    """액세스 토큰. 토스 문서 권고대로 받은 토큰을 저장해 재사용하고 만료 전에만 재발급한다."""
    now = time.time()
    if not force:
        try:
            saved = json.loads(TOSS_TOKEN_FILE.read_text(encoding="utf-8"))
            token = str(saved.get("access_token") or "")
            expires_at = float(saved.get("expires_at") or 0)
            # Access Key 가 바뀌면 앞 6자 힌트가 달라져 캐시를 버린다
            if (token and expires_at - now > TOSS_TOKEN_MARGIN
                    and str(saved.get("client_hint") or "") == tc["client_id"][:6]):
                return token
        except Exception:
            pass
    # ★ Authorization: Basic 헤더를 쓰지 않는다. client_id·client_secret·scope 를 폼 본문에 넣는다.
    form = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": TOSS_SCOPE,
        "client_id": tc["client_id"],
        "client_secret": tc["client_secret"],
    }).encode("utf-8")
    req = urllib.request.Request(
        TOSS_TOKEN_URL, data=form, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "SY-PC-Watch/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        log(f"토스 토큰 발급 실패 HTTP {e.code}: {detail}")
        return ""
    except Exception as e:
        log(f"토스 토큰 발급 실패: {type(e).__name__}: {e}")
        return ""
    token = str((res or {}).get("access_token") or "")
    if not token:
        log(f"토스 토큰 응답에 access_token 없음: {_tg_clean(res, cfg)}")
        return ""
    try:
        TOSS_TOKEN_FILE.write_text(json.dumps({
            "access_token": token,
            "expires_at": now + float(res.get("expires_in") or 0),
            "client_hint": tc["client_id"][:6],
            "saved_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(TOSS_TOKEN_FILE, 0o600)
        except Exception:
            pass
    except Exception as e:
        log(f"토스 토큰 저장 실패(이번 호출은 계속): {type(e).__name__}: {e}")
    return token


def _toss_call(cfg: dict, tc: dict, path: str, params: dict | None = None, body: dict | None = None):
    """토스 API 1회 호출 → (json, http_code, 오류사유). 토큰 401 이면 1회 재발급 후 재시도."""
    js, code, err = {}, 0, ""
    for attempt in (0, 1):
        token = _toss_token(cfg, tc, force=(attempt == 1))
        if not token:
            return None, 0, "토큰 발급 실패(client_id·client_secret·네트워크 확인)"
        url = TOSS_API_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
                   "User-Agent": "SY-PC-Watch/1.0"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                code = r.status
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            code = e.code
            raw = e.read().decode("utf-8", "replace")
        except Exception as e:
            log(f"토스 호출 실패({path}): {type(e).__name__}: {e}")
            return None, 0, f"{type(e).__name__}: {e}"
        try:
            js = json.loads(raw) if raw.strip() else {}
        except Exception:
            js = {"_raw": raw[:300]}
        err = _toss_err_text(js)
        if code == 401 and attempt == 0:
            log(f"토스 토큰 만료(401) → 재발급 후 재시도: {path}")
            try:
                TOSS_TOKEN_FILE.unlink()
            except Exception:
                pass
            continue
        if code >= 400 and not err:
            err = f"HTTP {code}"
        return js, code, err
    return js, 401, "인증 실패(토큰을 새로 받아도 401)"


def cmd_toss_health(cfg: dict, args: dict | None = None, source: str = "원격") -> dict:
    """GET /openapi/health — 인증·출발지 IP 등록이 통과하는지 확인."""
    tc = _toss_ready(cfg)
    if tc is None:
        return {"ok": False, "error": "설정 미완료"}
    js, code, err = _toss_call(cfg, tc, "/health")
    ok = isinstance(js, dict) and js.get("resultType") == "SUCCESS"
    lines = [f"🔗 [토스 쉐어링크 연결 확인 ({source})]"]
    if ok:
        status = (js.get("success") or {}).get("status") or "ok"
        lines.append(f"• 결과: 정상 (status={status})")
        lines.append("• 인증과 출발지 IP 등록이 모두 통과했습니다")
    else:
        lines.append(f"• 결과: 실패 (HTTP {code})")
        lines.append(f"• 사유: {err or '알 수 없음'}")
        hint = _toss_err_hint(err)
        if hint:
            lines.append(hint)
    send_telegram(cfg, "\n".join(lines))
    return {"ok": ok, "http": code, "error": err}


def _ymd(value, fallback):
    """YYYY-MM-DD 문자열 → date. 비어 있으면 fallback, 형식 오류면 ValueError."""
    s = str(value or "").strip()
    if not s:
        return fallback
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        raise ValueError(f"날짜 형식이 올바르지 않습니다: {s} (YYYY-MM-DD)")
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"달력에 없는 날짜입니다: {s} (YYYY-MM-DD)")


def cmd_toss_stats(cfg: dict, args: dict | None = None, source: str = "원격",
                   quiet: bool = False) -> dict:
    """GET /openapi/performance — 기간 클릭·판매·예상 수익금 요약(기본: 이번 달 1일~오늘 KST).

    quiet=True 는 본 루프의 자동 갱신용이다 — 텔레그램 보고를 보내지 않고 결과만 돌려준다.
    (명령·텔레그램 수동 호출은 기본 quiet=False 로 기존과 똑같이 보고한다.)
    """
    args = args or {}
    tc = _toss_ready(cfg)
    if tc is None:
        return {"ok": False, "error": "설정 미완료"}
    today = datetime.now(KST).date()
    try:
        d_from = _ymd(args.get("from_date") or args.get("fromDate"), today.replace(day=1))
        d_to = _ymd(args.get("to_date") or args.get("toDate"), today)
    except ValueError as e:
        if not quiet:
            send_telegram(cfg, f"📊 [토스 쉐어링크 실적] {e}")
        return {"ok": False, "error": str(e)}
    if d_from > d_to:
        msg = f"📊 [토스 쉐어링크 실적] 시작일({d_from})이 종료일({d_to})보다 늦습니다"
        if not quiet:
            send_telegram(cfg, msg)
        return {"ok": False, "error": "from_date > to_date"}

    sub_tag = str(args.get("sub_tag_id") or args.get("subTagId") or "").strip()
    keys = ("clickCount", "soldQuantity", "refundedQuantity", "salesAmount",
            "discountAmount", "netPaymentAmount", "expectedCommissionAmount",
            "confirmedCommissionAmount")
    total = {k: 0 for k in keys}
    items = []
    # 토스 문서: 기간은 최대 31일 → 넘으면 31일 창으로 나눠 합산
    windows, cur = [], d_from
    while cur <= d_to:
        end = min(cur + timedelta(days=30), d_to)
        windows.append((cur, end))
        cur = end + timedelta(days=1)
    for w_from, w_to in windows:
        params = {"fromDate": w_from.strftime("%Y-%m-%d"),
                  "toDate": w_to.strftime("%Y-%m-%d")}
        if sub_tag:
            params["subTagId"] = sub_tag
        js, code, err = _toss_call(cfg, tc, "/performance", params=params)
        if err or not isinstance(js, dict) or js.get("resultType") != "SUCCESS":
            msg = (f"📊 [토스 쉐어링크 실적] 조회 실패 ({w_from}~{w_to}, HTTP {code})\n"
                   f"• 사유: {err or '알 수 없음'}")
            hint = _toss_err_hint(err)
            if hint:
                msg += "\n" + hint
            if not quiet:
                send_telegram(cfg, msg)
            return {"ok": False, "error": err}
        summary = (js.get("success") or {}).get("summary") or {}
        for k in keys:
            try:
                total[k] += int(summary.get(k) or 0)
            except Exception:
                pass
        for it in ((js.get("success") or {}).get("items") or []):
            if isinstance(it, dict):
                items.append(it)

    def won(v):
        try:
            return f"{int(v):,}원"
        except Exception:
            return f"{v}원"

    lines = [f"📊 [토스 쉐어링크 실적] {d_from} ~ {d_to}"
             + (f" · subTag {sub_tag}" if sub_tag else "")]
    lines.append(f"• 클릭: {total['clickCount']:,}건")
    lines.append(f"• 판매 수량: {total['soldQuantity']:,}개 (환불 {total['refundedQuantity']:,}개)")
    lines.append(f"• 판매금액: {won(total['salesAmount'])}")
    lines.append(f"• 실 결제금액: {won(total['netPaymentAmount'])}")
    lines.append(f"• 예상 수익금: {won(total['expectedCommissionAmount'])} (원천징수 전)")
    lines.append(f"• 확정 수익금: {won(total['confirmedCommissionAmount'])} (구매확정분)")
    top = sorted(items, key=lambda x: int(x.get("expectedCommissionAmount") or 0), reverse=True)[:3]
    if top:
        lines.append("• 상위 상품:")
        for it in top:
            name = str(it.get("productName") or "(상품명 없음)")[:28]
            lines.append(f"   - {name} · {int(it.get('soldQuantity') or 0)}개 · "
                         f"{won(it.get('expectedCommissionAmount') or 0)}")
    if not total["clickCount"] and not total["soldQuantity"]:
        lines.append("• 이 기간에는 클릭·판매 실적이 없습니다")
    lines.append("• 확정 금액은 정산내역 기준이며 부가세·원천징수 전 금액입니다")
    if not quiet:
        send_telegram(cfg, "\n".join(lines))
    result = {"ok": True, "from": str(d_from), "to": str(d_to)}
    result.update(total)
    try:
        TOSS_STATS_FILE.write_text(json.dumps(
            {"fetched_at": datetime.now(KST).isoformat(timespec="seconds"), **result},
            ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log(f"toss_stats.json 저장 실패: {e}")
    return result


def cmd_toss_link(cfg: dict, args: dict | None = None, source: str = "원격") -> dict:
    """POST /openapi/links — 게시글에 넣을 쉐어링크(shortUrl) 발급."""
    args = args or {}
    raw_id = args.get("taca_item_id") or args.get("tacaItemId") or args.get("item_id")
    try:
        taca_item_id = int(str(raw_id).strip().replace(",", ""))
    except Exception:
        send_telegram(
            cfg,
            "🔗 [토스 쉐어링크 발급] taca_item_id(상품 옵션 ID)가 필요합니다\n"
            "• 예: {\"action\":\"toss_link\",\"args\":{\"taca_item_id\":1234567890,\"sub_tag_id\":\"threads\"}}",
        )
        return {"ok": False, "error": "taca_item_id 없음"}
    tc = _toss_ready(cfg)
    if tc is None:
        return {"ok": False, "error": "설정 미완료"}
    sub_tag = str(args.get("sub_tag_id") or args.get("subTagId") or "threads").strip()
    body = {"landingType": "PRODUCT", "tacaItemId": taca_item_id,
            "publisherId": tc["publisher_id"]}
    if sub_tag and sub_tag.lower() not in ("-", "none", "null"):
        body["subTagId"] = sub_tag
    js, code, err = _toss_call(cfg, tc, "/links", body=body)
    ok = isinstance(js, dict) and js.get("resultType") == "SUCCESS"
    if not ok:
        msg = (f"🔗 [토스 쉐어링크 발급] 실패 (HTTP {code}) · tacaItemId {taca_item_id}"
               + (f" · subTag {body.get('subTagId')}" if body.get("subTagId") else "")
               + f"\n• 사유: {err or '알 수 없음'}")
        hint = _toss_err_hint(err)
        if hint:
            msg += "\n" + hint
        if body.get("subTagId"):
            msg += ("\n• subTagId 는 어드민에 미리 등록된 값이어야 합니다. "
                    "등록 전이면 sub_tag_id 없이 다시 실행해 주세요.")
        send_telegram(cfg, msg)
        return {"ok": False, "error": err}
    succ = js.get("success") or {}
    short_url = str(succ.get("shortUrl") or "")
    origin_url = str(succ.get("originUrl") or "")
    issues = succ.get("tacaItemId")
    lines = ["🔗 [토스 쉐어링크 발급 완료]"]
    lines.append(f"• 상품 옵션: {issues if issues is not None else taca_item_id}")
    if body.get("subTagId"):
        lines.append(f"• subTag: {body['subTagId']}")
    lines.append(f"• 링크: {short_url or origin_url or '(응답에 링크 없음)'}")
    if short_url and origin_url:
        lines.append(f"• 원본: {origin_url}")
    lines.append("• 게시글에는 이 링크만 넣어야 수익이 잡힙니다(productUrl 은 집계 안 됨)")
    send_telegram(cfg, "\n".join(lines))
    return {"ok": True, "short_url": short_url, "origin_url": origin_url,
            "taca_item_id": issues}


def _toss_cookie_header():
    """toss_cookies.json → (Cookie 헤더 문자열, 상태). 여러 형식을 받아준다."""
    if not TOSS_COOKIE_FILE.exists():
        return "", "missing"
    raw = _read_json_file(TOSS_COOKIE_FILE, None)
    if raw is None:
        return "", "invalid"
    if isinstance(raw, str):
        return raw.strip(), "ok" if raw.strip() else "invalid"
    if isinstance(raw, dict):
        if raw.get("cookie"):
            return str(raw["cookie"]).strip(), "ok"
        src = raw.get("cookies") if isinstance(raw.get("cookies"), (dict, list)) else raw
        pairs = []
        if isinstance(src, dict):
            for k, v in src.items():
                if k and v is not None:
                    pairs.append(f"{k}={v}")
        elif isinstance(src, list):
            for it in src:
                if isinstance(it, dict) and it.get("name"):
                    pairs.append(f"{it['name']}={it.get('value', '')}")
        return ("; ".join(pairs), "ok") if pairs else ("", "invalid")
    return "", "invalid"


def _toss_internal_get(path: str, params: dict | None = None, cookies: str = "", timeout: int = 25):
    """토스 내부(비공식) JSON API 1회 조회 → (json, http_code, 오류문자열)."""
    url = TOSS_INTERNAL_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {
        "Accept": "application/json",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "User-Agent": _BROWSER_UA,
        "Referer": TOSS_INTERNAL_BASE + "/shopping/sharelink",
    }
    if cookies:
        headers["Cookie"] = cookies
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            code = r.status
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code = e.code
        raw = e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, 0, f"{type(e).__name__}: {e}"
    try:
        return (json.loads(raw) if raw.strip() else {}), code, ""
    except Exception:
        return {"_raw": raw[:200]}, code, "JSON 아님"


def _toss_product_rows(js) -> list:
    """curation 응답 → 상품 행 목록. item.accessToken 같은 비밀값은 읽지도 저장하지도 않는다."""
    rows = []
    success = (js or {}).get("success") if isinstance(js, dict) else {}
    sections = (success or {}).get("sections") or []
    if not sections and isinstance(success, dict) and isinstance(success.get("items"), list):
        sections = [{"section": str(success.get("section") or success.get("sectionCode") or "SECTION"), "items": success.get("items")}]
    elif not sections and isinstance(success, list):
        sections = [{"section": "SECTION", "items": success}]
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        sec_name = str(sec.get("section") or sec.get("sectionCode") or sec.get("name") or "")
        for it in (sec.get("items") or []):
            if not isinstance(it, dict):
                continue
            taca = it.get("taca") or it
            pv = taca.get("productView") or {}
            if not isinstance(pv, dict) or not pv.get("productId"):
                continue
            avail = it.get("linkIssueAvailability") or {}
            extra = taca.get("extraAttributes") or {}
            try:
                disc = float(pv.get("discountRate") or 0)
            except Exception:
                disc = 0.0
            try:
                price = int(pv.get("displayPrice") or 0)
            except Exception:
                price = 0
            try:
                orig = int(pv.get("originalPrice") or 0)
            except Exception:
                orig = 0
            rows.append({
                "section": sec_name,
                "taca_item_id": pv.get("productId"),
                "name": str(pv.get("displayName") or "")[:70],
                "price": price,
                "original_price": orig,
                "discount_rate": disc,
                "review_count": int(pv.get("reviewCount") or 0),
                "review_score": pv.get("reviewScore"),
                "sold_out": bool(pv.get("isSoldOut")),
                "status": pv.get("status"),
                "lowest_30d": bool((taca.get("displayContext") or {}).get("isLowestPriceIn30Days")),
                "today_deal": bool(it.get("todayDeal")),
                "link_available": (avail.get("available") if isinstance(avail, dict) else None),
                "link_reason": (avail.get("reason") if isinstance(avail, dict) else None),
                "categories": [str((c or {}).get("name") or c)[:20]
                               for c in (extra.get("categories") or [])][:3],
            })
    return rows


def cmd_toss_products(cfg: dict, args: dict | None = None, source: str = "원격") -> dict:
    """토스 쉐어링크 상품 조회·할인율 확인.

    공식 상품 Open API(/openapi/products/*)는 첫 매출 전까지 SHARELINK_OPENAPI_PARTNER_NOT_AUTHORIZED 로
    막혀 있어, toss_cookies.json 이 있으면 내부 curation API 로 조회하고 없으면 그 상태를 그대로 알린다.
      · 전체: GET /api-public/v3/shopping/sharelink/curation-sections?surface=BEST_RANKING&size=40
      · 섹션: GET /api-public/v3/shopping/sharelink/curation-sections/{SECTION}/items?tabCode=..&size=..&nextCursor=..
    """
    args = args or {}
    section = str(args.get("section") or "TODAY_DEAL").upper().strip()
    want_all = section in ("ALL", "전체")
    if not want_all and section not in TOSS_CURATION_SECTIONS:
        send_telegram(cfg, "🛒 [토스 상품 조회] SECTION 값이 올바르지 않습니다: %s\n• 가능: %s, ALL"
                           % (section, ", ".join(TOSS_CURATION_SECTIONS)))
        return {"ok": False, "error": "bad section"}
    tab_code = str(args.get("tab_code") or args.get("tabCode") or "").strip()
    try:
        size = max(1, min(40, int(args.get("size") or 20)))
    except Exception:
        size = 20
    try:
        limit = max(1, min(20, int(args.get("limit") or 8)))
    except Exception:
        limit = 8
    try:
        min_disc = float(args.get("min_discount") or 0)
    except Exception:
        min_disc = 0.0
    max_price = args.get("max_price")
    keyword = str(args.get("keyword") or "").strip()

    cookies, ck_state = _toss_cookie_header()
    rows, code, err, pages = [], 0, "", 0
    cursor = ""
    while pages < 3:
        if want_all and pages == 0:
            js, code, err = _toss_internal_get(
                "/api-public/v3/shopping/sharelink/curation-sections",
                {"surface": "BEST_RANKING", "size": size}, cookies)
        else:
            params = {"size": size}
            if tab_code:
                params["tabCode"] = tab_code
            if cursor:
                params["nextCursor"] = cursor
            sec_path = "TODAY_DEAL" if want_all else section
            js, code, err = _toss_internal_get(
                f"/api-public/v3/shopping/sharelink/curation-sections/{sec_path}/items", params, cookies)
        if err or not js or code >= 400:
            break
        rows.extend(_toss_product_rows(js))
        pages += 1
        cursor = str(((js.get("success") or {}).get("nextCursor")) or "")
        if want_all or not cursor:
            break
        time.sleep(0.2)

    if code >= 400 or not rows:
        lines = [f"🛒 [토스 상품 조회] 실패 (HTTP {code or '?'} · {source})"]
        if "AUTH_EXPIRED" in err or code == 401:
            lines.append("• 사유: 로그인 쿠키가 없거나 만료됐습니다(AUTH_EXPIRED)")
            lines.append("• 집 PC의 toss_cookies.json 에 sharelink.toss.im 로그인 쿠키를 넣어 주세요")
        elif "PARTNER_NOT_AUTHORIZED" in err:
            lines.append("• 사유: 공식 상품 API 는 첫 매출 전까지 막혀 있습니다(PARTNER_NOT_AUTHORIZED)")
            lines.append("• toss_cookies.json 을 넣으면 내부 curation API 로 조회합니다")
        else:
            shape = ""
            if isinstance(js, dict):
                s = js.get("success")
                if isinstance(s, dict):
                    shape = "keys=" + ",".join(list(s.keys())[:8])
                    for k, v in s.items():
                        if isinstance(v, list) and v and isinstance(v[0], dict):
                            shape += f" | {k}[0]=" + ",".join(list(v[0].keys())[:8])
                            break
                else:
                    shape = "top=" + ",".join(list(js.keys())[:8])
            lines.append(f"• 사유: {err or '상품이 없습니다'} ({shape})")
        send_telegram(cfg, "\n".join(lines))
        log(f"토스 상품 조회 실패: HTTP {code} {err} (쿠키 {ck_state})")
        return {"ok": False, "http": code, "error": err, "cookie_state": ck_state}

    def _num(v):
        try:
            return float(v)
        except Exception:
            return 0.0

    cand = [r for r in rows if not r["sold_out"] and _num(r["discount_rate"]) >= min_disc]
    if max_price not in (None, ""):
        cand = [r for r in cand if _num(r["price"]) <= _num(max_price)]
    if keyword:
        cand = [r for r in cand if keyword in r["name"]]
    linkable = [r for r in cand if r["link_available"] is not False]
    linkable.sort(key=lambda r: (_num(r["discount_rate"]), _num(r["review_count"])), reverse=True)
    top = linkable[:limit]

    payload = {
        "schema": "toss_products/1",
        "fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
        "section": ("ALL" if want_all else section),
        "tab_code": tab_code,
        "cookie_state": ck_state,
        "count": len(rows),
        "candidates": len(cand),
        "linkable": len(linkable),
        "items": top,
    }
    try:
        TOSS_PRODUCTS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log(f"toss_products.json 저장 실패: {e}")

    lines = [f"🛒 [토스 상품 조회] {('전체' if want_all else section)} · {len(rows)}건 중 {len(linkable)}건 발급가능 ({source})"]
    for r in top:
        lines.append(f"• {r['name'][:32]} · {r['discount_rate']:.0f}%↓ {r['price']:,}원"
                     f"(정가 {r['original_price']:,}) · 리뷰 {r['review_count']:,}"
                     + (" · 30일 최저" if r["lowest_30d"] else ""))
        lines.append(f"   ↳ 발급: {r['taca_item_id']}")
    if not top:
        lines.append("• 조건에 맞는 상품이 없습니다(품절·할인율 조건 확인)")
    lines.append("• 쉐어링크 발급: /토스링크 {tacaItemId} [subTagId]")
    send_telegram(cfg, "\n".join(lines))
    log("토스 상품 조회: %d건 · 조건충족 %d · 발급가능 %d (쿠키 %s)"
        % (len(rows), len(cand), len(linkable), ck_state))
    return {"ok": True, "count": len(rows), "candidates": len(cand),
            "items": len(top), "cookie_state": ck_state}


def cmd_toss_settlements(cfg: dict, args: dict | None = None, source: str = "원격") -> dict:
    """GET /openapi/settlements/{YYYY-MM} — 정산 내역(기본: 이번 달)."""
    args = args or {}
    month = str(args.get("month") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        month = datetime.now(KST).strftime("%Y-%m")
    tc = _toss_ready(cfg)
    if tc is None:
        return {"ok": False, "error": "설정 미완료"}
    js, code, err = _toss_call(cfg, tc, f"/settlements/{month}")
    ok = isinstance(js, dict) and js.get("resultType") == "SUCCESS"
    if not ok:
        msg = (f"💰 [토스 정산] {month} 조회 실패 (HTTP {code})\n• 사유: {err or '알 수 없음'}")
        hint = _toss_err_hint(err)
        if hint:
            msg += "\n" + hint
        send_telegram(cfg, msg)
        return {"ok": False, "http": code, "error": err}
    succ = js.get("success") or {}
    lines = [f"💰 [토스 정산] {month} ({source})"]
    shown = 0
    for key in ("settlementAmount", "totalAmount", "commissionAmount", "salesAmount",
                "settlementDate", "status", "payoutDate"):
        if isinstance(succ, dict) and key in succ:
            lines.append(f"• {key}: {succ.get(key)}")
            shown += 1
    rows = succ.get("items") if isinstance(succ, dict) else None
    if isinstance(rows, list):
        lines.append(f"• 정산 건수: {len(rows)}건")
        shown += 1
    if not shown:
        keys = ", ".join(list(succ.keys())[:8]) if isinstance(succ, dict) else "-"
        lines.append(f"• 응답 필드: {keys}")
        lines.append("• 금액 필드를 찾지 못해 원본 구조만 표시합니다(pc_auto/toss_settlements.json 저장)")
    try:
        (BASE_DIR / "toss_settlements.json").write_text(
            json.dumps({"month": month, "fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
                        "success": succ}, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log(f"toss_settlements.json 저장 실패: {e}")
    send_telegram(cfg, "\n".join(lines))
    return {"ok": True, "month": month}


def handle_action(cfg: dict, state: dict, action: str, args: dict | None = None, source: str = "원격") -> None:
    args = args or {}
    if action == "noop":
        return
    elif action == "status":
        up_min = int((time.time() - START_TIME) / 60)
        stock = state.get("daiso_stock", {})
        in_stock = sum(1 for v in stock.values() if v > 0)
        msg = (
            f"🖥️ [집 PC 상태 보고 ({source})]\n"
            f"• 연속 가동: {up_min}분\n"
            f"• 다이소 감시: {len(stock)}종 중 {in_stock}종 재고 있음\n"
            f"• 10km 오프라인 최근: {state.get('last_full_offline_date') or '미실행'}\n"
            f"• 부이 시트 감시 행: {len(state.get('seen_sheet_rows', []))}행\n"
            f"• 핫딜 시드: {len(state.get('seen_deals', []))}건\n"
            f"• 설문·퀴즈 레이더: 누적 {len(state.get('seen_events', []))}건 · "
            f"{_ago(state.get('last_event_check'))}\n"
            f"• 설문 수집기: 누적 중복상태 {_survey_state_note()} · {_ago(state.get('last_survey_check'))}\n"
            f"• 쇼피 통합조회: {_ago(state.get('last_stats_check'))} · "
            f"무료부스트: {_ago(state.get('last_boost_check'))} · "
            f"쿠키: {'설정됨' if _shopee_cookie_path() else '없음'}\n"
            f"• 큐텐 1~5호 다이소: {len(state.get('qoo10_daiso_qty') or {})}종 · "
            f"위험 {_qoo10_alert_count(state, cfg)}종 · {_ago(state.get('last_qoo10_daiso_check'))}\n"
            f"• 대시보드 푸시: {_ago(state.get('last_dash_push'))}\n"            f"• 네이버 카페 인기글: {_ago(state.get('last_cafe_check'))}"
            f" · 자동 {'/'.join(f'{h:02d}:{m:02d}' for h, m in CAFE_SLOTS)} KST\n"
            f"• 토스 쉐어링크: {_toss_state_note()}\n"
            f"• 지식iN·스레드 후보글: {_ago(state.get('last_targets_check'))} · "
            f"누적 {len(state.get('targets_seen') or [])}건 · Gemini {_gemini_note()}\n"
            f"• 인스타 교차발행: {_ig_note()}"
        )
        send_telegram(cfg, msg)
    elif action == "check_daiso":
        check_daiso(cfg, state, force_report=True)
    elif action == "check_daiso_full":
        check_daiso(cfg, state, force_report=False)
        check_daiso_full_offline(cfg, state, force_report=True)
    elif action == "check_qoo10_daiso":
        try:
            check_qoo10_daiso(cfg, state, force_report=True)
        except Exception as e:      # 본 감시 루프가 절대 죽지 않게
            log(f"큐텐 1~5호 점검 명령 오류(본 루프 계속): {type(e).__name__}: {e}")
            send_telegram(cfg, f"📦 [큐텐 1~5호 점검 오류] {type(e).__name__}: {_tg_clean(e, cfg)}")
    elif action == "check_cafe":
        try:
            check_cafe_hot(cfg, state, force_report=True, source=source)
        except Exception as e:      # 카페 수집 실패가 본 루프를 죽이지 않게
            log(f"카페 인기글 명령 오류(본 루프 계속): {type(e).__name__}: {e}")
            send_telegram(cfg, f"☕ [카페 인기글 오류] {type(e).__name__}: {_tg_clean(e, cfg)}")
    elif action == "push_dashboard":
        try:
            push_dashboard(cfg, state, force=True, source=source)
        except Exception as e:
            log(f"대시보드 푸시 명령 오류(본 루프 계속): {type(e).__name__}: {e}")
            send_telegram(cfg, f"📊 [대시보드 푸시 오류] {type(e).__name__}: {_tg_clean(e, cfg)}")
    elif action == "check_deals":
        check_deals(cfg, state, force_report=True)
    elif action == "check_events":
        check_events(cfg, state, force_report=True)
    elif action == "check_surveys":
        check_surveys(cfg, state, force_report=True)
    elif action == "check_stats":
        try:
            check_stats(cfg, state, force_report=True)
        except Exception as e:      # 본 감시 루프가 절대 죽지 않게
            log(f"통합조회 명령 오류(본 루프 계속): {type(e).__name__}: {e}")
            send_telegram(cfg, f"📊 [PC 통합조회 오류] {type(e).__name__}: {_tg_clean(e, cfg)}")
    elif action == "run_boost":
        try:
            run_boost(cfg, state, force_report=True)
        except Exception as e:
            log(f"무료부스트 명령 오류(본 루프 계속): {type(e).__name__}: {e}")
            send_telegram(cfg, f"🚀 [PC 무료부스트 오류] {type(e).__name__}: {_tg_clean(e, cfg)}")
    elif action == "verify_price":
        dv = _load_deal_verifier()
        title = str(args.get("title") or "")
        if dv is None or not title:
            send_telegram(cfg, "💰 [가격 검증] deal_verifier 없음 또는 제목(args.title) 누락")
        else:
            try:
                price = args.get("price")
                r = dv.verify(title, price=int(price) if price else None)
                msg = (f"💰 [가격 검증 ({source})]\n{title}\n" +
                       dv.format_line(r) +
                       (f"\n검색어: {r.get('query')}" if isinstance(r, dict) and r.get("query") else ""))
            except Exception as e:
                msg = f"💰 [가격 검증 오류] {type(e).__name__}: {e}"
            send_telegram(cfg, msg)
    elif action == "check_sheet":
        check_vooy_sheet(cfg, state, force_report=True)
    elif action == "get_ip":
        try:
            cmd_get_ip(cfg, args, source=source)
        except Exception as e:
            log(f"공인 IP 조회 오류(본 루프 계속): {type(e).__name__}: {e}")
            send_telegram(cfg, f"🌐 [집 PC 공인 IP] 오류 {type(e).__name__}: {_tg_clean(e, cfg)}")
    elif action in ("toss_health", "toss_stats", "toss_link", "toss_products", "toss_settlements"):
        handler = {"toss_health": cmd_toss_health,
                   "toss_stats": cmd_toss_stats,
                   "toss_link": cmd_toss_link,
                   "toss_products": cmd_toss_products,
                   "toss_settlements": cmd_toss_settlements}[action]
        try:
            handler(cfg, args, source=source)
        except Exception as e:      # 토스 오류가 본 감시 루프를 죽이지 않게
            log(f"토스 명령 오류(본 루프 계속): {type(e).__name__}: {e}")
            send_telegram(cfg, f"🔗 [토스 {action} 오류] {type(e).__name__}: {_tg_clean(e, cfg)}")
    elif action in ("collect_targets", "targets"):
        try:
            collect_targets(cfg, state, force_report=True)
        except Exception as e:      # 후보글 오류가 본 감시 루프를 죽이지 않게
            log(f"후보글 수집 명령 오류(본 루프 계속): {type(e).__name__}: {e}")
            send_telegram(cfg, f"🎯 [후보글 수집 오류] {type(e).__name__}: {_tg_clean(e, cfg)}")
    elif action in ("ig_health", "instagram_health"):
        try:
            send_telegram(cfg, instagram_status_msg(cfg))
        except Exception as e:
            log(f"인스타 상태 명령 오류(본 루프 계속): {type(e).__name__}: {e}")
    elif action in ("ig_publish", "instagram_publish"):
        try:
            instagram_publish(cfg, args, source=source, state=state)
        except Exception as e:      # 발행 오류가 본 감시 루프를 죽이지 않게
            log(f"인스타 발행 명령 오류(본 루프 계속): {type(e).__name__}: {e}")
            send_telegram(cfg, f"📸 [인스타 발행 오류] {type(e).__name__}: {_tg_clean(e, cfg)}")
    elif action == "fetch_urls":
        urls = (args.get("urls") or [])[:15]
        lines = [f"• {check_url_alive(str(u))} | {u}" for u in urls]
        send_telegram(cfg, f"🌐 [집 PC URL 점검 ({len(lines)}건)]\n" + "\n".join(lines))
    elif action == "make_card":
        make_card(cfg, args, source=source)
    elif action == "run_miri":
        run_miri(cfg, args, source=source)
    elif action == "make_shorts":
        try:
            make_shorts(cfg, args, source=source)
        except Exception as e:      # 본 감시 루프가 절대 죽지 않게
            log(f"쇼츠 명령 오류(본 루프 계속): {type(e).__name__}: {e}")
            try:
                send_telegram(cfg, f"🎬 [쇼츠 오류] {type(e).__name__}: {e}")
            except Exception:
                pass
    elif action == "self_update":
        try:
            self_update(cfg, state, args, source=source)
        except Exception as e:      # 교체 실패가 본 루프를 죽이지 않게
            log(f"자기 업데이트 오류(본 루프 계속): {type(e).__name__}: {e}")
            try:
                send_telegram(cfg, f"♻️ [자기 업데이트 오류] {type(e).__name__}: {e}")
            except Exception:
                pass
    else:
        log(f"허용되지 않은 원격 명령 무시: {action}")


def _get_github_token(cfg: dict) -> str:
    tok = cfg.get("github_token") or cfg.get("github_pat") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not tok and SURVEY_CONFIG_FILE.exists():
        try:
            _scfg = json.loads(SURVEY_CONFIG_FILE.read_text(encoding="utf-8"))
            tok = (_scfg.get("github") or {}).get("token") or ""
        except Exception:
            pass
    return str(tok).strip()


def check_remote_cmd(cfg: dict, state: dict) -> None:
    try:
        headers = {"User-Agent": "SY-PC-Watch/1.0", "Accept": "application/vnd.github.v3+json"}
        gh_tok = _get_github_token(cfg)
        if gh_tok:
            headers["Authorization"] = f"Bearer {gh_tok}"
        req = urllib.request.Request(REMOTE_CMD_URL, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read().decode("utf-8"))
        cmd = json.loads(base64.b64decode(res["content"]).decode("utf-8"))
        cmd_id = str(cmd.get("id") or "")
        if not cmd_id or cmd_id == state.get("last_remote_cmd_id"):
            return
        state["last_remote_cmd_id"] = cmd_id
        action = str(cmd.get("action") or "")
        args = cmd.get("args") if isinstance(cmd.get("args"), dict) else {}
        log(f"원격 명령 수신: {cmd_id} ({action})")
        handle_action(cfg, state, action, args, source="피비서 원격")
    except Exception as e:
        log(f"원격 명령 확인 생략/오류: {e}")


def check_telegram_cmds(cfg: dict, state: dict) -> None:
    token = (cfg.get("telegram_bot_token") or "").strip()
    target_chat = str(cfg.get("telegram_chat_id") or "").strip()
    if not token or not target_chat:
        return
    try:
        offset = int(state.get("last_tg_update_id", 0)) + 1
        url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=0"
        with urllib.request.urlopen(urllib.request.Request(url), timeout=10) as r:
            res = json.loads(r.read().decode("utf-8"))
        for upd in res.get("result", []):
            uid = int(upd.get("update_id", 0))
            if uid > state.get("last_tg_update_id", 0):
                state["last_tg_update_id"] = uid
            msg = upd.get("message") or {}
            if str((msg.get("chat") or {}).get("id", "")) != target_chat:
                continue
            text = (msg.get("text") or "").strip()
            if text.startswith("/상태"):
                handle_action(cfg, state, "status", source="텔레그램")
            elif text.startswith("/전수재고"):
                handle_action(cfg, state, "check_daiso_full", source="텔레그램")
            elif text.startswith("/재고"):
                handle_action(cfg, state, "check_daiso", source="텔레그램")
            elif text.startswith("/핫딜"):
                handle_action(cfg, state, "check_deals", source="텔레그램")
            elif text.startswith("/이벤트"):
                handle_action(cfg, state, "check_events", source="텔레그램")
            elif text.startswith("/설문"):
                handle_action(cfg, state, "check_surveys", source="텔레그램")
            elif text.startswith("/통계"):
                handle_action(cfg, state, "check_stats", source="텔레그램")
            elif text.startswith("/ip"):
                handle_action(cfg, state, "get_ip", source="텔레그램")
            elif text.startswith("/토스상태"):
                handle_action(cfg, state, "toss_health", source="텔레그램")
            elif text.startswith("/토스통계"):
                body = text[len("/토스통계"):].strip()
                parts = [p for p in re.split(r"[\s~]+", body) if p]
                targs = {}
                if parts:
                    targs["from_date"] = parts[0]
                if len(parts) > 1:
                    targs["to_date"] = parts[1]
                handle_action(cfg, state, "toss_stats", targs, source="텔레그램")
            elif text.startswith("/큐텐재고"):
                handle_action(cfg, state, "check_qoo10_daiso", source="텔레그램")
            elif text.startswith("/대시보드"):
                handle_action(cfg, state, "push_dashboard", source="텔레그램")
            elif text.startswith("/토스상품"):
                body = text[len("/토스상품"):].strip()
                targs = {}
                if body:
                    parts = [p for p in re.split(r"[\s,]+", body) if p]
                    if parts:
                        targs["section"] = parts[0]
                    if len(parts) > 1:
                        targs["tab_code"] = parts[1]
                handle_action(cfg, state, "toss_products", targs, source="텔레그램")
            elif text.startswith("/토스정산"):
                body = text[len("/토스정산"):].strip()
                handle_action(cfg, state, "toss_settlements",
                              {"month": body} if body else {}, source="텔레그램")
            elif text.startswith("/부스트"):
                handle_action(cfg, state, "run_boost", source="텔레그램")
            elif text.startswith("/시트"):
                handle_action(cfg, state, "check_sheet", source="텔레그램")
            elif text.startswith("/카페"):
                handle_action(cfg, state, "check_cafe", source="텔레그램")
            elif text.startswith("/카드"):
                body = text[len("/카드"):].strip()
                data = None
                if body.startswith("{"):
                    try:
                        data = json.loads(body)
                    except Exception as e:
                        send_telegram(cfg, f"🖼️ [카드] JSON 해석 실패: {e}")
                        continue
                elif body:
                    parts = [p.strip() for p in body.split("::") if p.strip()]
                    data = {"badge": parts[0] if parts else "",
                            "title": parts[1] if len(parts) > 1 else (parts[0] if parts else ""),
                            "items": parts[2:]}
                if not data:
                    send_telegram(cfg, "🖼️ [카드 사용법]\n/카드 {JSON}\n또는 /카드 배지 :: 제목|둘째줄 :: 소제목|설명 :: 소제목|설명")
                    continue
                try:
                    make_card(cfg, {"card_data": data}, source="텔레그램")
                except Exception as e:
                    log(f"카드 명령 오류(본 루프 계속): {type(e).__name__}: {e}")
            elif text.startswith("/미리"):
                try:
                    run_miri(cfg, {}, source="텔레그램")
                except Exception as e:
                    log(f"미리캔버스 명령 오류(본 루프 계속): {type(e).__name__}: {e}")
            elif text.startswith("/쇼츠"):
                body = text[len("/쇼츠"):].strip()
                data = None
                if body.startswith("{"):
                    try:
                        data = json.loads(body)
                    except Exception as e:
                        send_telegram(cfg, f"🎬 [쇼츠] JSON 해석 실패: {e}")
                        continue
                elif body:
                    parts = [p.strip() for p in body.split("::") if p.strip()]
                    data = {"title": parts[0] if parts else "",
                            "subtitle": parts[1] if len(parts) > 1 else ""}
                if not isinstance(data, dict) or not (data.get("scenes") or data.get("title")):
                    send_telegram(cfg, "🎬 [쇼츠 사용법]\n/쇼츠 {JSON}\n"
                                       "예: /쇼츠 {\"title\":\"제목\",\"subtitle\":\"자막\",\"upload\":false}\n"
                                       "또는 /쇼츠 제목 :: 자막")
                    continue
                try:
                    make_shorts(cfg, data, source="텔레그램")
                except Exception as e:
                    log(f"쇼츠 명령 오류(본 루프 계속): {type(e).__name__}: {e}")
            elif text.startswith("/타겟수집"):
                handle_action(cfg, state, "collect_targets", source="텔레그램")
            elif text.startswith("/인스타발행"):
                body = text[len("/인스타발행"):].strip()
                iargs = {}
                if body.startswith("{"):
                    try:
                        iargs = json.loads(body)
                    except Exception as e:
                        send_telegram(cfg, f"📸 [인스타발행] JSON 해석 실패: {e}")
                        continue
                elif body:
                    parts = body.split()
                    iargs = {"video_url": parts[0]}
                    if len(parts) > 1:
                        iargs["caption"] = " ".join(parts[1:])
                if not isinstance(iargs, dict) or not (iargs.get("video_url") or iargs.get("image_url")):
                    send_telegram(cfg, "📸 [인스타발행 사용법]\n/인스타발행 {\"video_url\":\"https://...mp4\","
                                       "\"caption\":\"문구\",\"confirm\":true}")
                    continue
                handle_action(cfg, state, "instagram_publish", iargs, source="텔레그램")
            elif text.startswith("/인스타"):
                handle_action(cfg, state, "instagram_health", source="텔레그램")
            elif text.startswith("/업데이트"):
                body = text[len("/업데이트"):].strip()
                data = {}
                if body.startswith("{"):
                    try:
                        data = json.loads(body)
                    except Exception as e:
                        send_telegram(cfg, f"♻️ [업데이트] JSON 해석 실패: {e}")
                        continue
                elif body:
                    data = {"files": [p.strip() for p in body.split() if p.strip()]}
                try:
                    self_update(cfg, state, data, source="텔레그램")
                except Exception as e:
                    log(f"업데이트 명령 오류(본 루프 계속): {type(e).__name__}: {e}")
    except Exception as e:
        log(f"텔레그램 명령 확인 오류: {e}")


def main() -> None:
    cfg = load_config()
    state = load_state()
    deal_sec = max(5, int(cfg.get("deal_interval_minutes", 30))) * 60
    daiso_sec = max(15, int(cfg.get("daiso_interval_minutes", 60))) * 60
    event_sec = max(300, int(cfg.get("event_interval_minutes", 10)) * 60)
    survey_sec = max(1800, int(float(cfg.get("survey_interval_hours", 2)) * 3600))
    stats_sec = max(600, int(cfg.get("stats_interval_minutes", 30)) * 60)
    boost_sec = max(900, int(cfg.get("boost_interval_minutes", 240)) * 60)
    q10_sec = max(1800, int(cfg.get("qoo10_daiso_interval_minutes", 60)) * 60)
    dash_sec = max(600, int(cfg.get("dashboard_push_minutes", 15)) * 60)
    toss_sec = max(1800, int(cfg.get("toss_interval_minutes", 180)) * 60)
    blog_sec = max(600, int(cfg.get("blog_rss_interval_minutes", 15)) * 60)
    threads_sec = max(900, int(cfg.get("threads_interval_minutes", 15)) * 60)
    targets_sec = max(1800, int(cfg.get("targets_interval_minutes", 180)) * 60)
    poll_sec = max(20, int(cfg.get("remote_poll_seconds", 60)))

    log("=== SY Auto Watch 시작 (57종 다이소+69매장+큐텐1~5호 09:30 경보+핫딜+부이시트+설문/퀴즈 레이더+경품 설문 수집+쇼피 통합조회·무료부스트+대시보드 수치 푸시+블로그 RSS·스레드 공개수집+토스 쉐어링크+네이버카페 인기글(08:30/14:30)+지식iN·Gemini 후보글+인스타 릴스·피드 교차발행(틀)+원격제어+인포그래픽카드+미리캔버스 · Ctrl+C 종료) ===")

    first_cycle = True
    while True:
        now = time.time()
        dt = datetime.now()
        today_str = dt.strftime("%Y-%m-%d")
        if now - state.get("last_deal_check", 0) >= deal_sec:
            try:
                check_deals(cfg, state)
            except Exception as e:
                log(f"핫딜 감시 오류: {e}")

        # ① 큐텐 1~5호 다이소 구성품 최우선 점검 — 매일 09:30 먼저, 그 뒤 60분 간격
        q10_daily_due = (state.get("last_qoo10_daiso_day") != today_str
                         and (dt.hour, dt.minute) >= (9, 30))
        if q10_daily_due:
            state["last_qoo10_daiso_day"] = today_str
            try:
                check_qoo10_daiso(cfg, state, force_report=True)
            except Exception as e:
                log(f"큐텐 1~5호 점검 오류(본 루프 계속): {type(e).__name__}: {e}")
        elif now - state.get("last_qoo10_daiso_check", 0) >= q10_sec:
            try:
                check_qoo10_daiso(cfg, state)
            except Exception as e:
                log(f"큐텐 1~5호 점검 오류(본 루프 계속): {type(e).__name__}: {e}")

        if now - state.get("last_daiso_check", 0) >= daiso_sec:
            try:
                check_daiso(cfg, state)
            except Exception as e:
                log(f"다이소 감시 오류: {e}")
            try:
                check_vooy_sheet(cfg, state)
            except Exception as e:
                log(f"부이 시트 감시 오류: {e}")

        if dt.hour == 9 and dt.minute >= 30 and state.get("last_full_offline_date") != today_str:
            try:
                check_daiso_full_offline(cfg, state)
            except Exception as e:
                log(f"오프라인 전수 점검 오류: {e}")

        if now - state.get("last_event_check", 0) >= event_sec:
            try:
                check_events(cfg, state)
            except Exception as e:
                log(f"설문·퀴즈 레이더 오류(본 루프 계속): {type(e).__name__}: {e}")

        if now - state.get("last_survey_check", 0) >= survey_sec:
            try:
                check_surveys(cfg, state)
            except Exception as e:
                log(f"설문 수집 오류(본 루프 계속): {type(e).__name__}: {e}")

        # 네이버 블로그 RSS·스레드 공개 프로필 → 블로그·스레드 카드 수치 조용히 갱신
        #   (토스·통합조회·대시보드 푸시보다 먼저 돌려야 같은 회차 푸시에 최신값이 실린다)
        if now - state.get("last_blog_check", 0) >= blog_sec:
            try:
                refresh_blog_rss(cfg, state)
            except Exception as e:
                log(f"블로그 RSS 갱신 오류(본 루프 계속): {type(e).__name__}: {e}")

        if now - state.get("last_threads_check", 0) >= threads_sec:
            try:
                refresh_threads_stats(cfg, state)
            except Exception as e:
                log(f"스레드 수집 오류(본 루프 계속): {type(e).__name__}: {e}")

        # 🎯 지식iN·스레드 후보글 → Gemini 필터 → pc_targets.json
        #   gemini_config.json 의 api_key 가 없으면 수집만 하고 조용히 건너뛴다(알림 없음).
        if now - state.get("last_targets_check", 0) >= _targets_gap_seconds(targets_sec):
            try:
                collect_targets(cfg, state)
            except Exception as e:
                log(f"후보글 수집 오류(본 루프 계속): {type(e).__name__}: {e}")

        # 토스 쉐어링크 실적 → toss_stats.json 조용히 갱신(설정 없으면 알림 없이 건너뜀)
        #   대시보드 푸시·통합조회보다 먼저 돌려야 같은 회차 푸시에 최신 toss 값이 실린다.
        if now - state.get("last_toss_check", 0) >= toss_sec:
            try:
                refresh_toss_stats(cfg, state)
            except Exception as e:
                log(f"토스 실적 자동 갱신 오류(본 루프 계속): {type(e).__name__}: {e}")

        if now - state.get("last_stats_check", 0) >= stats_sec:
            try:
                check_stats(cfg, state)
            except Exception as e:
                log(f"통합조회 오류(본 루프 계속): {type(e).__name__}: {e}")
            # stats_checker 가 pc_stats.json 을 통째로 새로 올리면 dashboard 키가 사라진다
            # → 바로 다시 박아 집 PC 실시간 카드가 1시간씩 비어 보이지 않게 한다(내부에서 생략 판단).
            try:
                push_dashboard(cfg, state)
            except Exception as e:
                log(f"통합조회 뒤 대시보드 재푸시 오류(본 루프 계속): {type(e).__name__}: {e}")


        if now - state.get("last_boost_check", 0) >= boost_sec:
            try:
                run_boost(cfg, state)
            except Exception as e:
                log(f"무료부스트 오류(본 루프 계속): {type(e).__name__}: {e}")

        # ③ 집 PC 수치를 대시보드(pc_stats.json dashboard)로 직접 푸시
        #   기동 첫 회차는 무조건 한 번 점검한다 — 재시작 뒤 dashboard 키 누락·토스 신규값을 바로 반영.
        if first_cycle or (now - state.get("last_dash_push", 0) >= dash_sec):
            try:
                push_dashboard(cfg, state)
            except Exception as e:
                log(f"대시보드 푸시 오류(본 루프 계속): {type(e).__name__}: {e}")
        first_cycle = False

        # ☕ 네이버 카페 인기글 — 매일 08:30·14:30 KST (가장 최근 도래 슬롯 1회만 실행)
        cafe_slot = ""
        for slot_h, slot_m in CAFE_SLOTS:
            if (dt.hour, dt.minute) >= (slot_h, slot_m):
                cafe_slot = f"{today_str}@{slot_h:02d}{slot_m:02d}"
        if cafe_slot and state.get("last_cafe_slot") != cafe_slot:
            state["last_cafe_slot"] = cafe_slot
            try:
                check_cafe_hot(cfg, state)
            except Exception as e:
                log(f"카페 인기글 오류(본 루프 계속): {type(e).__name__}: {e}")

        check_remote_cmd(cfg, state)
        check_telegram_cmds(cfg, state)
        save_state(state)
        time.sleep(poll_sec)


if __name__ == "__main__":
    main()
