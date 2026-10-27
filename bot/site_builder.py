"""把 data/ 裡的小說產生成靜態網站（輸出到 docs/，由 GitHub Pages 發布）。"""
import json
from html import escape
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
STORIES = ROOT / "data" / "stories"

CSS = """
:root{
  --bg:#f7f3ec; --paper:#fffdf8; --ink:#2b2622; --muted:#7a6f66; --line:#e6ddd0;
  --accent:#b4532a; --accent-soft:#f3e3d8; --bar:#d98b5f;
  --fs:19px;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#1b1917; --paper:#23201d; --ink:#e9e2d8; --muted:#a39889; --line:#3a342e;
    --accent:#e08a5c; --accent-soft:#3a2a20; --bar:#c0714a;
  }
}
:root[data-theme="dark"]{
  --bg:#1b1917; --paper:#23201d; --ink:#e9e2d8; --muted:#a39889; --line:#3a342e;
  --accent:#e08a5c; --accent-soft:#3a2a20; --bar:#c0714a;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:"Noto Serif TC","Songti TC","PMingLiU",serif;line-height:1.8;-webkit-text-size-adjust:100%}
a{color:var(--accent);text-decoration:none}
.wrap{max-width:720px;margin:0 auto;padding:0 16px}
header.top{border-bottom:1px solid var(--line);background:var(--paper)}
header.top .wrap{display:flex;align-items:center;justify-content:space-between;height:56px;gap:12px}
.brand{font-weight:700;font-size:18px;color:var(--ink)}
.tools{display:flex;gap:6px}
.tools button{font:inherit;font-size:14px;border:1px solid var(--line);background:var(--bg);color:var(--ink);
  border-radius:8px;padding:4px 10px;cursor:pointer}
.sub{color:var(--muted);font-size:15px;margin:28px 0 8px}
.card{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:20px;margin:14px 0}
.hero{border-color:var(--accent);box-shadow:0 2px 0 var(--accent-soft)}
.tag{display:inline-block;font-size:13px;padding:1px 10px;border-radius:99px;background:var(--accent-soft);color:var(--accent);margin-right:6px}
.meta{color:var(--muted);font-size:14px}
h1{font-size:26px;line-height:1.4;margin:6px 0 10px}
h2{font-size:21px;margin:48px 0 18px;text-align:center}
h3{font-size:19px;margin:6px 0}
.btn{display:inline-block;background:var(--accent);color:#fff;padding:10px 22px;border-radius:99px;font-size:16px;margin-top:12px}
.story{font-size:var(--fs)}
.story p{margin:0 0 1.1em;text-indent:2em}
.ending{text-align:center;color:var(--muted);margin:40px 0 10px}
.nav{display:flex;justify-content:space-between;gap:10px;margin:24px 0 48px;font-size:16px}
ul.eps{list-style:none;padding:0;margin:0}
ul.eps li{border-bottom:1px solid var(--line);padding:12px 0}
ul.eps li:last-child{border-bottom:none}
.statrow{display:grid;grid-template-columns:5.5em 1fr 4.5em;align-items:center;gap:10px;margin:8px 0;font-size:15px}
.track{background:var(--bg);border-radius:6px;height:12px;overflow:hidden}
.fill{background:var(--bar);height:100%}
.nums{display:flex;gap:12px;flex-wrap:wrap}
.nums div{flex:1;min-width:120px;text-align:center}
.nums b{display:block;font-size:26px}
footer{color:var(--muted);font-size:13px;text-align:center;padding:30px 0 40px}
"""

JS = """
(function(){
  var r=document.documentElement;
  function get(k){try{return localStorage.getItem(k)}catch(e){return null}}
  function set(k,v){try{localStorage.setItem(k,v)}catch(e){}}
  var fs=parseInt(get('fs')||'19',10); r.style.setProperty('--fs',fs+'px');
  var th=get('theme'); if(th) r.setAttribute('data-theme',th);
  window.fsize=function(d){fs=Math.min(26,Math.max(15,fs+d));r.style.setProperty('--fs',fs+'px');set('fs',fs)};
  window.theme=function(){
    var dark=r.getAttribute('data-theme')==='dark'||(!r.getAttribute('data-theme')&&matchMedia('(prefers-color-scheme: dark)').matches);
    var t=dark?'light':'dark'; r.setAttribute('data-theme',t); set('theme',t)};
})();
"""


def page(title, body, reading=False):
    tools = '<button onclick="fsize(-1)" aria-label="縮小字體">A-</button><button onclick="fsize(1)" aria-label="放大字體">A+</button>' if reading else ""
    return f"""<!doctype html>
<html lang="zh-Hant-TW"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+TC:wght@400;700&display=swap" rel="stylesheet">
<style>{CSS}</style><script>{JS}</script></head>
<body><header class="top"><div class="wrap">
<a class="brand" href="index.html">📖 {escape(config.SITE_TITLE)}</a>
<div class="tools">{tools}<button onclick="theme()" aria-label="切換深淺色">◐</button></div>
</div></header>
<main class="wrap">{body}</main>
<footer>{escape(config.SITE_SUBTITLE)}</footer></body></html>"""


def paragraphs(text):
    return "".join(f"<p>{escape(p.strip())}</p>" for p in text.split("\n") if p.strip())


