#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_shorts.py — 1080x1920 세로 쇼츠 자동 조립기 (집 PC용)

상품 사진(또는 그라데이션 배경) + 상단 후킹 타이틀 + 하단 자막 + 부드러운 줌(Ken Burns)
+ 한국어 TTS 나레이션(edge-tts, 없으면 무음)으로 유튜브 쇼츠용 MP4를 만든다.

사용 예)
  1) 한 장으로 빠르게
     python make_shorts.py --image 상품.jpg --title "이거 사면 무조건 이득" --subtitle "쿠팡 최저가 12,900원" --seconds 12

  2) 나레이션까지
     python make_shorts.py --image 상품.jpg --title "..." --narration "오늘 소개할 상품은..." --voice ko-KR-SunHiNeural

  3) 여러 장면(시나리오 JSON)
     python make_shorts.py --script scenes_example.json --out my_shorts.mp4

  4) 대화형(더블클릭 bat에서 호출)
     python make_shorts.py --interactive

필요 패키지: pillow, imageio-ffmpeg, edge-tts  (requirements.txt 참고)
ffmpeg은 imageio-ffmpeg 내장 바이너리 → 시스템 설치 없이 동작.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ----------------------------------------------------------------------------
# 기본 규격
# ----------------------------------------------------------------------------
W, H = 1080, 1920
FPS = 30
SS = 2  # 배경을 2배 해상도로 그려 줌(팬) 시 계단현상 완화

THEMES = {
    "dark":   {"top": (14, 17, 24),   "bottom": (32, 36, 52),  "accent": (255, 92, 60),  "sub": (255, 214, 92)},
    "navy":   {"top": (8, 18, 42),    "bottom": (24, 58, 104), "accent": (64, 196, 255), "sub": (255, 224, 120)},
    "mint":   {"top": (10, 46, 42),   "bottom": (26, 104, 92), "accent": (255, 226, 120), "sub": (255, 255, 255)},
    "sunset": {"top": (58, 18, 44),   "bottom": (150, 58, 40), "accent": (255, 196, 84), "sub": (255, 255, 255)},
    "white":  {"top": (245, 246, 250), "bottom": (222, 226, 236), "accent": (232, 68, 48), "sub": (24, 28, 36)},
}


