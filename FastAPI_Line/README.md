# FastAPI + Line Messaging API

透過 FastAPI 建立的 Line Bot 訊息發送服務。

## 功能

- **推播訊息** (`POST /send`) - 發送訊息給指定用戶
- **廣播訊息** (`POST /broadcast`) - 發送訊息給所有好友
- **回覆訊息** (`POST /reply`) - 回覆收到的訊息
- **多人推播** (`POST /multicast`) - 發送訊息給多個用戶
- **Webhook** (`POST /webhook`) - 接收 Line 傳來的事件

## 安裝步驟

### 1. 安裝依賴

```bash
pip install -r requirements.txt
```

### 2. 設定 Line Channel

1. 前往 [Line Developers Console](https://developers.line.biz/console/)
2. 建立 Provider（如果還沒有）
3. 建立 **Messaging API Channel**
4. 在 **Messaging API** 頁籤中：
   - 取得 **Channel Access Token**（點擊 Issue 產生）
   - 設定 **Webhook URL**（部署後的網址 + `/webhook`）
   - 開啟 **Use webhook**

### 3. 設定環境變數

```bash
# 複製範例檔案
cp .env.example .env

# 編輯 .env，填入你的 Channel Access Token
```

或直接修改 `main.py` 中的 `LINE_CHANNEL_ACCESS_TOKEN`。

### 4. 啟動服務

```bash
# 開發模式（自動重載）
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 或直接執行
python main.py
```

服務啟動後，可訪問 http://localhost:8000/docs 查看 API 文件。

## API 使用範例

### 推播訊息給指定用戶

```bash
curl -X POST "http://localhost:8000/send" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "U1234567890abcdef",
    "message": "Hello from API!"
  }'
```

### 廣播訊息給所有好友

```bash
curl -X POST "http://localhost:8000/broadcast" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "這是廣播訊息！"
  }'
```

### 推播給多個用戶

```bash
curl -X POST "http://localhost:8000/multicast" \
  -H "Content-Type: application/json" \
  -d '{
    "user_ids": ["U123...", "U456..."],
    "message": "群發訊息"
  }'
```

## 如何取得 User ID

User ID 可以從以下方式取得：

1. **Webhook 事件** - 當用戶傳訊息給 Bot 時，webhook 會收到包含 `userId` 的事件
2. **Line Login** - 透過 Line Login 取得用戶資訊
3. **Bot 後台** - 部分情況下可從 Line Official Account Manager 查看

## 部署建議

### 使用 ngrok 測試（本地開發）

```bash
# 安裝 ngrok 後
ngrok http 8000

# 將產生的 https 網址設定為 Webhook URL
# 例如：https://xxxx.ngrok.io/webhook
```

### 正式部署

建議部署到：
- **Railway**
- **Render**
- **Heroku**
- **AWS / GCP / Azure**

確保：
1. 使用 HTTPS
2. 設定環境變數
3. Webhook URL 設定正確

## 注意事項

- Push 訊息和 Multicast 在免費方案中有數量限制
- Reply 訊息免費且無限制，但 reply token 只有短時間有效
- Broadcast 會發送給所有加入好友的用戶
