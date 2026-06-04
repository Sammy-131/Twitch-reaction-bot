import asyncio
import json
import os
import re
from typing import Set

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

TWITCH_TOKEN = os.getenv("TWITCH_TOKEN", "")
TWITCH_NICK = os.getenv("TWITCH_NICK", "")
DEFAULT_CHANNEL = os.getenv("DEFAULT_CHANNEL", "")
DEFAULT_KEYWORDS = os.getenv("DEFAULT_KEYWORDS", "hey,hello,warframe")

CHANNEL_HOST = "irc.chat.twitch.tv"
CHANNEL_PORT = 6667

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict) -> None:
        if not self.active_connections:
            return
        payload = json.dumps(message)
        await asyncio.gather(
            *[connection.send_text(payload) for connection in self.active_connections],
            return_exceptions=True,
        )

manager = ConnectionManager()

class TwitchChatBot:
    def __init__(self):
        self.channel = DEFAULT_CHANNEL.strip().lower()
        self.keywords = [kw.strip().lower() for kw in DEFAULT_KEYWORDS.split(",") if kw.strip()]
        self.reader = None
        self.writer = None
        self.task = None
        self.reconnect_event = asyncio.Event()
        self._running = False

    async def start(self) -> None:
        if self.task is None:
            self._running = True
            self.task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        self.reconnect_event.set()
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        if self.task:
            await self.task
            self.task = None

    def update_settings(self, channel: str, keywords: str) -> None:
        cleaned_channel = channel.strip().lower()
        new_keywords = [kw.strip().lower() for kw in keywords.split(",") if kw.strip()]
        if cleaned_channel != self.channel:
            self.channel = cleaned_channel
            self.reconnect_event.set()
        self.keywords = new_keywords or self.keywords

    async def _run_loop(self) -> None:
        while self._running:
            if not self.channel:
                await asyncio.sleep(1)
                continue

            try:
                await self._connect_to_irc()
                await self._listen_loop()
            except Exception as error:
                await manager.broadcast({
                    "type": "status",
                    "message": f"Twitch chat connection error: {error}. Reconnecting...",
                })
            finally:
                await self._cleanup_connection()
                await asyncio.sleep(5)

    async def _connect_to_irc(self) -> None:
        if not TWITCH_TOKEN or not TWITCH_NICK:
            raise RuntimeError("TWITCH_TOKEN and TWITCH_NICK must be set in environment variables.")

        self.reader, self.writer = await asyncio.open_connection(CHANNEL_HOST, CHANNEL_PORT)
        self._send_line(f"PASS {TWITCH_TOKEN}")
        self._send_line(f"NICK {TWITCH_NICK}")
        self._send_line(f"JOIN #{self.channel}")
        await manager.broadcast({
            "type": "status",
            "message": f"Connected to Twitch chat for #{self.channel}.",
        })

    def _send_line(self, line: str) -> None:
        if self.writer and not self.writer.is_closing():
            self.writer.write(f"{line}\r\n".encode("utf-8"))

    async def _listen_loop(self) -> None:
        while self._running and self.reader:
            if self.reconnect_event.is_set():
                self.reconnect_event.clear()
                return

            line = await self.reader.readline()
            if not line:
                return

            text = line.decode("utf-8", errors="ignore").strip()
            if not text:
                continue

            if text.startswith("PING"):
                self._send_line(text.replace("PING", "PONG", 1))
                continue

            message = self._parse_privmsg(text)
            if message:
                await manager.broadcast({
                    "type": "chat",
                    "channel": message["channel"],
                    "username": message["username"],
                    "message": message["message"],
                })
                reaction = self._generate_reaction(message["username"], message["message"])
                if reaction:
                    await manager.broadcast({
                        "type": "reaction",
                        "reaction": reaction,
                        "trigger": message["matched_keyword"],
                        "username": message["username"],
                        "message": message["message"],
                    })

    def _parse_privmsg(self, raw_line: str) -> dict | None:
        match = re.search(r":(?P<username>[^!]+)!.* PRIVMSG #(?P<channel>[^ ]+) :(?P<message>.*)$", raw_line)
        if not match:
            return None
        chat_message = match.group("message")
        found_keyword = self._detect_keyword(chat_message)
        if not found_keyword:
            return None
        return {
            "username": match.group("username"),
            "channel": match.group("channel"),
            "message": chat_message,
            "matched_keyword": found_keyword,
        }

    def _detect_keyword(self, content: str) -> str | None:
        content_lower = content.lower()
        for keyword in self.keywords:
            if keyword and keyword in content_lower:
                return keyword
        return None

    def _generate_reaction(self, username: str, message: str) -> str | None:
        keyword = self._detect_keyword(message)
        if not keyword:
            return None
        if keyword == "hey":
            return f"Hey {username}! That greeting is getting noticed."
        if keyword == "hello":
            return f"Hello back to {username}! Nice chat energy."
        if keyword == "warframe":
            return f"Warframe hype! {username} just dropped a Warframe mention."
        return f"Detected '{keyword}' from {username}: {message}"

    async def _cleanup_connection(self) -> None:
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        self.reader = None
        self.writer = None

bot = TwitchChatBot()

@app.on_event("startup")
async def startup_event() -> None:
    await bot.start()

@app.get("/")
async def get_root() -> FileResponse:
    return FileResponse("static/index.html")

@app.get("/config")
async def get_config() -> dict:
    return {
        "defaultChannel": DEFAULT_CHANNEL,
        "defaultKeywords": DEFAULT_KEYWORDS,
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        await websocket.send_text(json.dumps({
            "type": "status",
            "message": "WebSocket connection established.",
        }))
        while True:
            payload = await websocket.receive_text()
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "settings":
                channel = data.get("channel", "").strip()
                keywords = data.get("keywords", "").strip()
                bot.update_settings(channel, keywords)
                await websocket.send_text(json.dumps({
                    "type": "status",
                    "message": f"Updated settings: channel=#{channel}, keywords=[{keywords}].",
                }))
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
