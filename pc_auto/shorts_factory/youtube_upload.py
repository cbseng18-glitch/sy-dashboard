#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
youtube_upload.py — 유튜브 쇼츠 자동 업로드 (YouTube Data API v3)

처음 한 번만: 같은 폴더에 client_secrets.json 을 넣고 실행하면
브라우저 인증 창이 뜨고, token.json 이 저장되어 다음부터는 자동 로그인됩니다.

사용 예)
  python youtube_upload.py --video sample_shorts.mp4 ^
      --title "12월 달력, 이거 하나면 끝 #Shorts" ^
      --description "미리캔버스 무료 템플릿 3종 정리했어요." ^
      --tags "쇼츠,미리캔버스,12월달력" ^
      --privacy public --pinned-comment "템플릿 링크는 여기 있어요"

  # 예약 공개(한국시간 KST)
  python youtube_upload.py --video a.mp4 --title "..." --publish-at "2026-11-28T19:00:00+09:00"

  # 메타데이터만 검사(실제 업로드 안 함)
  python youtube_upload.py --video a.mp4 --title "..." --dry-run

준비물: pip install google-api-python-client google-auth-oauthlib
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # 썸네일·댓글
]
CATEGORIES = {"22": "People & Blogs", "24": "Entertainment", "26": "Howto & Style",
              "28": "Science & Technology", "20": "Gaming", "10": "Music"}


def die(msg: str) -> None:
    print(f"[오류] {msg}")
    sys.exit(1)


def get_service():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        die("필요 패키지가 없습니다.  pip install google-api-python-client google-auth-oauthlib")

    base = Path(__file__).resolve().parent
    token_path = base / "token.json"
    secret_path = base / "client_secrets.json"

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not secret_path.exists():
                die("client_secrets.json 이 없습니다. 업로드_가이드.txt 의 1~4단계를 먼저 하세요.")
            flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
            creds = flow.run_local_server(port=0, prompt="consent")
        token_path.write_text(creds.to_json(), encoding="utf-8")
        print(f"인증 저장 완료: {token_path}")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def shortsify(title: str, description: str) -> tuple[str, str]:
    """쇼츠로 분류되도록 #Shorts 를 붙인다(이미 있으면 그대로)."""
    if "#shorts" in (title + description).lower():
        return title, description
    title = (title + " #Shorts")[:100]
    desc = description.rstrip() + "\n\n#Shorts"
    return title, desc