# ----------------------------------------------------------------------------
# 유틸
# ----------------------------------------------------------------------------
def ffmpeg_exe() -> str:
    """imageio-ffmpeg 내장 바이너리 → PATH의 ffmpeg 순으로 찾는다."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    raise SystemExit(
        "[오류] ffmpeg을 찾지 못했습니다.\n"
        "  해결: pip install imageio-ffmpeg  (또는 ffmpeg을 PATH에 추가)"
    )


def run(cmd: list[str], quiet: bool = True) -> None:
    """ffmpeg 실행. 실패하면 마지막 stderr를 보여준다."""
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        tail = res.stderr.decode("utf-8", "replace").strip().splitlines()[-12:]
        raise SystemExit("[ffmpeg 실패]\n" + "\n".join(tail))
    if not quiet:
        print("  ok:", " ".join(cmd[:3]), "...")


def media_duration(path: str | Path) -> float:
    """ffmpeg -i 의 stderr에서 길이(초)를 읽는다(ffprobe 없이)."""
    res = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    text = res.stderr.decode("utf-8", "replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", text)
    if not m:
        return 0.0
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s


# ---------------------------------------------------------------------------
# 폰트 — 한글·일본어·영문 글리프를 모두 그릴 수 있는 폰트 자동 선택 (2026-11-28 패치)
#
#   증상: 일본어 자막의 장음 'ー'(U+30FC) 등이 □(두부)로 깨짐 (예: 硬質ケ□ス & シ□ル)
#   원인: 한글 폰트(맑은 고딕)만 먼저 쓰고, 그 폰트에 없는 글자를 다른 폰트로
#         넘겨 그리는 처리가 없었음.
#   수정: ①글자 종류를 보고 일본어(가나)가 있으면 일본어 폰트를 먼저 사용
#         ②폰트 파일의 문자표(cmap)를 직접 읽어 '이 글자를 실제로 그릴 수 있는지' 판정
#         ③한 폰트에 없는 글자는 글자 단위로 다음 폰트에 넘겨 그린다(FontStack)
# ---------------------------------------------------------------------------
_KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f]")   # 가나 + 장음 'ー' + 반각 가나
_HANGUL_RE = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]")
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

_WIN_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

# (폰트 파일, 굵은 글꼴 여부) — 없는 파일은 자동으로 건너뛴다.
# Windows 10/11 기본 설치: Yu Gothic 계열 / 선택적 기능(일본어 보충 글꼴): Meiryo·MS Gothic
_FONT_JP: list[tuple[Path, bool]] = [
    (_WIN_FONTS / "meiryob.ttc", True),
    (_WIN_FONTS / "meiryo.ttc", False),
    (_WIN_FONTS / "YuGothB.ttc", True),
    (_WIN_FONTS / "YuGothM.ttc", False),
    (_WIN_FONTS / "YuGothR.ttc", False),
    (_WIN_FONTS / "BIZ-UDGothicB.ttc", True),
    (_WIN_FONTS / "BIZ-UDGothicR.ttc", False),
    (_WIN_FONTS / "msgothic.ttc", False),
    (_WIN_FONTS / "msmincho.ttc", False),
    (Path("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"), False),
    (Path("/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"), False),
    (Path("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"), False),
    (Path("/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"), True),
]

_FONT_KR: list[tuple[Path, bool]] = [
    (_WIN_FONTS / "malgunbd.ttf", True),
    (_WIN_FONTS / "malgun.ttf", False),
    (_WIN_FONTS / "NanumGothicBold.ttf", True),
    (_WIN_FONTS / "NanumGothic.ttf", False),
    (_WIN_FONTS / "H2HDRM.TTF", True),
    (_WIN_FONTS / "gulim.ttc", False),
    (_WIN_FONTS / "batang.ttc", False),
    (Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"), False),
    (Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"), True),
    (Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"), False),
]

# 한글·일본어·중국어를 한 파일에 담은 통합 폰트(있으면 최후의 보루로)
_FONT_ANY: list[tuple[Path, bool]] = [
    (Path("/usr/share/fonts/pickle-hwp/NotoSansCJKkr-Regular.otf"), False),
    (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), False),
    (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"), True),
    (_WIN_FONTS / "NotoSansCJKjp-Regular.otf", False),
    (_WIN_FONTS / "NotoSansCJKkr-Regular.otf", False),
    (Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"), False),
    (_WIN_FONTS / "seguisb.ttf", True),
    (_WIN_FONTS / "arial.ttf", False),
    (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), False),
]


def _script_key(text: str) -> str:
    """이 글자 덩어리를 어떤 폰트로 먼저 그릴지: 일본어(가나) > 한글 > 한자 > 한글."""
    if _KANA_RE.search(text or ""):
        return "jp"
    if _HANGUL_RE.search(text or ""):
        return "kr"
    if _HAN_RE.search(text or ""):
        return "jp"
    return "kr"


def _ordered(group: list[tuple[Path, bool]], bold: bool) -> list[tuple[Path, bool]]:
    """굵게/보통 요청에 맞는 파일을 앞으로."""
    return [item for _, item in sorted(enumerate(group), key=lambda p: (p[1][1] != bold, p[0]))]


def _font_chain(script: str, bold: bool, override: str | None = None) -> list[tuple[Path, bool]]:
    """텍스트 종류에 따른 폰트 우선순위 목록."""
    first, second = (_FONT_JP, _FONT_KR) if script == "jp" else (_FONT_KR, _FONT_JP)
    chain: list[tuple[Path, bool]] = []
    if override:
        chain.append((Path(override), bold))
    for group in (first, second, _FONT_ANY):
        chain += _ordered(group, bold)
    seen: set[str] = set()
    out: list[tuple[Path, bool]] = []
    for path, is_bold in chain:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((path, is_bold))
    return out


@lru_cache(maxsize=64)
def _read_cmap(path: Path, index: int = 0) -> tuple[tuple[int, int], ...] | None:
    """폰트 파일의 문자표(cmap)를 직접 읽어 '그릴 수 있는 유니코드 구간'을 돌려준다.
    외부 라이브러리 없이 TTF/OTF/TTC의 format 4·6·12 표를 해석한다. 실패하면 None."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    try:
        base = 0
        if data[:4] == b"ttcf":
            count = int.from_bytes(data[8:12], "big")
            if not 0 <= index < count:
                return None
            base = int.from_bytes(data[12 + 4 * index:16 + 4 * index], "big")
        num_tables = int.from_bytes(data[base + 4:base + 6], "big")
        cmap_off = 0
        for i in range(num_tables):
            rec = base + 12 + 16 * i
            if data[rec:rec + 4] == b"cmap":
                cmap_off = int.from_bytes(data[rec + 8:rec + 12], "big")
                break
        if not cmap_off:
            return None
        spans: list[tuple[int, int]] = []
        n = int.from_bytes(data[cmap_off + 2:cmap_off + 4], "big")
        for i in range(n):
            rec = cmap_off + 4 + 8 * i
            sub = cmap_off + int.from_bytes(data[rec + 4:rec + 8], "big")
            fmt = int.from_bytes(data[sub:sub + 2], "big")
            if fmt == 4:
                seg2 = int.from_bytes(data[sub + 6:sub + 8], "big")
                seg = seg2 // 2
                ends = sub + 14
                starts = ends + seg2 + 2
                for k in range(seg):
                    s = int.from_bytes(data[starts + 2 * k:starts + 2 * k + 2], "big")
                    e = int.from_bytes(data[ends + 2 * k:ends + 2 * k + 2], "big")
                    if s != 0xFFFF and s <= e:
                        spans.append((s, e))
            elif fmt == 12:
                groups = int.from_bytes(data[sub + 12:sub + 16], "big")
                for k in range(groups):
                    g = sub + 16 + 12 * k
                    s = int.from_bytes(data[g:g + 4], "big")
                    e = int.from_bytes(data[g + 4:g + 8], "big")
                    if s <= e:
                        spans.append((s, e))
            elif fmt == 6:
                first = int.from_bytes(data[sub + 6:sub + 8], "big")
                cnt = int.from_bytes(data[sub + 8:sub + 10], "big")
                if cnt:
                    spans.append((first, first + cnt - 1))
        if not spans:
            return None
        spans.sort()
        merged: list[tuple[int, int]] = []
        for s, e in spans:
            if merged and s <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        return tuple(merged)
    except (IndexError, ValueError):
        return None


