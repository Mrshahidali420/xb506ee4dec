"""Leave a comment on a Facebook Page post as the Page: python comment_fb.py <page> <post_id> "<text>" """
from __future__ import annotations

import sys

import requests

from post_fb import env_key


def comment(page: str, post_id: str, text: str) -> str:
    token = env_key(f"{page.upper()}_FB_PAGE_TOKEN")
    r = requests.post(f"https://graph.facebook.com/v21.0/{post_id}/comments",
                      data={"message": text, "access_token": token}, timeout=60)
    r.raise_for_status()
    return r.json()["id"]


if __name__ == "__main__":
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    print("comment:", comment(sys.argv[1], sys.argv[2], sys.argv[3]))
