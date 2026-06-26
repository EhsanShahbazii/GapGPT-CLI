import json
import time
import uuid
import websocket
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text

console = Console()

WS_URL = "wss://ws.gapgpt.app/ws/salam"
VERSION = 89
TIMEOUT_SECONDS = 120


def make_rid():
    return int(uuid.uuid4().int % 100000000)


def render_response(live, text, *, accent, bot_title, subtitle):
    live.update(Panel(
        Markdown(text),
        border_style=accent,
        title=bot_title,
        title_align="left",
        subtitle=subtitle,
        subtitle_align="right",
    ))


def merge_chunk(current, chunk_text, start_index):
    if start_index is None:
        return current + chunk_text
    if start_index < 0:
        start_index = len(current)
    if start_index > len(current):
        return current + chunk_text
    return current[:start_index] + chunk_text + current[start_index + len(chunk_text):]


class GapClient:
    def __init__(self, token, model="A-GAP", chat_token=None):
        self.token = token
        self.model = model
        self.chat_token = chat_token
        self.last_metadata = {}

    def send_message(self, text, *, accent="cyan", bot_title="GapGPT", subtitle=None, subtitle_factory=None):
        ws = None
        started_at = time.perf_counter()
        try:
            ws = websocket.create_connection(
                WS_URL,
                header=[
                    "Origin: https://gapgpt.app",
                    "Cache-Control: no-cache",
                    "Pragma: no-cache",
                    "Accept-Language: en-US,en;q=0.9,fa;q=0.8,tr;q=0.7",
                    "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari",
                ]
            )
        except Exception as exc:
            console.print(Panel(
                f"[red]Could not connect to GapGPT:[/red] {exc}",
                border_style="red",
                title="Connection error",
                title_align="left",
            ))
            self.last_metadata = {
                "input_seconds": time.perf_counter() - started_at,
                "output_seconds": 0.0,
            }
            return ""

        try:
            heartbeat_rid = make_rid()
            ws.send(json.dumps({
                "event": "heartbeat",
                "rid": heartbeat_rid,
                "data": {},
                "version": VERSION,
                "token": self.token,
            }))
        except Exception:
            pass

        rid = make_rid()

        payload = {
            "event": "new_message",
            "rid": rid,
            "data": {
                "action": {
                    "type": "text_message",
                    "message_token": "",
                    "bot_name": self.model,
                    "version": 4,
                    "chat_token": self.chat_token or "",
                    "text": text,
                    "file_tokens": [],
                    "allow_downgrade": False,
                    "enable_user_memory": True,
                    "mode": "",
                    "mode_extra": {},
                    "chat_extra": {},
                    "is_temporary": False,
                },
                "chat_token": self.chat_token or "",
                "chat_model": self.model,
                "message_token": "",
                "allow_downgrade": False,
                "enable_user_memory": True,
            },
            "version": VERSION,
            "token": self.token
        }

        ws.send(json.dumps(payload))
        sent_at = time.perf_counter()

        response_text = ""
        response_blocks = {}
        message_token = None
        last_text_at = None
        spinner = Spinner("dots", Text("Thinking...", style=accent))

        def panel_subtitle(current_text):
            if subtitle_factory:
                return subtitle_factory(
                    current_text,
                    sent_at - started_at,
                    time.perf_counter() - sent_at,
                )
            return subtitle

        def update_text(live):
            nonlocal response_text
            if response_blocks:
                response_text = "\n".join(value for value in response_blocks.values() if value)
            if response_text:
                render_response(
                    live,
                    response_text,
                    accent=accent,
                    bot_title=bot_title,
                    subtitle=panel_subtitle(response_text),
                )

        with Live(spinner, console=console, refresh_per_second=12) as live:
            while True:
                try:
                    ws.settimeout(12 if response_text else TIMEOUT_SECONDS)
                    raw = ws.recv()
                except Exception:
                    break

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                event = data.get("event", "")

                if event == "heartbeat":
                    try:
                        ws.send(json.dumps({
                            "event": "heartbeat_ack",
                            "rid": data.get("rid"),
                            "data": {},
                            "version": VERSION,
                            "token": self.token,
                        }))
                    except Exception:
                        pass
                    if response_text and last_text_at and time.perf_counter() - last_text_at > 8:
                        break
                    continue

                if event in ("heartbeat_ack", "event_ack"):
                    continue

                if event == "ack_new_message":
                    ack_data = data.get("data", {})
                    if ack_data.get("chat_token"):
                        self.chat_token = ack_data["chat_token"]
                    ack_message = ack_data.get("message", {})
                    if ack_message.get("token"):
                        message_token = ack_message["token"]
                    continue

                if event == "text_response_chunk":
                    chunk = data.get("data", {})
                    if self.chat_token and chunk.get("token") and chunk.get("token") != self.chat_token:
                        continue
                    if message_token and chunk.get("mtoken") and chunk.get("mtoken") != message_token:
                        continue
                    block_id = chunk.get("block_id") or "default"
                    response_blocks[block_id] = merge_chunk(
                        response_blocks.get(block_id, ""),
                        chunk.get("text", ""),
                        chunk.get("start_ind"),
                    )
                    last_text_at = time.perf_counter()
                    update_text(live)
                    continue

                if event != "new_message":
                    continue

                message = data.get("data", {}).get("message", {})
                if message.get("text") and message.get("text") != text:
                    continue
                if message.get("chat_token"):
                    self.chat_token = message["chat_token"]
                if message.get("token"):
                    message_token = message["token"]

                msg_status = message.get("status", "")
                responses = message.get("response", [])

                for block in responses:
                    block_type = block.get("type")
                    block_id = block.get("block_id") or "default"
                    if block_type == "text" and block.get("content") is not None:
                        response_blocks[block_id] = block["content"]
                        last_text_at = time.perf_counter()

                update_text(live)

                if msg_status in ("completed", "done"):
                    break

                if msg_status == "error":
                    live.update(Text("[red]Error generating response[/red]", style="red"))
                    break

                if response_text and last_text_at:
                    if time.perf_counter() - last_text_at > 8:
                        break

        if not response_text:
            console.print("[dim]No response received[/dim]")

        ws.close()
        self.last_metadata = {
            "input_seconds": sent_at - started_at,
            "output_seconds": time.perf_counter() - sent_at,
        }
        return response_text

    def reset_chat(self):
        self.chat_token = None

    def set_model(self, model):
        self.model = model

    def set_chat_token(self, chat_token):
        self.chat_token = chat_token