class _Face:
    """폰트 파일 하나 — 문자표와 폰트 객체는 실제로 필요할 때만 읽는다(메모리 절약)."""
    __slots__ = ("path", "size", "is_bold", "_p", "_spans", "_spans_loaded", "_font", "_dead",
                 "_cache", "_notdef")

    def __init__(self, path, size: int, is_bold: bool):
        self._p = Path(path)
        self.path = str(path)
        self.size = int(size)
        self.is_bold = bool(is_bold)
        self._spans = None
        self._spans_loaded = False
        self._font = None
        self._dead = False
        self._cache: dict[str, bool] = {}
        self._notdef = None

    @property
    def spans(self):
        if not self._spans_loaded:
            self._spans_loaded = True
            self._spans = None if self.path == "<default>" else _read_cmap(self._p)
        return self._spans

    @property
    def font(self):
        """FreeType 폰트 객체(지연 로딩). 실패하면 None."""
        if self._font is None and not self._dead:
            if self.path == "<default>":
                self._font = ImageFont.load_default(self.size)
            else:
                self._font = _ft(self._p, self.size)
            if self._font is None:
                self._dead = True
        return self._font

    def _sig(self, ch: str):
        try:
            mask = self.font.getmask(ch)
            return (mask.size[0], mask.size[1], mask.tobytes())
        except Exception:
            return None

    def _is_tofu(self, ch: str) -> bool:
        """문자표에는 있는데 실제로는 .notdef(□)로 그려지는 글자인지 확인."""
        if ch.isspace() or not ch.isprintable():
            return False
        if "\u25a0" <= ch <= "\u25ff":   # ■□▲ 같은 도형 문자는 두부와 모양이 같아 오판 위험
            return False
        if self._notdef is None:
            self._notdef = self._sig("\ue000") or False   # 사용자 영역 문자 → 사실상 .notdef
        if not self._notdef:
            return False
        return self._sig(ch) == self._notdef

    def covers(self, ch: str) -> bool:
        hit = self._cache.get(ch)
        if hit is not None:
            return hit
        cp = ord(ch)
        spans = self.spans
        ok = True
        if spans is not None:
            i = bisect.bisect_right(spans, (cp, 0x10FFFF)) - 1
            ok = i >= 0 and spans[i][0] <= cp <= spans[i][1]
        if ok:
            if self.font is None:          # 파일이 깨졌거나 못 열면 이 폰트는 건너뛴다
                ok = False
            else:
                ok = not self._is_tofu(ch)
        self._cache[ch] = ok
        return ok


class FontStack:
    """한 덩어리의 글자를 여러 폰트로 나눠 그리는 도구(글자 단위 폴백).
    폰트에 없는 글자(예: 맑은 고딕의 'ー')는 다음 폰트로 자동으로 넘어간다."""
    __slots__ = ("size", "bold", "faces", "_pick")

    def __init__(self, size: int, bold: bool, faces: list[_Face]):
        self.size = size
        self.bold = bold
        self.faces = faces
        self._pick: dict[str, _Face | None] = {}

    def face_for(self, ch: str):
        if ch in self._pick:
            return self._pick[ch]
        chosen = None
        for face in self.faces:
            if face.covers(ch):
                chosen = face
                break
        if chosen is None and self.faces:
            chosen = self.faces[0]      # 마지막 수단: 첫 폰트로 그대로
        self._pick[ch] = chosen
        return chosen

    def runs(self, text: str) -> list[tuple[_Face, str]]:
        """폰트가 바뀌는 지점에서 자른 (폰트, 글자덩어리) 목록."""
        out: list[tuple[_Face, str]] = []
        for ch in text or "":
            face = self.face_for(ch)
            if face is None:
                continue
            if out and out[-1][0] is face:
                out[-1] = (face, out[-1][1] + ch)
            else:
                out.append((face, ch))
        return out

    def getlength(self, text: str) -> float:
        total = 0.0
        for face, part in self.runs(text):
            ft = face.font
            if ft is not None:
                total += ft.getlength(part)
        return total

    def missing(self, text: str) -> list[str]:
        """어떤 후보 폰트로도 그릴 수 없는 글자 목록(진단용)."""
        return [ch for ch in dict.fromkeys(text or "")
                if not any(face.covers(ch) for face in self.faces)]

    def draw(self, draw: ImageDraw.ImageDraw, xy, text: str, fill,
             stroke_width: int = 0, stroke_fill=None) -> None:
        x, y = xy
        for face, part in self.runs(text):
            ft = face.font
            if ft is None:
                continue
            draw.text((x, y), part, font=ft, fill=fill,
                      stroke_width=stroke_width, stroke_fill=stroke_fill)
            x += ft.getlength(part)


