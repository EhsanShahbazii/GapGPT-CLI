import os
import json
import webbrowser
from pathlib import Path

from rich.console import Console
from rich.prompt import Prompt

console = Console()
CONFIG_DIR = Path.home() / ".gapcode"
TOKEN_FILE = CONFIG_DIR / "token"
CONFIG_FILE = CONFIG_DIR / "config.json"


def get_token():
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text().strip()
        if token:
            return token
    return None


def save_token(token):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token.strip())


def load_config():
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text())
        except json.JSONDecodeError:
            config = {}
        return {"model": "A-GAP", "theme": "aurora", "verbose": True, **config}
    return {"model": "A-GAP", "theme": "aurora", "verbose": True}


def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def login():
    console.print("\n[bold cyan]🔐 GapCode Authentication[/bold cyan]\n")
    console.print("Opening [link=https://gapgpt.app]gapgpt.app[/link] in your browser...\n")
    console.print("Steps:")
    console.print("  1. Log in to your account")
    console.print("  2. Open DevTools (F12) → Application → Local Storage → gapgpt.app")
    console.print("  3. Find the [bold]access_token[/bold] key and copy its value")
    console.print("")
    webbrowser.open("https://gapgpt.app")
    token = Prompt.ask("[bold yellow]Paste your access token[/bold yellow]")
    token = token.strip().strip('"').strip("'")
    if not token:
        console.print("[red]No token provided. Exiting.[/red]")
        raise SystemExit(1)
    save_token(token)
    console.print("[green]✓ Token saved to[/green] [dim]~/.gapcode/token[/dim]\n")
    return token