def build(state):
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / ".nojekyll").write_text("")
    series = state.get("series", {})
    eps = state.get("episodes", [])

    # ---------- 每一集 ----------
    for s in series.values():
        s_eps = [e for e in eps if e["series"] == s["id"]]
        for i, e in enumerate(s_eps):
            f = STORIES / f"{e['id']}.json"
            if not f.exists():
                continue
            story = json.loads(f.read_text(encoding="utf-8"))
            body = [f'<div style="margin-top:28px"><span class="tag">{escape(s["genre"])}</span>'
                    f'<a class="meta" href="series-{s["id"]}.html">《{escape(s["title"])}》</a></div>',
                    f'<h1>第 {e["no"]} 集　{escape(e["title"])}</h1>',
                    f'<div class="meta">{e["date"]}・約 {e.get("chars", 0):,} 字</div><div class="story">']
            for n, ch in enumerate(story["chapters"], 1):
                body.append(f'<h2>{n}・{escape(ch["title"])}</h2>{paragraphs(ch["text"])}')
            body.append("</div>")
            body.append('<p class="ending">— 全書完 —</p>' if e.get("final") else '<p class="ending">— 本集完，明天見 —</p>')
            body.append('<div class="card" style="text-align:center">讀完了嗎？回到 LINE 幫今天的故事打個分數，<br>也可以告訴我明天想看什麼類型 💌</div>')
            prev_l = f'<a href="ep-{s_eps[i-1]["id"]}.html">← 上一集</a>' if i > 0 else "<span></span>"
            next_l = f'<a href="ep-{s_eps[i+1]["id"]}.html">下一集 →</a>' if i + 1 < len(s_eps) else "<span></span>"
            body.append(f'<div class="nav">{prev_l}<a href="series-{s["id"]}.html">目錄</a>{next_l}</div>')
            (DOCS / f"ep-{e['id']}.html").write_text(
                page(f"{s['title']} 第{e['no']}集", "".join(body), reading=True), encoding="utf-8")

        # ---------- 作品目錄 ----------
        items = "".join(
            f'<li><a href="ep-{e["id"]}.html">第 {e["no"]} 集　{escape(e["title"])}</a>'
            f'<div class="meta">{e["date"]}・{escape(e.get("teaser", ""))}</div></li>' for e in s_eps)
        chars = "".join(f'<li><b>{escape(c["name"])}</b>（{escape(c["role"])}）<div class="meta">{escape(c["desc"])}</div></li>'
                        for c in s["bible"].get("characters", []))
        body = (f'<div style="margin-top:28px"><span class="tag">{escape(s["genre"])}</span><span class="meta">{s["status"]}</span></div>'
                f'<h1>{escape(s["title"])}</h1><p>{escape(s.get("logline", ""))}</p>'
                f'<p class="sub">目錄</p><div class="card"><ul class="eps">{items}</ul></div>'
                f'<p class="sub">登場人物</p><div class="card"><ul class="eps">{chars}</ul></div>')
        (DOCS / f"series-{s['id']}.html").write_text(page(s["title"], body), encoding="utf-8")

    # ---------- 首頁 ----------
    body = []
    if eps:
        e = eps[-1]
        s = series[e["series"]]
        body.append(f'<p class="sub">今日更新</p><div class="card hero"><span class="tag">{escape(s["genre"])}</span>'
                    f'<span class="meta">《{escape(s["title"])}》第 {e["no"]} 集</span>'
                    f'<h1>{escape(e["title"])}</h1><div>{escape(e.get("teaser", ""))}</div>'
                    f'<a class="btn" href="ep-{e["id"]}.html">開始閱讀</a></div>')
        body.append('<p class="sub">書架</p>')
        for s in reversed(list(series.values())):
            body.append(f'<a href="series-{s["id"]}.html" style="color:inherit"><div class="card">'
                        f'<span class="tag">{escape(s["genre"])}</span><span class="meta">{s["status"]}・已更新 {s["episodes_written"]}/{s["planned_episodes"]} 集</span>'
                        f'<h3>{escape(s["title"])}</h3><div class="meta">{escape(s.get("logline", ""))}</div></div></a>')
        body.append('<p style="text-align:center;margin:30px 0"><a href="stats.html">📊 喜好分析</a></p>')
    else:
        body.append('<div class="card hero" style="margin-top:28px"><h1>第一個故事正在路上</h1>'
                    '<p>明天早上就會出現在這裡，敬請期待 ✨</p></div>')
    (DOCS / "index.html").write_text(page(config.SITE_TITLE, "".join(body)), encoding="utf-8")

    # ---------- 喜好分析 ----------
    ratings = state.get("ratings", {})
    ep_genre = {e["id"]: e["genre"] for e in eps}
    agg = {}
    for ep_id, sc in ratings.items():
        g = ep_genre.get(ep_id)
        if g:
            agg.setdefault(g, []).append(sc)
    rows = "".join(
        f'<div class="statrow"><span>{escape(g)}</span><div class="track"><div class="fill" style="width:{sum(v)/len(v)/5*100:.0f}%"></div></div>'
        f'<span class="meta">{sum(v)/len(v):.1f} 分・{len(v)}次</span></div>'
        for g, v in sorted(agg.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])))
    total_chars = sum(e.get("chars", 0) for e in eps)
    avg_all = f"{sum(ratings.values())/len(ratings):.1f}" if ratings else "–"
    body = (f'<h1 style="margin-top:28px">喜好分析</h1>'
            f'<div class="card nums"><div><b>{len(eps)}</b><span class="meta">已讀集數</span></div>'
            f'<div><b>{total_chars:,}</b><span class="meta">累積字數</span></div>'
            f'<div><b>{avg_all}</b><span class="meta">平均評分</span></div></div>'
            f'<p class="sub">各類型平均評分（評分越高，之後越常出現）</p>'
            f'<div class="card">{rows or "<span class=meta>還沒有評分，在 LINE 上打分數後這裡就會出現。</span>"}</div>')
    (DOCS / "stats.html").write_text(page("喜好分析", body), encoding="utf-8")


if __name__ == "__main__":
    import generate
    build(generate.load_state())