_FT_CACHE: dict[tuple[str, int, int], ImageFont.FreeTypeFont | None] = {}
_STACK_CACHE: dict[tuple[int, bool, str, str], FontStack] = {}
_FONT_OVERRIDE = ""          # --font 로 특정 글꼴을 강제 지정
_FONT_REPORTED: set[str] = set()


def _ft(path: Path, size: int, index: int = 0):
    key = (str(path), size, index)
    if key not in _FT_CACHE:
        try:
            _FT_CACHE[key] = ImageFont.truetype(str(path), size, index=index)
        except Exception:
            _FT_CACHE[key] = None
    return _FT_CACHE[key]


def _load_faces(size: int, bold: bool, script: str) -> list[_Face]:
    faces: list[_Face] = []
    for path, is_bold in _font_chain(script, bold, _FONT_OVERRIDE or None):
        try:
            if not path.exists():
                continue
        except OSError:
            continue
        faces.append(_Face(path, size, is_bold))
    if not faces:      # 폰트를 하나도 못 찾았을 때: 최소한 기본 폰트로라도 동작
        faces.append(_Face("<default>", size, bool(bold)))
    return faces


def font(size: int, bold: bool = False, text: str = "") -> FontStack:
    """텍스트에 맞는 폰트 묶음(FontStack). 같은 (크기·굵기·글자종류)는 재사용."""
    script = _script_key(text)
    key = (size, bool(bold), script, _FONT_OVERRIDE)
    stack = _STACK_CACHE.get(key)
    if stack is None:
        stack = FontStack(size, bool(bold), _load_faces(size, bool(bold), script))
        _STACK_CACHE[key] = stack
    if script not in _FONT_REPORTED:
        _FONT_REPORTED.add(script)
        names = ", ".join(Path(f.path).name for f in stack.faces[:3])
        more = f" 외 {len(stack.faces) - 3}개" if len(stack.faces) > 3 else ""
        print(f"  [폰트] {script.upper()} 글자 → {names}{more}")
    miss = stack.missing(text)
    if miss:
        print(f"  [폰트 경고] 그릴 수 없는 글자 {len(miss)}자: {' '.join(miss)} (폰트 추가 필요)")
    return stack


def find_font(bold: bool = False, text: str = "") -> str | None:
    """(호환용) 가장 먼저 쓰는 폰트 파일 경로."""
    stack = font(64, bold, text)
    return stack.faces[0].path if stack.faces else None