def upload(args) -> str:
    video = Path(args.video)
    if not video.exists():
        die(f"영상 파일이 없습니다: {video}")

    title, desc = args.title, args.description
    lang = (args.lang or "ko").strip()[:8] or "ko"      # ja 영상이면 ja — 자동 자막 언어가 틀어지지 않게
    if args.shorts:
        title, desc = shortsify(title, desc)

    body = {
        "snippet": {
            "title": title[:100],
            "description": desc[:5000],
            "tags": [t.strip() for t in args.tags.split(",") if t.strip()][:30],
            "categoryId": args.category,
            "defaultLanguage": lang,
            "defaultAudioLanguage": lang,
        },
        "status": {
            "privacyStatus": "private" if args.publish_at else args.privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    if args.publish_at:
        body["status"]["publishAt"] = args.publish_at

    if args.dry_run:
        print("[검사 모드] 아래 내용으로 업로드될 예정입니다.")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        print(f"파일: {video} ({video.stat().st_size / 1048576:.2f}MB)")
        return ""
    if len(body["snippet"]["tags"]):
        total = sum(len(t) + 1 for t in body["snippet"]["tags"])
        if total > 500:
            print(f"[안내] 태그 길이 합계 {total}자 > 500자 → 뒤쪽 태그는 유튜브가 무시할 수 있습니다.")

    try:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        die("필요 패키지가 없습니다.  python -m pip install -r requirements.txt")

    yt = get_service()
    media = MediaFileUpload(str(video), chunksize=4 * 1024 * 1024, resumable=True,
                            mimetype="video/mp4")
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    video_id, progress = "", 0
    print("업로드 시작…")
    while request is not None:
        try:
            status, response = request.next_chunk()
        except HttpError as e:
            die(f"업로드 실패: {e}")
        if status:
            pct = int(status.progress() * 100)
            if pct >= progress + 10:
                progress = pct
                print(f"  {progress}%")
        if response:
            video_id = response["id"]
            print(f"업로드 완료: https://youtu.be/{video_id}")
            break

    if not video_id:
        return ""

    if args.thumbnail:
        try:
            yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(args.thumbnail)).execute()
            print("썸네일 설정 완료")
        except Exception as e:  # noqa: BLE001
            print(f"[안내] 썸네일 실패(채널 인증 필요할 수 있음): {e}")

    if args.playlist:
        add_to_playlist(yt, args.playlist, video_id)

    if args.pinned_comment:
        add_comment(yt, video_id, args.pinned_comment)

    print(f"\n영상 ID: {video_id}")
    print(f"주소: https://youtu.be/{video_id}")
    print("※ 고정댓글 '핀'은 API로 불가 → 유튜브 스튜디오/앱에서 한 번만 눌러 고정하세요.")
    return video_id


def add_to_playlist(yt, playlist_id: str, video_id: str) -> None:
    try:
        yt.playlistItems().insert(
            part="snippet",
            body={"snippet": {"playlistId": playlist_id,
                              "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
        ).execute()
        print(f"재생목록 추가 완료: {playlist_id}")
    except Exception as e:  # noqa: BLE001
        print(f"[안내] 재생목록 추가 실패: {e}")


def add_comment(yt, video_id: str, text: str) -> None:
    try:
        res = yt.commentThreads().insert(
            part="snippet",
            body={"snippet": {"videoId": video_id,
                              "topLevelComment": {"snippet": {"textOriginal": text}}}},
        ).execute()
        print(f"고정댓글 등록 완료: {res['snippet']['topLevelComment']['id']}")
    except Exception as e:  # noqa: BLE001
        print(f"[안내] 댓글 등록 실패(권한·스팸 정책): {e}")


def main() -> None:
    p = argparse.ArgumentParser(description="유튜브 쇼츠 자동 업로드")
    p.add_argument("--video", required=True, help="업로드할 mp4")
    p.add_argument("--title", required=True, help="제목(100자 이내)")
    p.add_argument("--description", default="", help="설명")
    p.add_argument("--tags", default="", help="쉼표로 구분한 태그")
    p.add_argument("--privacy", default="public", choices=["public", "unlisted", "private"])
    p.add_argument("--publish-at", default="", help="예약 공개 ISO 시각 (예: 2026-11-28T19:00:00+09:00)")
    p.add_argument("--category", default="22", choices=list(CATEGORIES))
    p.add_argument("--lang", default="ko", help="영상 언어 코드 (ja 영상이면 ja — 기본 자막·음성 언어)")
    p.add_argument("--thumbnail", default="", help="썸네일 이미지 경로")
    p.add_argument("--playlist", default="", help="재생목록 ID")
    p.add_argument("--pinned-comment", default="", help="등록할 고정댓글 내용")
    p.add_argument("--no-shorts-hashtag", dest="shorts", action="store_false", help="#Shorts 자동 추가 끄기")
    p.add_argument("--dry-run", action="store_true", help="업로드 없이 메타데이터만 확인")
    args = p.parse_args()

    if len(args.title) > 100:
        print(f"[안내] 제목이 {len(args.title)}자 → 100자로 잘립니다.")
    if args.publish_at and args.privacy != "private":
        print("[안내] 예약 공개는 privacy=private 로 처리됩니다(유튜브 규칙).")
    upload(args)


if __name__ == "__main__":
    main()
