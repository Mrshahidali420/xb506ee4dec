"""Upload a finished recap to one of our YouTube channels (YouTube Data API v3, resumable upload).

Usage:
  python post_yt.py auth <channel>                       one time: log in and pick the channel in the browser
  python post_yt.py <channel> <video.mp4> "<title>" "<description>|@desc.txt" [--tags a,b,c] [--thumb cover.jpg]
                       [--playlist "Name"]... [--public] [--go]

  <channel>  short name, e.g. manhwax; the login is kept in ~/.config/watch/yt-<channel>.json
  --playlist add to the channel playlist with this title (made if missing); repeat for more
  --public   ask for a public video (default: private, to check it in Studio first)
  --go       really upload. Without it, only checks the login and the files (dry run).

The Google app is the "installed" OAuth client at ~/.config/watch/yt_client_secret.json
(or YT_CLIENT_SECRET). Until Google audits that app, every upload it makes is locked to
private: open YouTube Studio and set it to Public by hand.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

CONFIG = Path.home() / ".config/watch"
SCOPES = ["https://www.googleapis.com/auth/youtube"]  # upload, read and manage playlists
VALUE_FLAGS = ("--tags", "--thumb", "--playlist")
CATEGORY = "1"  # Film & Animation
CHUNK = 8 * 1024 * 1024


def token_file(channel: str) -> Path:
    return CONFIG / f"yt-{channel}.json"


def client_file() -> Path:
    path = Path(os.environ.get("YT_CLIENT_SECRET") or CONFIG / "yt_client_secret.json")
    if not path.exists():
        sys.exit(f"missing Google OAuth client file: {path}")
    return path


def login(channel: str):
    """Return a YouTube client from the saved login; refresh it and save it again when it has expired."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    path = token_file(channel)
    if not path.exists():
        sys.exit(f"no login for {channel}: run  python post_yt.py auth {channel}")
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        path.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def auth(channel: str) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_file()), SCOPES)
    # prompt=consent always returns a refresh token, so the robot can keep uploading after this
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    CONFIG.mkdir(parents=True, exist_ok=True)
    token_file(channel).write_text(creds.to_json(), encoding="utf-8")
    print("logged in to:", channel_name(login(channel)))


def channel_name(yt) -> str:
    items = yt.channels().list(part="snippet", mine=True).execute().get("items", [])
    if not items:
        sys.exit("this Google login has no YouTube channel: log in again and pick the channel")
    return f"{items[0]['snippet']['title']} ({items[0]['id']})"


def upload(yt, video: Path, title: str, description: str, tags: list[str], privacy: str) -> dict:
    """Send the file in chunks and return the new video (id + the privacy YouTube really gave it)."""
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {"title": title[:100], "description": description[:5000], "tags": tags, "categoryId": CATEGORY,
                    "defaultLanguage": "en", "defaultAudioLanguage": "en"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "embeddable": True},
    }
    media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=CHUNK, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    done = None
    while done is None:
        status, done = req.next_chunk()
        if status:
            print(f"  sent {status.progress():.0%}", flush=True)
    return done


def add_to_playlist(yt, video_id: str, name: str) -> None:
    """Put the video in the channel's playlist with this exact title; make the playlist (public) if it is new."""
    found = [p["id"] for p in yt.playlists().list(part="snippet", mine=True, maxResults=50).execute().get("items", [])
             if p["snippet"]["title"].strip().lower() == name.strip().lower()]
    if found:
        pid = found[0]
    else:
        pid = yt.playlists().insert(part="snippet,status", body={
            "snippet": {"title": name, "defaultLanguage": "en"}, "status": {"privacyStatus": "public"}}).execute()["id"]
        print(f"made playlist {name!r}")
    yt.playlistItems().insert(part="snippet", body={
        "snippet": {"playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
    print(f"added to playlist {name!r}")


def main() -> None:
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "auth":
        auth(args[1].lower())
        return
    flags: dict[str, list[str]] = {}
    plain = []
    rest = iter(args)
    for a in rest:
        if a in VALUE_FLAGS:
            flags.setdefault(a, []).append(next(rest, ""))
        elif a not in ("--go", "--public"):
            plain.append(a)
    if len(plain) != 4:
        sys.exit(__doc__)
    channel, video, title, description = plain[0].lower(), Path(plain[1]), plain[2], plain[3]
    if description.startswith("@"):  # a long description with line breaks lives in a text file
        description = Path(description[1:]).read_text(encoding="utf-8").strip()
    thumb = Path(flags["--thumb"][-1]) if "--thumb" in flags else None
    tags = [t.strip() for t in ",".join(flags.get("--tags", [])).split(",") if t.strip()]
    playlists = flags.get("--playlist", [])
    for f in (video, thumb):
        if f and not f.exists():
            sys.exit(f"file not found: {f}")
    yt = login(channel)
    print("channel:", channel_name(yt))
    print(f"video: {video.name} ({video.stat().st_size / 1e6:.0f} MB)\ntitle: {title}")
    if "--go" not in sys.argv:
        print("dry run: nothing uploaded")
        return
    privacy = "public" if "--public" in sys.argv else "private"
    done = upload(yt, video, title, description, tags, privacy)
    print(f"uploaded https://youtu.be/{done['id']}  privacy: {done['status']['privacyStatus']}")
    if thumb:
        from googleapiclient.http import MediaFileUpload

        try:  # custom thumbnails need a phone-verified channel; the video is up either way
            yt.thumbnails().set(videoId=done["id"], media_body=MediaFileUpload(str(thumb))).execute()
            print("thumbnail set")
        except Exception as exc:
            print(f"thumbnail failed: {exc}")
    for name in playlists:
        try:  # the video is up either way; a playlist can be fixed in Studio
            add_to_playlist(yt, done["id"], name)
        except Exception as exc:
            print(f"playlist {name!r} failed: {exc}")
    if privacy == "public" and done["status"]["privacyStatus"] != "public":
        print("Google locked it to private (app not audited yet): set it to Public in YouTube Studio")


if __name__ == "__main__":
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    main()
