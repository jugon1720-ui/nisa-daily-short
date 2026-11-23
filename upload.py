#!/usr/bin/env python3
"""make_short.py が作った動画を YouTube にアップロードする（YouTube Data API v3）。

初回だけ:  .venv/bin/python upload.py --auth   （ブラウザでGoogleアカウントを許可）
通常:      .venv/bin/python upload.py <出力フォルダ>
publish_time（config.json）より前に実行すると「予約公開」、過ぎていれば即公開。
"""
import argparse, datetime as dt, json, os, sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

ROOT = Path(__file__).resolve().parent
SECRETS = ROOT / "secrets"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
JST = dt.timezone(dt.timedelta(hours=9))


def credentials(interactive=False):
    token = SECRETS / "token.json"
    creds = Credentials.from_authorized_user_file(str(token), SCOPES) if token.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not interactive:
            sys.exit("YouTubeの認証がありません。先に `.venv/bin/python upload.py --auth` を実行してください。")
        flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS / "client_secret.json"), SCOPES)
        creds = flow.run_local_server(port=0)
    token.write_text(creds.to_json())
    os.chmod(token, 0o600)
    return creds


def upload(folder, private=False):
    folder = Path(folder)
    done = folder / "uploaded.json"
    if done.exists():
        print("アップロード済み:", done.read_text()); return
    cfg = json.loads((ROOT / "config.json").read_text())
    meta = json.loads((folder / "meta.json").read_text())

    status = {"privacyStatus": "private" if private else cfg.get("privacy_status", "public"),
              "selfDeclaredMadeForKids": False}
    hh, mm = map(int, cfg.get("publish_time", "07:30").split(":"))
    now = dt.datetime.now(JST)
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if status["privacyStatus"] == "public" and at - now > dt.timedelta(minutes=2):
        status = {"privacyStatus": "private", "publishAt": at.isoformat(), "selfDeclaredMadeForKids": False}

    yt = build("youtube", "v3", credentials=credentials(), cache_discovery=False)
    body = {"snippet": {"title": meta["title"], "description": meta["description"], "tags": meta["tags"],
                        "categoryId": "27", "defaultLanguage": "ja", "defaultAudioLanguage": "ja"},
            "status": status}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(meta["video"], mimetype="video/mp4", resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    info = {"id": resp["id"], "url": f"https://youtube.com/shorts/{resp['id']}", "status": status,
            "uploaded_at": now.isoformat()}
    done.write_text(json.dumps(info, ensure_ascii=False, indent=2))
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--auth", action="store_true")
    ap.add_argument("--private", action="store_true", help="テスト用に非公開でアップロード")
    a = ap.parse_args()
    if a.auth:
        SECRETS.mkdir(exist_ok=True)
        credentials(interactive=True); print("認証OK")
    else:
        upload(a.folder, a.private)
