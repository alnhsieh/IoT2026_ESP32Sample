"""
FastAPI + Line Messaging API 整合
當呼叫 API 時，發送訊息到 Line
"""

from fastapi import FastAPI, HTTPException, Request, Header
from pydantic import BaseModel
from pydantic_settings import BaseSettings
from typing import Optional
import httpx
import hmac
import hashlib
import base64
import json
from dotenv import load_dotenv

# 載入 .env 檔案
load_dotenv()


class Settings(BaseSettings):
    """應用程式設定，自動從 .env 讀取"""
    line_channel_access_token: str = ""
    line_channel_secret: str = ""
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# 初始化設定
settings = Settings()

# 檢查必要設定
if not settings.line_channel_access_token:
    print("=" * 50)
    print("⚠️  警告：LINE_CHANNEL_ACCESS_TOKEN 未設定！")
    print("請確認 .env 檔案存在且包含正確的 Token")
    print("=" * 50)

app = FastAPI(
    title="Line Message API",
    description="透過 API 發送 Line 訊息"
)

# Line Messaging API 設定
LINE_CHANNEL_ACCESS_TOKEN = settings.line_channel_access_token
LINE_CHANNEL_SECRET = settings.line_channel_secret
LINE_API_URL = "https://api.line.me/v2/bot/message"


def verify_signature(body: bytes, signature: str) -> bool:
    """
    驗證 Line Webhook 簽章
    確保請求真的來自 Line 伺服器
    """
    if not LINE_CHANNEL_SECRET:
        # 如果沒設定 secret，跳過驗證（不建議在正式環境這樣做）
        return True
    
    hash_value = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()
    
    expected_signature = base64.b64encode(hash_value).decode("utf-8")
    return hmac.compare_digest(signature, expected_signature)


class PushMessageRequest(BaseModel):
    """推播訊息請求模型"""
    user_id: str  # Line User ID
    message: str  # 要發送的訊息


class BroadcastMessageRequest(BaseModel):
    """廣播訊息請求模型（發送給所有好友）"""
    message: str


class ReplyMessageRequest(BaseModel):
    """回覆訊息請求模型"""
    reply_token: str  # Line 提供的回覆 token
    message: str


class MulticastMessageRequest(BaseModel):
    """多人推播訊息請求模型"""
    user_ids: list[str]  # 多個 Line User ID
    message: str


def get_headers():
    """取得 Line API 請求標頭"""
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }


@app.get("/")
async def root():
    """首頁"""
    return {
        "message": "Line Message API 服務運行中",
        "endpoints": {
            "POST /send": "推播訊息給指定用戶",
            "POST /broadcast": "廣播訊息給所有好友",
            "POST /reply": "回覆訊息",
            "POST /multicast": "推播訊息給多個用戶",
            "POST /webhook": "Line Webhook 接收端點"
        }
    }


@app.post("/send")
async def send_push_message(request: PushMessageRequest):
    """
    推播訊息給指定用戶
    
    - user_id: Line User ID（可從 webhook 事件中取得）
    - message: 要發送的文字訊息
    """
    payload = {
        "to": request.user_id,
        "messages": [
            {
                "type": "text",
                "text": request.message
            }
        ]
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{LINE_API_URL}/push",
            headers=get_headers(),
            json=payload
        )
    
    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Line API 錯誤: {response.text}"
        )
    
    return {"status": "success", "message": "訊息已發送"}


@app.post("/broadcast")
async def send_broadcast_message(request: BroadcastMessageRequest):
    """
    廣播訊息給所有加入好友的用戶
    
    - message: 要發送的文字訊息
    """
    payload = {
        "messages": [
            {
                "type": "text",
                "text": request.message
            }
        ]
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{LINE_API_URL}/broadcast",
            headers=get_headers(),
            json=payload
        )
    
    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Line API 錯誤: {response.text}"
        )
    
    return {"status": "success", "message": "廣播訊息已發送"}


@app.post("/reply")
async def send_reply_message(request: ReplyMessageRequest):
    """
    回覆訊息（需要 reply token，只能在收到訊息後短時間內使用）
    
    - reply_token: 從 webhook 事件取得的回覆 token
    - message: 要回覆的文字訊息
    """
    payload = {
        "replyToken": request.reply_token,
        "messages": [
            {
                "type": "text",
                "text": request.message
            }
        ]
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{LINE_API_URL}/reply",
            headers=get_headers(),
            json=payload
        )
    
    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Line API 錯誤: {response.text}"
        )
    
    return {"status": "success", "message": "回覆已發送"}


@app.post("/multicast")
async def send_multicast_message(request: MulticastMessageRequest):
    """
    推播訊息給多個用戶（最多 500 人）
    
    - user_ids: Line User ID 列表
    - message: 要發送的文字訊息
    """
    if len(request.user_ids) > 500:
        raise HTTPException(
            status_code=400,
            detail="一次最多只能發送給 500 位用戶"
        )
    
    payload = {
        "to": request.user_ids,
        "messages": [
            {
                "type": "text",
                "text": request.message
            }
        ]
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{LINE_API_URL}/multicast",
            headers=get_headers(),
            json=payload
        )
    
    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Line API 錯誤: {response.text}"
        )
    
    return {"status": "success", "message": f"訊息已發送給 {len(request.user_ids)} 位用戶"}

1
@app.post("/webhook")
async def webhook(
    request: Request,
    x_line_signature: str = Header(None, alias="X-Line-Signature")
):
    """
    Line Webhook 接收端點
    當有人傳訊息給 Bot 時，Line 會呼叫這個端點
    """
    # 取得原始請求內容
    body = await request.body()
    
    # 驗證簽章
    if x_line_signature and not verify_signature(body, x_line_signature):
        raise HTTPException(status_code=403, detail="簽章驗證失敗")
    
    # 解析 JSON
    body_json = json.loads(body)
    
    events = body_json.get("events", [])
    
    for event in events:
        event_type = event.get("type")
        
        if event_type == "message":
            # 收到訊息事件
            user_id = event["source"]["userId"]
            message = event["message"]
            reply_token = event["replyToken"]
            
            print(f"收到來自 {user_id} 的訊息: {message}")
            
            # 你可以在這裡加入自動回覆邏輯
            # 例如：自動回覆相同訊息
            if message.get("type") == "text":
                await auto_reply(reply_token, f"你說: {message.get('text')}")
        
        elif event_type == "follow":
            # 有人加入好友
            user_id = event["source"]["userId"]
            print(f"新好友: {user_id}")
        
        elif event_type == "unfollow":
            # 有人封鎖
            user_id = event["source"]["userId"]
            print(f"被封鎖: {user_id}")
    
    return {"status": "ok"}


async def auto_reply(reply_token: str, message: str):
    """自動回覆輔助函數"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("錯誤：無法回覆，LINE_CHANNEL_ACCESS_TOKEN 未設定")
        return
    
    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{LINE_API_URL}/reply",
                headers=get_headers(),
                json=payload
            )
            if response.status_code != 200:
                print(f"回覆失敗: {response.text}")
    except Exception as e:
        print(f"回覆時發生錯誤: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)