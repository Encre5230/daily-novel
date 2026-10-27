// ============================================================
//  每日小說 LINE 回饋小幫手（貼到 Cloudflare Worker 使用）
//  功能：
//   - 接收評分按鈕、明天想看的類型、她打的任何願望文字
//   - 存起來，讓每天早上的寫作程式讀取
//  需要的設定（Cloudflare 後台設定，不要寫在這裡）：
//   - KV 綁定，變數名稱 FEEDBACK
//   - 祕密：LINE_CHANNEL_SECRET、LINE_CHANNEL_ACCESS_TOKEN、FEEDBACK_TOKEN
// ============================================================

// 要和 bot/config.py 裡的 GENRES 一致
const GENRES = [
  "末世冒險", "奇幻冒險", "都市言情", "懸疑推理", "科幻", "古風武俠",
  "穿越重生", "校園青春", "職場", "溫馨日常", "輕恐怖",
];

const MAX_EVENTS = 300;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "POST" && url.pathname === "/webhook") {
      return handleWebhook(request, env);
    }

    if (request.method === "GET" && url.pathname === "/feedback") {
      if (request.headers.get("Authorization") !== `Bearer ${env.FEEDBACK_TOKEN}`) {
        return new Response("unauthorized", { status: 401 });
      }
      return Response.json({ events: await loadEvents(env) });
    }

    return new Response("每日小說 LINE 小幫手運作中 ✅", {
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  },
};

// ------------------------------------------------------------
async function handleWebhook(request, env) {
  const body = await request.text();
  const ok = await verifySignature(body, request.headers.get("x-line-signature"), env.LINE_CHANNEL_SECRET);
  if (!ok) return new Response("bad signature", { status: 401 });

  const { events = [] } = JSON.parse(body || "{}");
  for (const ev of events) {
    try {
      await handleEvent(ev, env);
    } catch (e) {
      console.log("event error", e);
    }
  }
  return new Response("ok");
}

async function handleEvent(ev, env) {
  const ts = ev.timestamp || Date.now();

  if (ev.type === "follow") {
    return reply(env, ev.replyToken, [text(
      "嗨～歡迎來到每日小說 📖\n每天早上我會送上一集新故事，讀完記得幫故事打分數喔！\n\n想看什麼類型，隨時點下面的按鈕告訴我；也可以直接打字許願，例如「想看甜一點的」。",
      true,
    )]);
  }

  if (ev.type === "postback") {
    const p = new URLSearchParams(ev.postback.data);

    if (p.get("type") === "rate") {
      const score = parseInt(p.get("score"), 10);
      await saveEvent(env, { ts, type: "rate", ep: p.get("ep"), score });
      const thanks = score >= 4 ? "太好了，謝謝你喜歡 💕" : score === 3 ? "收到，我會再努力的 ✍️" : "收到，下次會寫得更好看 🙏";
      return reply(env, ev.replyToken, [text(`${thanks}\n明天想看什麼呢？`, true)]);
    }

    if (p.get("type") === "next") {
      return chooseNext(env, ev.replyToken, ts, p.get("genre"));
    }
    return;
  }

  if (ev.type === "message" && ev.message.type === "text") {
    const t = ev.message.text.trim();
    if (GENRES.includes(t)) return chooseNext(env, ev.replyToken, ts, t);
    if (["繼續", "繼續追", "繼續追這部"].includes(t)) return chooseNext(env, ev.replyToken, ts, "continue");
    if (["隨機", "隨機驚喜"].includes(t)) return chooseNext(env, ev.replyToken, ts, "random");

    await saveEvent(env, { ts, type: "wish", text: t.slice(0, 200) });
    return reply(env, ev.replyToken, [text(
      "收到你的願望了 ✨ 明天的故事會參考你的想法！\n（想指定類型也可以點下面的按鈕）",
      true,
    )]);
  }
}

async function chooseNext(env, replyToken, ts, genre) {
  await saveEvent(env, { ts, type: "next", genre });
  let msg;
  if (genre === "continue") msg = "好的！明天繼續追這部 📖";
  else if (genre === "random") msg = "好的！明天給你一個驚喜 🎁";
  else msg = `好的！明天開一部新的「${genre}」✨`;
  return reply(env, replyToken, [text(msg + "\n（改變心意的話，再點一次別的就好）", false)]);
}

// ------------------------------------------------------------
function quickReply() {
  const item = (label, genre) => ({
    type: "action",
    action: { type: "postback", label, data: `type=next&genre=${genre}`, displayText: `明天想看：${label}` },
  });
  return {
    items: [
      item("📖 繼續追這部", "continue"),
      item("🎁 隨機驚喜", "random"),
      ...GENRES.map((g) => item(g, g)),
    ].slice(0, 13), // LINE 最多 13 個按鈕
  };
}

function text(t, withChoices) {
  const m = { type: "text", text: t };
  if (withChoices) m.quickReply = quickReply();
  return m;
}

async function reply(env, replyToken, messages) {
  if (!replyToken) return;
  const r = await fetch("https://api.line.me/v2/bot/message/reply", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}`,
    },
    body: JSON.stringify({ replyToken, messages }),
  });
  if (!r.ok) console.log("reply failed", r.status, await r.text());
}

// ------------------------------------------------------------
async function loadEvents(env) {
  return JSON.parse((await env.FEEDBACK.get("events")) || "[]");
}

async function saveEvent(env, ev) {
  const events = await loadEvents(env);
  events.push(ev);
  await env.FEEDBACK.put("events", JSON.stringify(events.slice(-MAX_EVENTS)));
}

async function verifySignature(body, signature, secret) {
  if (!signature || !secret) return false;
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  const expected = btoa(String.fromCharCode(...new Uint8Array(mac)));
  return expected === signature;
}
