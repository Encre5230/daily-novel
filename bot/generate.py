"""
每日小說生成主程式（由 GitHub Actions 每天自動執行）

流程：
  1. 讀取 LINE 上的回饋（評分、明天想看的類型、願望）
  2. 決定今天要「續寫目前的故事」還是「開一部新故事」
  3. 用 Claude 依序產生：故事設定 → 今日大綱 → 3 個章節 → 摘要
  4. 存檔並重新產生網站
"""
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import config  # noqa: E402
import site_builder  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STORIES = DATA / "stories"
STATE_FILE = DATA / "state.json"
TW = timezone(timedelta(hours=8))

FAKE_AI = os.environ.get("FAKE_AI") == "1"   # 測試用：不呼叫 Claude


# ------------------------------------------------------------
#  狀態存取
# ------------------------------------------------------------
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "series": {},          # 所有作品
        "current": None,       # 目前連載中的作品 id
        "episodes": [],        # 所有已發布的集數（新的在後面）
        "ratings": {},         # 集數 id -> 分數 (1~5)
        "last_feedback_ts": 0, # 已處理到的回饋時間
    }


def save_state(state):
    DATA.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------
#  讀取 LINE 回饋（存在 Cloudflare Worker 裡）
# ------------------------------------------------------------
def fetch_feedback():
    url = os.environ.get("FEEDBACK_URL", "").rstrip("/")
    token = os.environ.get("FEEDBACK_TOKEN", "")
    if not url:
        print("（未設定 FEEDBACK_URL，略過讀取回饋）")
        return []
    try:
        r = requests.get(f"{url}/feedback", headers={"Authorization": f"Bearer {token}"}, timeout=30)
        r.raise_for_status()
        return r.json().get("events", [])
    except Exception as e:  # 回饋讀不到不影響今天的小說
        print(f"⚠️ 讀取回饋失敗：{e}")
        return []


def apply_feedback(state, events):
    """把新回饋寫進 state，回傳 (明天類型選擇, 願望清單)。"""
    choice, wishes = None, []
    newest = state.get("last_feedback_ts", 0)
    for ev in sorted(events, key=lambda e: e.get("ts", 0)):
        ts = ev.get("ts", 0)
        if ev.get("type") == "rate" and ev.get("ep"):
            # 評分不限新舊，以最後一次為準
            try:
                state["ratings"][ev["ep"]] = max(1, min(5, int(ev["score"])))
            except (KeyError, ValueError, TypeError):
                pass
        if ts <= state.get("last_feedback_ts", 0):
            continue
        if ev.get("type") == "next":
            choice = ev.get("genre")
        elif ev.get("type") == "wish" and ev.get("text"):
            wishes.append(ev["text"][:200])
        newest = max(newest, ts)
    state["last_feedback_ts"] = newest
    return choice, wishes


# ------------------------------------------------------------
#  喜好分析與類型挑選
# ------------------------------------------------------------
def genre_stats(state):
    """每個類型的平均分數與評分次數。"""
    ep_genre = {e["id"]: e["genre"] for e in state["episodes"]}
    stats = {}
    for ep_id, score in state["ratings"].items():
        g = ep_genre.get(ep_id)
        if not g:
            continue
        s = stats.setdefault(g, {"sum": 0, "count": 0})
        s["sum"] += score
        s["count"] += 1
    return {g: {"avg": round(s["sum"] / s["count"], 2), "count": s["count"]} for g, s in stats.items()}


def pick_random_genre(state):
    """依喜好加權隨機：評分越高的類型越常出現；避開最近兩部的類型。"""
    stats = genre_stats(state)
    recent = [state["series"][sid]["genre"] for sid in list(state["series"])[-2:]]
    weights = {}
    for g in config.GENRES:
        if g in recent:
            continue
        avg = stats.get(g, {}).get("avg")
        weights[g] = 1.0 if avg is None else 0.4 + (avg - 1) * 0.4   # 5 分 → 2.0，1 分 → 0.4
    if not weights:
        weights = {g: 1.0 for g in config.GENRES}
    return random.choices(list(weights), weights=list(weights.values()))[0]