def wrap_lines(text: str, f: FontStack, max_w: int) -> list[str]:
    """한글/영문 혼용 자동 줄바꿈(단어 우선, 넘치면 글자 단위)."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    words = text.split(" ")
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if f.getlength(trial) <= max_w or not cur:
            cur = trial
            # 한 단어가 한 줄을 넘는 경우 글자로 쪼갠다
            while f.getlength(cur) > max_w and len(cur) > 1:
                cut = len(cur)
                while cut > 1 and f.getlength(cur[:cut]) > max_w:
                    cut -= 1
                lines.append(cur[:cut])
                cur = cur[cut:]
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit_text(text: str, max_w: int, max_lines: int, start: int, low: int, bold: bool):
    """주어진 폭/줄 수에 맞는 최대 글자 크기와 줄 목록을 찾는다."""
    size = start
    while size >= low:
        f = font(size, bold, text)
        lines = wrap_lines(text, f, max_w)
        if len(lines) <= max_lines and all(f.getlength(l) <= max_w for l in lines):
            return f, lines
        size -= 4
    f = font(low, bold, text)
    return f, wrap_lines(text, f, max_w)


# ----------------------------------------------------------------------------
# 배경 / 텍스트 레이어
# ----------------------------------------------------------------------------
def cover_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    """비율 유지하며 꽉 채우기(중앙 크롭)."""
    img = img.convert("RGB")
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = max(w, int(iw * scale + 0.5)), max(h, int(ih * scale + 0.5))
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def gradient_bg(w: int, h: int, theme: dict) -> Image.Image:
    """세로 그라데이션 + 은은한 광원 + 비네팅."""
    base = Image.new("RGB", (1, h))
    px = base.load()
    c1, c2 = theme["top"], theme["bottom"]
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    base = base.resize((w, h), Image.BILINEAR)

    # 좌상단 광원
    glow = Image.new("L", (w // 6, h // 6), 0)
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-w // 20, -h // 24, w // 5, h // 9], fill=120)
    glow = glow.resize((w, h), Image.BILINEAR).filter(ImageFilter.GaussianBlur(w / 14))
    lit = Image.new("RGB", (w, h), tuple(min(255, int(c * 1.7) + 24) for c in theme["accent"]))
    base = Image.composite(lit, base, glow.point(lambda v: int(v * 0.55)))

    # 비네팅
    vig = Image.new("L", (w // 6, h // 6), 255)
    vd = ImageDraw.Draw(vig)
    vd.ellipse([-w // 30, -h // 40, w // 5, h // 7], fill=0)
    vig = vig.resize((w, h), Image.BILINEAR).filter(ImageFilter.GaussianBlur(w / 20))
    dark = Image.new("RGB", (w, h), (0, 0, 0))
    base = Image.composite(dark, base, vig.point(lambda v: int(v * 0.85)))
    return base


def make_background(scene: dict, theme: dict, out_png: Path, dim: float = 0.88,
                    title_bottom: float = 0.28, sub_top: float = 0.72) -> None:
    w, h = W * SS, H * SS
    src = scene.get("image")
    if src and Path(src).exists():
        bg = cover_crop(Image.open(src), w, h)
        # 사진 위 글자가 읽히도록 상단·하단을 부드러운 그라데이션으로 어둡게(가운데는 원본 유지)
        scrim = Image.new("L", (1, h))
        sp = scrim.load()

        def band(t: float, full_until: float, zero_at: float) -> float:
            if t <= full_until:
                return 1.0
            if t >= zero_at:
                return 0.0
            x = (t - full_until) / max(1e-6, zero_at - full_until)
            return 1.0 - (x * x * (3 - 2 * x))  # smoothstep

        # 글자 영역(타이틀 아래·자막 위)에 맞춰 어둡게 할 구간을 잡는다
        top_full = max(0.24, min(0.55, title_bottom + 0.02))
        bot_full = 1 - max(0.24, min(0.55, 1 - sub_top + 0.02))
        for y in range(h):
            t = y / (h - 1)
            v = dim * band(t, top_full, top_full + 0.22) + dim * band(1 - t, 1 - bot_full, 1 - bot_full + 0.22)
            sp[0, y] = int(255 * min(dim + 0.05, v))
        scrim = scrim.resize((w, h), Image.BILINEAR)
        bg = Image.composite(Image.new("RGB", (w, h), (0, 0, 0)), bg, scrim)
    else:
        bg = gradient_bg(w, h, theme)
    bg.save(out_png)


def _draw_with_shadow(draw: ImageDraw.ImageDraw, xy, text, f: FontStack, fill, stroke_w, stroke_fill,
                      shadow_alpha=170):
    """글자 뒤에 그림자를 깔아 그린다. 폰트가 바뀌는 지점(글자 단위 폴백)마다 따로 그린다."""
    x, y = xy
    runs = f.runs(text)
    if not runs:
        return
    off = max(2, f.size // 20)
    for dx, dy in ((off, off), (off, -off), (-off, off), (-off, -off)):
        cx, cy = x + dx, y + dy
        for face, part in runs:
            ft = face.font
            if ft is None:
                continue
            draw.text((cx, cy), part, font=ft, fill=(0, 0, 0, shadow_alpha),
                      stroke_width=stroke_w, stroke_fill=(0, 0, 0, shadow_alpha))
            cx += ft.getlength(part)
    cx = x
    for face, part in runs:
        ft = face.font
        if ft is None:
            continue
        if f.bold and not face.is_bold:      # 굵은 폰트가 없는 글자는 살짝 겹쳐 그려 굵게
            for bx, by in ((2, 0), (0, 2), (2, 2), (-1, 0), (0, -1)):
                draw.text((cx + bx, y + by), part, font=ft, fill=fill,
                          stroke_width=stroke_w, stroke_fill=stroke_fill)
        draw.text((cx, y), part, font=ft, fill=fill,
                  stroke_width=stroke_w, stroke_fill=stroke_fill)
        cx += ft.getlength(part)


def make_text_layer(scene: dict, theme: dict, out_png: Path) -> dict:
    """투명 PNG 위에 상단 후킹 타이틀 + 하단 자막 + 배지/브랜드를 그린다.
    반환: 배경 어둡게 할 구간 계산용 {'title_bottom': 0~1, 'sub_top': 0~1}"""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    accent = theme["accent"]
    light_text = theme["top"][0] > 200  # 밝은 테마면 글자를 어둡게

    # --- 상단 배지(선택)
    badge = (scene.get("badge") or "").strip()
    y_cursor = 132
    if badge:
        bf = font(44, True, badge)
        bw = bf.getlength(badge) + 64
        bh = 78
        bx = (W - bw) / 2
        draw.rounded_rectangle([bx, y_cursor, bx + bw, y_cursor + bh], radius=39, fill=accent + (255,))
        bf.draw(draw, (bx + 32, y_cursor + 14), badge, (255, 255, 255, 255))
        y_cursor += bh + 34

    # --- 후킹 타이틀
    title = (scene.get("title") or "").strip()
    title_box = None
    if title:
        f, lines = fit_text(title, W - 150, 3, 100, 62, True)
        line_h = int(f.size * 1.24)
        # 삐뚤빼뚤한 형광펜 밑줄 대신 타이틀 위 얇은 액센트 바
        draw.rounded_rectangle([W / 2 - 46, y_cursor, W / 2 + 46, y_cursor + 10], radius=5,
                               fill=accent + (255,))
        y_cursor += 40
        top_edge = y_cursor
        left_edge, right_edge = W, 0
        for line in lines:
            tw = f.getlength(line)
            left_edge, right_edge = min(left_edge, (W - tw) / 2), max(right_edge, (W + tw) / 2)
            _draw_with_shadow(draw, ((W - tw) / 2, y_cursor), line, f,
                              (255, 255, 255, 255) if not light_text else (20, 22, 28, 255),
                              max(3, f.size // 26), (0, 0, 0, 210) if not light_text else (255, 255, 255, 230))
            y_cursor += line_h
        title_box = (left_edge - 46, top_edge - 24, right_edge + 46, y_cursor - line_h + f.size + 26)

    # --- 하단 자막 박스
    sub = (scene.get("subtitle") or "").strip()
    sub_box = None
    if sub:
        sf, slines = fit_text(sub, W - 220, 3, 54, 34, False)
        lh = int(sf.size * 1.34)
        pad_x, pad_y = 44, 32
        box_w = max(sf.getlength(l) for l in slines) + pad_x * 2
        box_h = lh * len(slines) + pad_y * 2
        bx = (W - box_w) / 2
        by = H - 300 - box_h
        draw.rounded_rectangle([bx, by, bx + box_w, by + box_h], radius=26, fill=(12, 12, 16, 205))
        draw.rounded_rectangle([bx, by, bx + 8, by + box_h], radius=4, fill=accent + (255,))
        sub_box = (bx, by, bx + box_w, by + box_h)
        ty = by + pad_y
        for line in slines:
            sf.draw(draw, ((W - sf.getlength(line)) / 2, ty), line, (255, 255, 255, 255))
            ty += lh

    # --- 브랜드 워터마크
    brand = (scene.get("brand") or "").strip()
    if brand:
        bf2 = font(38, True, brand)
        bf2.draw(draw, (W - bf2.getlength(brand) - 52, H - 132), brand,
                 (255, 255, 255, 165), stroke_width=2, stroke_fill=(0, 0, 0, 120))

    # 글자 주변을 은은하게 어둡게(배경이 밝아도 읽히도록)
    alpha = layer.split()[3]
    halo = alpha.filter(ImageFilter.GaussianBlur(26)).point(lambda v: int(v * 0.72))
    backdrop = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    backdrop.putalpha(halo)
    layer = Image.alpha_composite(backdrop, layer)

    layer.save(out_png)
    return {
        "title_bottom": (title_box[3] / H) if title_box else 0.22,
        "sub_top": (sub_box[1] / H) if sub_box else 0.72,
    }


# ---------------------------------------------------------------------------
# 나레이션 보이스 — 일본어 문장은 일본어 보이스로 (2026-11-28 패치)
#
#   증상: 일본어 나레이션에도 기본 한국어 보이스(ko-KR-SunHiNeural)가 쓰여
#         유튜브 자동 자막이 엉뚱한 한국어로 잡히고 발음도 한국어 억양이 됐다.
#   규칙: ①가나가 있거나(한자만으로 된) 일본어 문장 → ja-JP 보이스
#         ②voice 에 "ja-JP-..." 를 직접 지정하면 그 보이스를 그대로
#         ③한국어 문장 → 지정 보이스, 없으면 기본 한국어 보이스
# ---------------------------------------------------------------------------
DEFAULT_VOICE_KR = "ko-KR-SunHiNeural"
DEFAULT_VOICE_JA = "ja-JP-NanamiNeural"
_VOICE_AUTO_WORDS = ("", "auto", "default", "자동")


def wants_japanese(text: str) -> bool:
    """이 문장을 일본어로 읽어야 하는지(가나가 있거나, 한글 없이 한자만)."""
    t = text or ""
    if _KANA_RE.search(t):
        return True
    return bool(_HAN_RE.search(t)) and not _HANGUL_RE.search(t)


def pick_voice(text: str, requested: str = "", ja_voice: str = DEFAULT_VOICE_JA,
               kr_voice: str = DEFAULT_VOICE_KR) -> str:
    """나레이션 문장에 맞는 edge-tts 보이스를 고른다."""
    req = (requested or "").strip()
    if req.lower() in _VOICE_AUTO_WORDS:
        req = ""
    if wants_japanese(text):
        if req.lower().startswith("ja-"):
            return req
        return ja_voice or DEFAULT_VOICE_JA     # ko 보이스가 지정돼 있어도 일본어면 ja 로
    return req or kr_voice or DEFAULT_VOICE_KR


# ----------------------------------------------------------------------------
# 음성
# ----------------------------------------------------------------------------
def tts(text: str, out_mp3: Path, voice: str, rate: str = "+0%") -> bool:
    """edge-tts 나레이션 생성. 실패(오프라인 등)하면 False → 무음 폴백."""
    if not text.strip():
        return False
    try:
        import asyncio

        import edge_tts

        async def _go():
            comm = edge_tts.Communicate(text, voice, rate=rate)
            await comm.save(str(out_mp3))

        asyncio.run(_go())
        return out_mp3.exists() and out_mp3.stat().st_size > 1000
    except Exception as e:  # noqa: BLE001
        print(f"  [안내] TTS 실패 → 무음으로 진행 ({type(e).__name__}: {e})")
        return False


# ----------------------------------------------------------------------------
# 장면 렌더
# ----------------------------------------------------------------------------
def render_scene(scene: dict, idx: int, workdir: Path, theme: dict, opts: argparse.Namespace) -> Path:
    bg_png = workdir / f"bg_{idx:02d}.png"
    tx_png = workdir / f"tx_{idx:02d}.png"
    info = make_text_layer(scene, theme, tx_png)
    make_background(scene, theme, bg_png, float(scene.get("dim") or opts.dim),
                    info["title_bottom"], info["sub_top"])

    narration = (scene.get("narration") or "").strip()
    audio: Path | None = None
    if narration:
        mp3 = workdir / f"nar_{idx:02d}.mp3"
        voice = pick_voice(narration, scene.get("voice") or opts.voice,
                           getattr(opts, "voice_ja", DEFAULT_VOICE_JA) or DEFAULT_VOICE_JA)
        if tts(narration, mp3, voice, scene.get("rate") or opts.rate):
            audio = mp3
            print(f"    [나레이션] {voice}{' (일본어 자동)' if wants_japanese(narration) else ''}")

    dur = float(scene.get("seconds") or opts.seconds or 12)
    if audio is not None:
        dur = max(3.0, media_duration(audio) + 0.6)
    dur = round(dur, 2)

    # Ken Burns 식(부드러운 줌/팬)
    n = max(1, int(dur * FPS))
    zmax = float(scene.get("zoom") or opts.zoom)
    motion = (scene.get("motion") or opts.motion).lower()
    if motion == "out":
        zexpr = f"max(1.001,{zmax:.4f}-{zmax - 1:.4f}*on/{n})"
        xexpr, yexpr = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif motion == "pan":
        zexpr = f"{zmax:.4f}"
        xexpr = f"(iw-iw/zoom)*(0.22+0.56*on/{n})"
        yexpr = "ih/2-(ih/zoom/2)"
    else:  # in
        zexpr = f"min({zmax:.4f},1.0+{zmax - 1:.4f}*on/{n})"
        xexpr, yexpr = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"

    out = workdir / f"seg_{idx:02d}.mp4"
    cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
           "-loop", "1", "-framerate", str(FPS), "-i", str(bg_png),
           "-loop", "1", "-framerate", str(FPS), "-i", str(tx_png)]
    if audio is not None:
        cmd += ["-i", str(audio)]
        amap = "2:a"
        afilter = "aresample=async=1:first_pts=0,apad"  # 나레이션이 짧으면 무음으로 채움
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        amap = "2:a"
        afilter = None  # 무음 소스는 필터가 필요 없다

    fc = (
        f"[0:v]zoompan=z='{zexpr}':x='{xexpr}':y='{yexpr}':d=1:s={W}x{H}:fps={FPS}[bg];"
        f"[1:v]format=rgba,fade=t=in:st=0.12:d=0.42:alpha=1[txt];"
        f"[bg][txt]overlay=0:0,format=yuv420p[v]"
    )
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", amap, "-t", f"{dur}"]
    if afilter:
        cmd += ["-af", afilter]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p",
            "-r", str(FPS), "-g", str(FPS * 2),
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart", str(out)]
    print(f"  장면 {idx + 1}: {dur:.1f}초 렌더링{' (나레이션)' if audio else ' (무음)'}")
    run(cmd)
    return out


def concat(parts: list[Path], out: Path, workdir: Path) -> None:
    lst = workdir / "list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    run([ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", "-movflags", "+faststart", str(out)])


def add_music(video: Path, music: Path, out: Path, volume: float = 0.18) -> None:
    """완성본에 배경음악을 낮은 볼륨으로 깔아준다(영상은 재인코딩 안 함)."""
    run([ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(video), "-stream_loop", "-1", "-i", str(music),
         "-filter_complex",
         f"[1:a]volume={volume},afade=t=out:st={max(0.0, media_duration(video) - 1.2):.2f}:d=1.2[bg];"
         f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
         "-shortest", "-movflags", "+faststart", str(out)])


# ----------------------------------------------------------------------------
# 입력 처리
# ----------------------------------------------------------------------------
def scenes_from_args(a: argparse.Namespace) -> list[dict]:
    if a.script:
        data = json.loads(Path(a.script).read_text(encoding="utf-8"))
        scenes = data["scenes"] if isinstance(data, dict) else data
        if not isinstance(scenes, list) or not scenes:
            raise SystemExit("[오류] 시나리오 JSON에 scenes 배열이 없습니다.")
        return scenes
    scene = {
        "title": a.title, "subtitle": a.subtitle, "image": a.image,
        "narration": a.narration, "badge": a.badge, "brand": a.brand,
        "seconds": a.seconds, "motion": a.motion, "zoom": a.zoom,
    }
    return [{k: v for k, v in scene.items() if v not in (None, "")} or {"title": "쇼츠"}.copy()]


def interactive(default_image: str = "") -> list[dict]:
    print("=" * 60)
    print(" 쇼츠 만들기 — 물어보는 대로 입력하고 Enter")
    print("=" * 60)
    if default_image:
        img = default_image
        print(f"상품 사진: {img} (끌어다 놓은 파일)")
    else:
        img = input("상품 사진 경로 (없으면 Enter → 그라데이션 배경): ").strip().strip('"')
    title = input("후킹 타이틀 (예: 이거 모르면 손해): ").strip()
    sub = input("하단 자막 (예: 오늘만 12,900원): ").strip()
    nar = input("나레이션 문장 (없으면 Enter → 무음): ").strip()
    sec = input("길이(초) [기본 12]: ").strip()
    scenes = [{"title": title or "오늘의 추천템", "subtitle": sub, "image": img, "narration": nar}]
    if sec.isdigit():
        scenes[0]["seconds"] = int(sec)
    return scenes


def main() -> None:
    p = argparse.ArgumentParser(description="1080x1920 쇼츠 자동 조립기")
    p.add_argument("--script", help="장면 목록 JSON (scenes 배열)")
    p.add_argument("--image", help="상품 사진 경로")
    p.add_argument("--title", default="", help="상단 후킹 타이틀")
    p.add_argument("--subtitle", default="", help="하단 자막")
    p.add_argument("--narration", default="", help="나레이션 문장(edge-tts)")
    p.add_argument("--badge", default="", help="상단 배지 문구 (예: 오늘의 핫딜)")
    p.add_argument("--brand", default="", help="우하단 워터마크")
    p.add_argument("--voice", default="",
                   help="edge-tts 음성 — 비우면 자동(일본어 문장 → ja-JP-NanamiNeural, 그 외 → ko-KR-SunHiNeural)")
    p.add_argument("--voice-ja", default=DEFAULT_VOICE_JA,
                   help=f"일본어 나레이션 보이스 (기본 {DEFAULT_VOICE_JA})")
    p.add_argument("--rate", default="+0%", help="말 빠르기 (예: +8%%)")
    p.add_argument("--seconds", type=float, default=12, help="장면 길이(초)")
    p.add_argument("--zoom", type=float, default=1.14, help="줌 배율")
    p.add_argument("--motion", default="in", choices=["in", "out", "pan"])
    p.add_argument("--theme", default="dark", choices=list(THEMES))
    p.add_argument("--dim", type=float, default=0.88, help="사진 배경 위/아래 어둡게(0~1)")
    p.add_argument("--music", help="배경음악 파일(mp3/wav) — 전체에 깔림")
    p.add_argument("--out", default="", help="출력 MP4 경로")
    p.add_argument("--interactive", action="store_true", help="대화형 입력")
    p.add_argument("--keep", action="store_true", help="중간 파일 보존")
    p.add_argument("--font", default="",
                   help="글꼴 파일 강제 지정 (예: C:\\Windows\\Fonts\\meiryob.ttc) — 일본어·한글 자동 선택을 무시")
    a = p.parse_args()

    global _FONT_OVERRIDE
    _FONT_OVERRIDE = (a.font or "").strip()

    scenes = interactive(a.image or "") if a.interactive else scenes_from_args(a)
    theme = THEMES[a.theme]

    if a.out:
        out = Path(a.out)
    elif a.script:
        out = Path(Path(a.script).stem + ".mp4")
    else:
        out = Path("shorts_out.mp4")
    if not out.is_absolute():
        out = Path.cwd() / out
    out.parent.mkdir(parents=True, exist_ok=True)

    workdir = Path(tempfile.mkdtemp(prefix="shorts_"))
    try:
        parts = []
        print(f"[1/3] 장면 {len(scenes)}개 준비 (테마={a.theme}, 규격={W}x{H}@{FPS}fps)")
        for i, sc in enumerate(scenes):
            if not sc.get("brand") and a.brand:
                sc["brand"] = a.brand
            parts.append(render_scene(sc, i, workdir, theme, a))

        print("[2/3] 이어붙이기")
        if len(parts) == 1:
            shutil.copyfile(parts[0], out)
        else:
            concat(parts, out, workdir)

        if a.music:
            print("[3/3] 배경음악 믹스")
            mixed = workdir / "mixed.mp4"
            add_music(out, Path(a.music), mixed)
            shutil.move(str(mixed), str(out))
        else:
            print("[3/3] 마무리")

        size = out.stat().st_size / (1024 * 1024)
        print("=" * 60)
        print(f"완성: {out}")
        print(f"길이: {media_duration(out):.1f}초 / 크기: {size:.2f}MB / 해상도: {W}x{H}")
        print("=" * 60)
    finally:
        if a.keep:
            print(f"중간 파일: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
