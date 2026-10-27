"""網站更新後，透過 LINE 官方帳號推播今天的小說卡片（含評分按鈕）。"""
import json
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / "state.json"

RATINGS = [(5, "😍 超愛"), (4, "🙂 喜歡"), (3, "😐 普通"), (2, "😕 還好"), (1, "😩 不喜歡")]


def wait_until_live(url, timeout=600):
    """等 GitHub Pages 把新頁面發布出去，避免她點進去看到 404。"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            if requests.get(url, timeout=20).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(20)
    print("⚠️ 等不到網頁上線，仍然發送通知")
    return False


def build_message(state, url):
    ep = next(e for e in state["episodes"] if e["id"] == state["latest"])
    s = state["series"][ep["series"]]
    label = f"《{s['title']}》第 {ep['no']} 集" + ("・完結篇" if ep.get("final") else "")
    rating_buttons = [{
        "type": "button", "style": "secondary", "height": "sm", "margin": "sm",
        "action": {"type": "postback", "label": name,
                   "data": f"type=rate&ep={ep['id']}&score={score}", "displayText": f"{name}！"},
    } for score, name in RATINGS]
    bubble = {
        "type": "bubble",
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": [
            {"type": "text", "text": f"📖 {config.SITE_TITLE}・{s['genre']}", "size": "xs", "color": "#b4532a", "weight": "bold"},
            {"type": "text", "text": label, "size": "sm", "color": "#7a6f66", "wrap": True},
            {"type": "text", "text": ep["title"], "size": "xl", "weight": "bold", "wrap": True},
            {"type": "text", "text": ep.get("teaser") or s.get("logline", ""), "size": "sm", "wrap": True, "color": "#444444"},
            {"type": "button", "style": "primary", "color": "#b4532a", "margin": "lg",
             "action": {"type": "uri", "label": "開始閱讀", "uri": url}},
        ]},
        "footer": {"type": "box", "layout": "vertical", "contents": [
            {"type": "separator"},
            {"type": "text", "text": "讀完後，今天的故事你喜歡嗎？", "size": "sm", "color": "#7a6f66", "margin": "md", "align": "center"},
            *rating_buttons,
        ]},
    }
    return {"type": "flex", "altText": f"今日小說更新：{label}《{ep['title']}》", "contents": bubble}


def main():
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    site = os.environ.get("SITE_URL", "").rstrip("/")
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    url = f"{site}/ep-{state['latest']}.html"
    msg = build_message(state, url)

    if os.environ.get("DRY_RUN") == "1" or not token:
        print(json.dumps(msg, ensure_ascii=False, indent=2))
        print("（DRY_RUN 或未設定 LINE_CHANNEL_ACCESS_TOKEN，未實際發送）")
        return

    wait_until_live(url)
    r = requests.post(
        "https://api.line.me/v2/bot/message/broadcast",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"messages": [msg]}, ensure_ascii=False).encode("utf-8"),
        timeout=30,
    )
    if r.status_code != 200:
        print(f"❌ LINE 發送失敗：{r.status_code} {r.text}")
        sys.exit(1)
    print("📨 LINE 通知已發送")


if __name__ == "__main__":
    main()