def decide_today(state, choice):
    """回傳 ('continue', series_id) 或 ('new', genre)。"""
    cur = state["series"].get(state.get("current") or "")
    cur_active = cur is not None and cur["episodes_written"] < cur["planned_episodes"]

    if not state["series"]:
        return "new", config.FIRST_GENRE
    if choice in config.GENRES:
        return "new", choice
    if choice == "random":
        return "new", pick_random_genre(state)
    # 沒有指定或指定「繼續」：有進行中的故事就續寫，否則隨機開新的
    if cur_active:
        return "continue", cur["id"]
    return "new", pick_random_genre(state)


# ------------------------------------------------------------
#  Claude
# ------------------------------------------------------------
_client = None


def ask_claude(prompt, max_tokens=4000, system=None):
    global _client
    if FAKE_AI:
        return fake_ai(prompt)
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    system = system or f"你是一位擅長寫連載網路小說的台灣小說家。\n寫作原則：\n{config.WRITING_RULES}"
    for attempt in range(4):
        try:
            msg = _client.messages.create(
                model=config.MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        except Exception as e:
            wait = 20 * (attempt + 1)
            print(f"⚠️ Claude 呼叫失敗（{e}），{wait} 秒後重試")
            time.sleep(wait)
    raise RuntimeError("Claude 連續呼叫失敗")


def ask_json(prompt, max_tokens=3000):
    for _ in range(3):
        text = ask_claude(prompt + "\n\n只輸出 JSON，不要加任何說明或 ``` 符號。", max_tokens)
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        print("⚠️ JSON 解析失敗，重試")
    raise RuntimeError("無法取得 JSON 回應")


def char_count(text):
    return len(re.sub(r"\s", "", text))


# ------------------------------------------------------------
#  寫作步驟
# ------------------------------------------------------------
def create_series(state, genre, wishes):
    n = random.randint(*config.EPISODES_PER_SERIES)
    old_titles = "、".join(s["title"] for s in state["series"].values()) or "（無）"
    wish_text = "\n".join(f"- {w}" for w in wishes) or "（無）"
    bible = ask_json(f"""請為一部全新的「{genre}」連載小說建立故事設定。
類型風格：{config.GENRES[genre]}
預計連載 {n} 集，每集約 {config.CHAPTERS_PER_EPISODE * config.CHAPTER_CHARS} 字。
讀者最近的願望（可參考，不一定要全部採用）：
{wish_text}
請避免和這些舊作品名稱或題材太像：{old_titles}

JSON 格式：
{{
  "title": "書名（簡短有吸引力）",
  "logline": "一句話介紹（40 字內）",
  "world": "世界觀與背景（200 字內）",
  "tone": "文風與氛圍（50 字內）",
  "characters": [{{"name": "姓名", "role": "身分", "desc": "個性、外貌、動機（60 字內）"}}],
  "arc": ["第1集：本集主要事件（40 字內）", "...共 {n} 集，最後一集要完結主線"]
}}""")
    sid = f"s{len(state['series']) + 1:03d}"
    series = {
        "id": sid,
        "genre": genre,
        "title": bible["title"],
        "logline": bible.get("logline", ""),
        "bible": bible,
        "planned_episodes": len(bible.get("arc", [])) or n,
        "episodes_written": 0,
        "notes": "",            # 角色目前狀態等連貫性筆記
        "status": "連載中",
        "started": today_str(),
    }
    # 舊的連載如果沒寫完，標為暫停
    old = state["series"].get(state.get("current") or "")
    if old and old["status"] == "連載中":
        old["status"] = "暫停"
    state["series"][sid] = series
    state["current"] = sid
    print(f"📘 新作品：《{series['title']}》（{genre}，預計 {series['planned_episodes']} 集）")
    return series


def series_context(state, series):
    b = series["bible"]
    chars = "\n".join(f"- {c['name']}（{c['role']}）：{c['desc']}" for c in b.get("characters", []))
    past = [e for e in state["episodes"] if e["series"] == series["id"]]
    past_text = "\n".join(f"第{e['no']}集《{e['title']}》：{e['summary']}" for e in past) or "（這是第一集）"
    return f"""【作品】《{series['title']}》／類型：{series['genre']}
【一句話】{b.get('logline', '')}
【世界觀】{b.get('world', '')}
【文風】{b.get('tone', '')}
【角色】
{chars}
【全書規劃】
{chr(10).join(b.get('arc', []))}
【前情提要】
{past_text}
【連貫性筆記】{series.get('notes') or '（無）'}"""


def write_episode(state, series, wishes):
    ep_no = series["episodes_written"] + 1
    is_final = ep_no >= series["planned_episodes"]
    ctx = series_context(state, series)

    prev = [e for e in state["episodes"] if e["series"] == series["id"]]
    prev_tail = ""
    if prev:
        prev_story = json.loads((STORIES / f"{prev[-1]['id']}.json").read_text(encoding="utf-8"))
        prev_tail = prev_story["chapters"][-1]["text"][-800:]
        last_score = state["ratings"].get(prev[-1]["id"])
    else:
        last_score = None

    extra = []
    if is_final:
        extra.append("這是本作的最終集，請把主線收尾，給一個完整、令人滿足的結局。")
    if last_score is not None:
        extra.append(f"讀者給上一集的評分是 {last_score}/5。" + ("請調整節奏，讓故事更吸引人。" if last_score <= 2 else ""))
    if wishes:
        extra.append("讀者的願望（在不破壞劇情的前提下參考）：" + "；".join(wishes))
    extra_text = "\n".join(extra)

    plan = ask_json(f"""{ctx}

【上一集結尾】
{prev_tail or '（無）'}

現在要寫第 {ep_no} 集（共 {series['planned_episodes']} 集）。
{extra_text}

請規劃這一集，分成 {config.CHAPTERS_PER_EPISODE} 章，每章約 {config.CHAPTER_CHARS} 字。
JSON 格式：
{{
  "episode_title": "本集標題",
  "chapters": [{{"title": "章名", "outline": "這章發生的事（100 字內）"}}]
}}""")

    chapters = []
    for i, ch in enumerate(plan["chapters"][: config.CHAPTERS_PER_EPISODE], start=1):
        written = "\n\n".join(f"【{c['title']}】\n{c['text']}" for c in chapters)
        outline = "\n".join(f"第{j}章《{c['title']}》：{c['outline']}" for j, c in enumerate(plan["chapters"], 1))
        prompt = f"""{ctx}

【上一集結尾】
{prev_tail or '（無）'}

【本集：第 {ep_no} 集《{plan['episode_title']}》大綱】
{outline}
{extra_text}

【本集已寫好的內容】
{written or '（尚未開始）'}

請寫第 {i} 章《{ch['title']}》的正文，約 {config.CHAPTER_CHARS} 字。
直接輸出小說正文，不要寫章名、不要任何說明。"""
        text = ask_claude(prompt, max_tokens=6000)
        if char_count(text) < config.MIN_CHAPTER_CHARS:
            print(f"   第 {i} 章只有 {char_count(text)} 字，要求加長")
            text = ask_claude(prompt + f"\n\n下面是太短的初稿，請擴寫到約 {config.CHAPTER_CHARS} 字，保留原本情節並增加細節與對話：\n\n{text}", max_tokens=6000)
        chapters.append({"title": ch["title"], "text": text})
        print(f"   ✍️ 第 {i} 章《{ch['title']}》完成，{char_count(text)} 字")

    full = "\n\n".join(c["text"] for c in chapters)
    summ = ask_json(f"""以下是小說《{series['title']}》第 {ep_no} 集的全文：

{full}

原本的連貫性筆記：{series.get('notes') or '（無）'}

JSON 格式：
{{
  "summary": "本集劇情摘要，給之後續寫參考（200 字內）",
  "teaser": "給讀者看的一句話預告，吸引人但不劇透結局（35 字內）",
  "notes": "更新後的連貫性筆記：角色目前位置、傷勢、關係、持有物、未解伏筆（250 字內）"
}}""")

    ep_id = datetime.now(TW).strftime("%Y%m%d") + f"-{series['id']}-{ep_no:02d}"
    story = {
        "id": ep_id,
        "series": series["id"],
        "no": ep_no,
        "title": plan["episode_title"],
        "chapters": chapters,
    }
    STORIES.mkdir(parents=True, exist_ok=True)
    (STORIES / f"{ep_id}.json").write_text(json.dumps(story, ensure_ascii=False, indent=2), encoding="utf-8")

    state["episodes"].append({
        "id": ep_id,
        "series": series["id"],
        "genre": series["genre"],
        "no": ep_no,
        "title": plan["episode_title"],
        "summary": summ.get("summary", ""),
        "teaser": summ.get("teaser", ""),
        "date": today_str(),
        "chars": char_count(full),
        "final": is_final,
    })
    series["episodes_written"] = ep_no
    series["notes"] = summ.get("notes", series.get("notes", ""))
    if is_final:
        series["status"] = "已完結"
    print(f"✅ 第 {ep_no} 集《{plan['episode_title']}》完成，共 {char_count(full)} 字")
    return ep_id


def today_str():
    return datetime.now(TW).strftime("%Y-%m-%d")


# ------------------------------------------------------------
#  測試用假 AI
# ------------------------------------------------------------
def fake_ai(prompt):
    if "建立故事設定" in prompt:
        return json.dumps({
            "title": "灰燼之後的晨光", "logline": "城市沉寂後，一位快遞員背著最後的種子穿越廢土。",
            "world": "病毒爆發十年後，城市被藤蔓覆蓋。", "tone": "緊張但溫暖",
            "characters": [{"name": "林曉", "role": "前快遞員", "desc": "堅毅、愛碎念"}],
            "arc": [f"第{i}集：事件{i}" for i in range(1, 4)]}, ensure_ascii=False)
    if "請規劃這一集" in prompt:
        return json.dumps({"episode_title": "廢墟裡的鈴聲", "chapters": [
            {"title": f"第{i}章標題", "outline": "大綱"} for i in range(1, 4)]}, ensure_ascii=False)
    if "連貫性筆記：角色" in prompt:
        return json.dumps({"summary": "林曉找到了信號。", "teaser": "鈴聲響起的那一刻，她知道自己不是唯一的倖存者。",
                           "notes": "林曉右手受傷"}, ensure_ascii=False)
    para = "風從破碎的玻璃窗灌進來，林曉握緊背包的肩帶。「還有多遠？」她低聲問。遠處傳來鈴聲，清脆得不像這個世界該有的聲音。"
    return "\n\n".join([para] * 30)


# ------------------------------------------------------------
def main():
    state = load_state()
    choice, wishes = apply_feedback(state, fetch_feedback())
    print(f"讀者選擇：{choice or '（未指定）'}；願望：{wishes or '（無）'}")

    mode, value = decide_today(state, choice)
    if mode == "new":
        series = create_series(state, value, wishes)
    else:
        series = state["series"][value]
        print(f"📖 續寫《{series['title']}》第 {series['episodes_written'] + 1} 集")

    ep_id = write_episode(state, series, wishes)
    state["latest"] = ep_id
    save_state(state)
    site_builder.build(state)
    print("🌐 網站已更新")


if __name__ == "__main__":
    main()
