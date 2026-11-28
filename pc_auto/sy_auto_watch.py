import base64
import csv
import email.utils
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
    "10290951": "[큐텐1호] A5 6공 스티커·포토카드 바인더 20포켓",
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
    "1072876": "[큐텐3호] 키링 사각 파우치",
    "1058005": "[큐텐3호] T형 카드 스탠드",
    "1073176": "[큐텐3호] 비즈 키링 리본",
    "1075138": "[큐텐3호] 썸머글로시 해파리 비즈 키링",
    "1066705": "[큐텐3호] 미니 사각 체크 파우치",
    "1074817": "[큐텐4호] 모트모트 스프링 스터디 플래너 브라운",
    "1055253": "[큐텐4호] 학습용 버튼 타이머(4기능, 무음)",
    "1013422": "[큐텐4호] 파스텔 형광펜 3개입",
    "1079748": "[큐텐4호] 모트모트 데일리 체크 점착 메모지 60매입",
    "1068539": "[큐텐5호] 맥세이프 호환 스탠드 카드 지갑 아이보리",
    "1049373": "[큐텐5호] 실리콘 스마트폰 스트랩 하트",
    "1083290": "[큐텐5호] 서머 비즈 스트랩 키링",
    "1062445": "[큐텐5호] 접착식 아크릴 팝핑거(강아지)",
    "1069124": "[큐텐5호] 퀼팅 모바일 파우치"
}
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
    # 57종 전체 동기화 보장
    merged = dict(DEFAULT_DAISO_ITEMS)
    merged.update(cfg.get("daiso_items") or {})
    cfg["daiso_items"] = merged
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
        # scenes 모드에서도 전체에 걸리는 옵션만 넘긴다
        cmd += _shorts_cli_extra(args, skip=("title", "subtitle", "narration", "badge",
                                            "image", "voice", "rate", "motion"))
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
            yt_title = str(args.get("yt_title") or "").strip()
            yt_title = re.sub(r"\s+", " ", yt_title.replace("\\n", " ").replace("\n", " ")).strip()
            if not yt_title and scenes:
                first = scenes[0] if isinstance(scenes[0], dict) else {}
                yt_title = re.sub(r"\s+", " ", str(first.get("title") or "").replace("\n", " ")).strip()
            if not yt_title:
                yt_title = str(args.get("title") or "").strip() or f"쇼츠 {datetime.now():%m/%d}"
            ucmd = [sys.executable, str(upload_py), "--video", str(out_path),
                    "--title", yt_title[:100], "--privacy", privacy]
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
                result.update({"uploaded": True, "url": url, "upload_privacy": privacy})
            else:
                tail = [x for x in (uerr or uout or "").splitlines() if x.strip()][-4:]
                head = ("업로드 시간초과 " + str(up_timeout) + "초" if utimed
                        else f"업로드 실패(종료코드 {urc})")
                lines.append(f"⚠️ [유튜브 {head} ({source})] 파일은 정상 생성됨")
                if tail:
                    lines.append("• " + "\n• ".join(t.strip()[:150] for t in tail))
                result.update({"uploaded": False, "upload_error": head})

    msg = "\n".join(lines)
    log(msg)
    send_telegram(cfg, msg)
    return result


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


def cmd_toss_stats(cfg: dict, args: dict | None = None, source: str = "원격") -> dict:
    """GET /openapi/performance — 기간 클릭·판매·예상 수익금 요약(기본: 이번 달 1일~오늘 KST)."""
    args = args or {}
    tc = _toss_ready(cfg)
    if tc is None:
        return {"ok": False, "error": "설정 미완료"}
    today = datetime.now(KST).date()
    try:
        d_from = _ymd(args.get("from_date") or args.get("fromDate"), today.replace(day=1))
        d_to = _ymd(args.get("to_date") or args.get("toDate"), today)
    except ValueError as e:
        send_telegram(cfg, f"📊 [토스 쉐어링크 실적] {e}")
        return {"ok": False, "error": str(e)}
    if d_from > d_to:
        msg = f"📊 [토스 쉐어링크 실적] 시작일({d_from})이 종료일({d_to})보다 늦습니다"
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
    send_telegram(cfg, "\n".join(lines))
    result = {"ok": True, "from": str(d_from), "to": str(d_to)}
    result.update(total)
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
            f"• 토스 쉐어링크: {_toss_state_note()}"
        )
        send_telegram(cfg, msg)
    elif action == "check_daiso":
        check_daiso(cfg, state, force_report=True)
    elif action == "check_daiso_full":
        check_daiso(cfg, state, force_report=False)
        check_daiso_full_offline(cfg, state, force_report=True)
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
    elif action in ("toss_health", "toss_stats", "toss_link"):
        handler = {"toss_health": cmd_toss_health,
                   "toss_stats": cmd_toss_stats,
                   "toss_link": cmd_toss_link}[action]
        try:
            handler(cfg, args, source=source)
        except Exception as e:      # 토스 오류가 본 감시 루프를 죽이지 않게
            log(f"토스 명령 오류(본 루프 계속): {type(e).__name__}: {e}")
            send_telegram(cfg, f"🔗 [토스 {action} 오류] {type(e).__name__}: {_tg_clean(e, cfg)}")
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
            elif text.startswith("/부스트"):
                handle_action(cfg, state, "run_boost", source="텔레그램")
            elif text.startswith("/시트"):
                handle_action(cfg, state, "check_sheet", source="텔레그램")
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
    stats_sec = max(600, int(cfg.get("stats_interval_minutes", 120)) * 60)
    boost_sec = max(900, int(cfg.get("boost_interval_minutes", 240)) * 60)
    poll_sec = max(20, int(cfg.get("remote_poll_seconds", 60)))

    log("=== SY Auto Watch 시작 (57종 다이소+69매장+핫딜+부이시트+설문/퀴즈 레이더+경품 설문 수집+쇼피 통합조회·무료부스트+원격제어+인포그래픽카드+미리캔버스 · Ctrl+C 종료) ===")

    while True:
        now = time.time()
        dt = datetime.now()
        if now - state.get("last_deal_check", 0) >= deal_sec:
            try:
                check_deals(cfg, state)
            except Exception as e:
                log(f"핫딜 감시 오류: {e}")

        if now - state.get("last_daiso_check", 0) >= daiso_sec:
            try:
                check_daiso(cfg, state)
            except Exception as e:
                log(f"다이소 감시 오류: {e}")
            try:
                check_vooy_sheet(cfg, state)
            except Exception as e:
                log(f"부이 시트 감시 오류: {e}")

        today_str = dt.strftime("%Y-%m-%d")
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

        if now - state.get("last_stats_check", 0) >= stats_sec:
            try:
                check_stats(cfg, state)
            except Exception as e:
                log(f"통합조회 오류(본 루프 계속): {type(e).__name__}: {e}")

        if now - state.get("last_boost_check", 0) >= boost_sec:
            try:
                run_boost(cfg, state)
            except Exception as e:
                log(f"무료부스트 오류(본 루프 계속): {type(e).__name__}: {e}")

        check_remote_cmd(cfg, state)
        check_telegram_cmds(cfg, state)
        save_state(state)
        time.sleep(poll_sec)


if __name__ == "__main__":
    main()
