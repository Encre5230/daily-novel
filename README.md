# 每日小說

每天早上自動寫一集小說（約 5,000～6,000 字、3 章），發布到網站，並透過 LINE 官方帳號通知。

- `bot/config.py`：唯一可能需要調整的設定（類型、字數、模型）
- `bot/generate.py`：寫小說
- `bot/site_builder.py`：產生網站
- `bot/notify.py`：發 LINE 通知
- `worker.js`：貼到 Cloudflare Worker，接收 LINE 上的評分與許願
- `.github/workflows/daily.yml`：每天自動執行的排程
