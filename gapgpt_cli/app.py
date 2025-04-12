import json
import os
import re
import select
import sys
import termios
import time
import tty
import uuid
from datetime import datetime
from pathlib import Path

from rich.align import Align
from rich.box import ROUNDED, SIMPLE_HEAVY
from rich.columns import Columns
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.live import Live

from .auth import CONFIG_DIR, get_token, login, load_config, save_config
from .client import GapClient

console = Console()
ROOT_DIR = Path(__file__).resolve().parent
MODELS_FILE = ROOT_DIR / "models.json"
CHATS_FILE = CONFIG_DIR / "chats.json"

THEMES = {
    "aurora": {
        "name": "Aurora",
        "accent": "bright_cyan",
        "accent_2": "bright_magenta",
        "accent_3": "bright_green",
        "muted": "grey66",
        "prompt": "bright_green",
        "user": "bright_white",
    },
    "synthwave": {
        "name": "Synthwave",
        "accent": "magenta",
        "accent_2": "bright_blue",
        "accent_3": "bright_yellow",
        "muted": "grey70",
        "prompt": "bright_magenta",
        "user": "bright_cyan",
    },
    "ember": {
        "name": "Ember",
        "accent": "orange1",
        "accent_2": "red1",
        "accent_3": "yellow1",
        "muted": "grey62",
        "prompt": "orange1",
        "user": "bright_white",
    },
    "ocean": {
        "name": "Ocean",
        "accent": "deep_sky_blue1",
        "accent_2": "spring_green2",
        "accent_3": "cyan",
        "muted": "grey66",
        "prompt": "deep_sky_blue1",
        "user": "bright_white",
    },
    "mono": {
        "name": "Mono",
        "accent": "white",
        "accent_2": "grey82",
        "accent_3": "grey70",
        "muted": "grey58",
        "prompt": "white",
        "user": "white",
    },
}


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def short_time(value):
    if not value:
        return "never"
    try:
        return datetime.fromisoformat(value).strftime("%b %d, %H:%M")
    except ValueError:
        return value


def get_theme(config):
    theme_name = config.get("theme", "aurora")
    return THEMES.get(theme_name, THEMES["aurora"])


def gradient_text(value, styles):
    text = Text()
    painted = 0
    for char in value:
        if char.isspace():
            text.append(char)
        else:
            text.append(char, style=styles[painted % len(styles)])
            painted += 1
    return text


def read_menu_key(fd):
    key = os.read(fd, 1).decode(errors="ignore")
    if key in ("\r", "\n"):
        return "enter"
    if key == "\x03":
        raise KeyboardInterrupt
    if key in ("q", "Q"):
        return "escape"
    if key in ("j", "J"):
        return "down"
    if key in ("k", "K"):
        return "up"
    if key != "\x1b":
        return key

    sequence = ""
    while select.select([fd], [], [], 0.04)[0]:
        sequence += os.read(fd, 1).decode(errors="ignore")
        if sequence in ("[A", "[B", "OA", "OB") or sequence.endswith("~"):
            break
        if len(sequence) >= 5:
            break

    if sequence in ("[A", "OA"):
        return "up"
    if sequence in ("[B", "OB"):
        return "down"
    if sequence == "[5~":
        return "page_up"
    if sequence == "[6~":
        return "page_down"
    return "escape"


def resolve_column_style(column, active_theme):
    if column.get("style_key"):
        return active_theme[column["style_key"]]
    return column.get("style", "white")


def clip_cell(value, width):
    value = " ".join(str(value or "").split())
    if width and len(value) > width:
        return value[:max(0, width - 3)] + "..."
    return value


def render_selector(
    title,
    options,
    selected_index,
    theme,
    columns,
    footer,
    max_visible=18,
    preview_factory=None,
):
    total = len(options)
    half_window = max_visible // 2
    start = max(0, selected_index - half_window)
    end = min(total, start + max_visible)
    start = max(0, end - max_visible)
    visible_options = options[start:end]
    active_theme = options[selected_index].get("_theme", theme)

    table = Table(
        title=f"{title}  ({selected_index + 1}/{total})",
        title_style=active_theme["accent"],
        border_style=active_theme["accent"],
        box=ROUNDED,
        expand=False,
    )
    table.add_column("", width=2, justify="center", style=active_theme["accent"], no_wrap=True)
    for column in columns:
        table.add_column(
            column["title"],
            style=resolve_column_style(column, active_theme),
            width=column.get("width"),
            min_width=column.get("width"),
            max_width=column.get("width"),
            overflow="ellipsis",
            no_wrap=True,
        )

    for visible_index, option in enumerate(visible_options, start=start):
        marker = ">" if visible_index == selected_index else ""
        row_style = "reverse bold" if visible_index == selected_index else None
        table.add_row(
            marker,
            *[clip_cell(option.get(column["key"], ""), column.get("width")) for column in columns],
            style=row_style,
        )

    for _ in range(max_visible - len(visible_options)):
        table.add_row("", *["" for _ in columns])

    selector = Panel(
        Group(table, Text(footer, style=active_theme["muted"], justify="center")),
        border_style=active_theme["accent_2"],
        box=ROUNDED,
    )
    if not preview_factory:
        return selector
    return Columns([selector, preview_factory(options[selected_index], active_theme)], expand=True, equal=False)


def keyboard_select(
    title,
    options,
    theme,
    columns,
    selected_index=0,
    footer="Use Up/Down, Enter to select, Esc to cancel.",
    max_visible=18,
    preview_factory=None,
    transient=True,
):
    if not options:
        return None

    selected_index = max(0, min(selected_index, len(options) - 1))
    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except termios.error:
        return None
    try:
        tty.setcbreak(fd)
        while True:
            console.clear()
            console.print(render_selector(title, options, selected_index, theme, columns, footer, max_visible, preview_factory))
            key = read_menu_key(fd)
            if key == "up":
                selected_index = (selected_index - 1) % len(options)
            elif key == "down":
                selected_index = (selected_index + 1) % len(options)
            elif key == "page_up":
                selected_index = max(0, selected_index - 10)
            elif key == "page_down":
                selected_index = min(len(options) - 1, selected_index + 10)
            elif key == "enter":
                return options[selected_index]
            elif key == "escape":
                return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def load_models():
    try:
        raw_models = json.loads(MODELS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return []

    models = []
    seen = set()
    for item in raw_models:
        value = item.get("value")
        if not value or value in seen:
            continue
        seen.add(value)
        label = item.get("label") or value
        if label.startswith("Ie."):
            label = "GapGPT" if value == "A-GAP" else value
        models.append({
            "label": label,
            "value": value,
            "caption": item.get("caption", ""),
            "categories": item.get("categories", []),
            "show": item.get("showInModelSelection", False),
            "official": item.get("vitrineOfficialModel", False),
            "chosen": item.get("vitrineChosenModel", False),
            "pro": item.get("pro", False),
            "reasoning": item.get("reasoningModel", False),
            "image": item.get("supportsImage", item.get("hasFile", False)),
        })
    return models


def model_label(model_value, models):
    for model in models:
        if model["value"] == model_value:
            return f"{model['label']} ({model_value})"
    return model_value


def model_name(model_value, models):
    for model in models:
        if model["value"] == model_value:
            return model["label"]
    return model_value


def compact_caption(value, limit=90):
    caption = " ".join((value or "").split())
    if not caption:
        return "-"
    return caption[:limit] + ("..." if len(caption) > limit else "")


def count_tokens(text):
    return len(re.findall(r"\w+|[^\s\w]", text or "", re.UNICODE))


def elapsed_seconds(start):
    return max(0.0, time.perf_counter() - start)


def metadata_footer(parts):
    return " • ".join(part for part in parts if part)


def assistant_footer(model_value, models, input_tokens, response_text, input_seconds, output_seconds, verbose=True):
    parts = [f"model: {model_name(model_value, models)}"]
    if verbose:
        parts.extend([
            f"input: {input_tokens} tokens / {input_seconds:.2f}s",
            f"output: {count_tokens(response_text)} tokens / {output_seconds:.2f}s",
        ])
    return f"[dim]{metadata_footer(parts)}[/dim]"


def user_footer(user_input, verbose=True):
    if not verbose:
        return None
    return f"[dim]input: {count_tokens(user_input)} tokens[/dim]"


def erase_prompt_echo():
    if console.is_terminal:
        console.file.write("\x1b[1A\x1b[2K\r")
        console.file.flush()


def score_model(model):
    return (
        int(model["show"]) * 8
        + int(model["chosen"]) * 4
        + int(model["official"]) * 2
        - int(model["pro"])
    )


def search_models(models, query=""):
    query = query.strip().lower()
    if not query:
        results = models[:]
    else:
        results = [
            model for model in models
            if query in model["label"].lower()
            or query in model["value"].lower()
            or query in " ".join(model["categories"]).lower()
        ]
    return sorted(results, key=lambda model: (-score_model(model), model["label"].lower()))


def find_model(models, query):
    query = query.strip()
    if not query:
        return None, []

    for model in models:
        if model["value"].lower() == query.lower():
            return model, []

    matches = search_models(models, query)
    if len(matches) == 1:
        return matches[0], []
    return None, matches


def load_chat_store():
    if not CHATS_FILE.exists():
        return {"active_chat_id": None, "chats": []}
    try:
        store = json.loads(CHATS_FILE.read_text())
    except json.JSONDecodeError:
        return {"active_chat_id": None, "chats": []}
    store.setdefault("active_chat_id", None)
    store.setdefault("chats", [])
    return store


def save_chat_store(store):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CHATS_FILE.write_text(json.dumps(store, indent=2, ensure_ascii=False))


def create_chat(store, model, title=None):
    created = now_iso()
    chat = {
        "id": uuid.uuid4().hex[:10],
        "title": title or f"Chat {datetime.now().strftime('%b %d %H:%M')}",
        "model": model,
        "chat_token": None,
        "created_at": created,
        "updated_at": created,
        "messages": [],
    }
    store["chats"].append(chat)
    store["active_chat_id"] = chat["id"]
    save_chat_store(store)
    return chat


def sorted_chats(store):
    return sorted(store["chats"], key=lambda chat: chat.get("updated_at", ""), reverse=True)


def get_active_chat(store, model):
    active_id = store.get("active_chat_id")
    for chat in store["chats"]:
        if chat["id"] == active_id:
            return chat
    return create_chat(store, model)


def resolve_chat(store, reference):
    if not reference or reference == "current":
        reference = store.get("active_chat_id")

    chats = sorted_chats(store)
    if str(reference).isdigit():
        index = int(reference) - 1
        if 0 <= index < len(chats):
            return chats[index]

    for chat in store["chats"]:
        if chat["id"] == reference or chat["id"].startswith(str(reference)):
            return chat
    return None


def add_message(chat, role, text, model):
    chat["messages"].append({
        "role": role,
        "text": text,
        "model": model,
        "created_at": now_iso(),
    })
    chat["updated_at"] = now_iso()
    if role == "user" and len([m for m in chat["messages"] if m["role"] == "user"]) == 1:
        compact = " ".join(text.split())
        chat["title"] = compact[:52] + ("..." if len(compact) > 52 else "")


def boot_banner(theme, config, active_chat, models):
    logo_lines = [
        "   ____              ____          _      ",
        "  / ___| __ _ _ __  / ___|___   __| | ___ ",
        " | |  _ / _` | '_ \\| |   / _ \\ / _` |/ _ \\",
        " | |_| | (_| | |_) | |__| (_) | (_| |  __/",
        "  \\____|\\__,_| .__/ \\____\\___/ \\__,_|\\___|",
        "             |_|                           ",
    ]
    subtitle = (
        f"{THEMES.get(config.get('theme'), THEMES['aurora'])['name']} command center"
        f"  •  {model_label(active_chat['model'], models)}"
    )

    frames = ["♡", "♥", "❤", "♥", "♡"]
    with Live(console=console, refresh_per_second=16, transient=True) as live:
        for i, marker in enumerate(frames):
            logo = Text("\n".join(logo_lines), style=theme["accent"])
            panel = Panel(
                Align.center(Group(
                    logo,
                    Text(f"\n{marker} GapCode is warming up the console {marker}", style=theme["accent_2"]),
                    Text("Developed with heart by @EhsanShahbazi", style=theme["muted"]),
                )),
                border_style=theme["accent_2"],
                padding=(1, 2),
            )
            live.update(panel)
            time.sleep(0.08 + i * 0.03)

    logo = gradient_text("\n".join(logo_lines), [theme["accent"], theme["accent_2"], theme["accent_3"]])
    command_bar = Columns([
        Panel("[bold]/new[/bold]\n[dim]fresh chat[/dim]", border_style=theme["accent"], box=ROUNDED),
        Panel("[bold]/model[/bold]\n[dim]choose brain[/dim]", border_style=theme["accent_2"], box=ROUNDED),
        Panel("[bold]/theme[/bold]\n[dim]change mood[/dim]", border_style=theme["accent_3"], box=ROUNDED),
        Panel("[bold]/chats[/bold]\n[dim]history[/dim]", border_style=theme["accent"], box=ROUNDED),
    ], equal=True, expand=True)
    console.print(Panel(
        Align.center(Group(
            logo,
            Text(f"\n{subtitle}", style=theme["muted"]),
            Text.from_markup("[dim]Developed with [red]♥[/red] by [bold][link=https://github.com/EhsanShahbazii]@ehsanshahbazi[/link][/bold][/dim]"),
            Text("Type /help for the full command palette", style=theme["muted"]),
        )),
        border_style=theme["accent"],
        padding=(1, 2),
        box=SIMPLE_HEAVY,
    ))
    console.print(command_bar)


def print_status(theme, active_chat, models):
    stats = Table.grid(expand=True)
    stats.add_column(justify="left")
    stats.add_column(justify="center")
    stats.add_column(justify="right")
    stats.add_row(
        f"[{theme['accent']}]Session[/] {active_chat['title']} [dim]#{active_chat['id']}[/dim]",
        f"[{theme['accent_2']}]Model[/] {model_label(active_chat['model'], models)}",
        f"[{theme['accent_3']}]Messages[/] {len(active_chat['messages'])}",
    )
    console.print(Panel(stats, border_style=theme["accent"], box=ROUNDED))


def print_help(theme):
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style=theme["accent"], no_wrap=True)
    table.add_column(style="white")
    commands = [
        ("/help", "Show this command palette"),
        ("/new [title]", "Start a fresh chat"),
        ("/chats", "Show saved local chats"),
        ("/open <number|id>", "Switch to a saved chat"),
        ("/delete [number|id|current]", "Delete a saved local chat"),
        ("/rename <title>", "Rename the current chat"),
        ("/history [count]", "Show recent messages"),
        ("/models [search]", "Browse models from models.json"),
        ("/model [search|value]", "Open arrow-key model picker"),
        ("/theme [name]", "Open arrow-key theme picker"),
        ("/settings", "Show current theme, model, and storage path"),
        ("/verbose [on|off]", "Toggle dim model/tokens/timing footers"),
        ("/export [path]", "Export current chat as Markdown"),
        ("/token", "Re-authenticate with a new token"),
        ("/clear", "Clear the terminal"),
        ("/banner", "Replay the animated banner"),
        ("/exit", "Quit GapCode"),
    ]
    for command, description in commands:
        table.add_row(command, description)
    console.print(Panel(table, title="Command palette", title_align="left", border_style=theme["accent"], box=ROUNDED))


def print_models(models, theme, query="", limit=32):
    results = search_models(models, query)
    table = Table(
        title=f"Models{f' matching {query!r}' if query else ''}",
        title_style=theme["accent"],
        border_style=theme["accent"],
        box=ROUNDED,
    )
    table.add_column("#", style=theme["muted"], justify="right", width=3, no_wrap=True)
    table.add_column("Name", style="bold", width=18, no_wrap=True, overflow="ellipsis")
    table.add_column("Caption", style="white", width=58, no_wrap=True, overflow="ellipsis")
    table.add_column("Tags", style=theme["accent_2"], width=20, no_wrap=True, overflow="ellipsis")
    for index, model in enumerate(results[:limit], start=1):
        tags = ", ".join(model["categories"][:3]) or "-"
        table.add_row(str(index), model["label"], compact_caption(model["caption"], 58), tags)
    console.print(table)
    if len(results) > limit:
        console.print(f"[{theme['muted']}]Showing {limit} of {len(results)}. Try /models <search> to narrow it down.[/]")
    return results


def choose_model(models, theme, query="", current_model=None):
    selected, matches = find_model(models, query) if query else (None, search_models(models))
    if selected:
        return selected

    if not matches:
        console.print("[yellow]No models found.[/yellow]")
        return None

    options = []
    selected_index = 0
    for index, model in enumerate(matches):
        if model["value"] == current_model:
            selected_index = index
        options.append({
            "name": model["label"],
            "value": model["value"],
            "caption": compact_caption(model["caption"]),
            "tags": ", ".join(model["categories"][:3]) or "-",
            "model": model,
        })

    picked = keyboard_select(
        "Choose model",
        options,
        theme,
        [
            {"title": "Name", "key": "name", "style": "bold", "width": 18},
            {"title": "Caption", "key": "caption", "style": "white", "width": 48},
            {"title": "Tags", "key": "tags", "style_key": "accent_2", "width": 20},
        ],
        selected_index=selected_index,
        footer="Up/Down moves • Enter selects • Esc cancels • PageUp/PageDown jumps",
        max_visible=16,
    )
    return picked["model"] if picked else None


def theme_preview(option, preview_theme):
    code = Syntax(
        "def stream_reply(prompt):\n"
        "    model = 'GapGPT'\n"
        "    return model.run(prompt)\n",
        "python",
        theme="monokai",
        word_wrap=False,
        background_color="default",
    )
    return Panel(
        Group(
            Text(f"{option['name']} preview", style=f"bold {preview_theme['accent']}"),
            Panel(
                Text("Design the chat UI with color and motion.", style=preview_theme["user"]),
                title="You",
                title_align="left",
                subtitle="[dim]input: 9 tokens[/dim]",
                subtitle_align="right",
                border_style=preview_theme["accent_2"],
                box=ROUNDED,
            ),
            Panel(
                Markdown("Absolutely. Here is a crisp, animated command surface with a calm readable rhythm."),
                title="GapGPT",
                title_align="left",
                subtitle=f"[dim]model: {option['name']} • output: 14 tokens / 0.42s[/dim]",
                subtitle_align="right",
                border_style=preview_theme["accent"],
                box=ROUNDED,
            ),
            Panel(code, title="Code Sample", title_align="left", border_style=preview_theme["accent_3"], box=ROUNDED),
        ),
        title="Live Theme Preview",
        title_align="left",
        border_style=preview_theme["accent"],
        box=ROUNDED,
    )


def choose_theme(config, theme):
    current_theme = config.get("theme", "aurora")
    options = []
    selected_index = 0
    for index, (key, value) in enumerate(THEMES.items()):
        if key == current_theme:
            selected_index = index
        options.append({
            "name": value["name"],
            "key": key,
            "accent": value["accent"],
            "status": "current" if key == current_theme else "",
            "_theme": value,
        })

    picked = keyboard_select(
        "Choose theme",
        options,
        theme,
        [
            {"title": "Theme", "key": "name", "style": "bold", "width": 12},
            {"title": "Command", "key": "key", "style_key": "accent", "width": 12},
            {"title": "Accent", "key": "accent", "style_key": "accent_2", "width": 16},
            {"title": "Status", "key": "status", "style_key": "muted", "width": 8},
        ],
        selected_index=selected_index,
        footer="Up/Down moves • Enter applies • Esc cancels",
        max_visible=len(options),
        preview_factory=theme_preview,
        transient=False,
    )
    return picked["key"] if picked else None


def print_chats(store, theme, models):
    chats = sorted_chats(store)
    table = Table(title="Saved chats", title_style=theme["accent"], border_style=theme["accent"], box=ROUNDED)
    table.add_column("#", justify="right", style=theme["muted"])
    table.add_column("Title", style="bold")
    table.add_column("Model", style=theme["accent_2"])
    table.add_column("Messages", justify="right")
    table.add_column("Updated", style=theme["muted"])
    table.add_column("Id", style=theme["muted"])
    for index, chat in enumerate(chats, start=1):
        active = " ●" if chat["id"] == store.get("active_chat_id") else ""
        table.add_row(
            str(index),
            chat["title"] + active,
            model_label(chat.get("model", "A-GAP"), models),
            str(len(chat.get("messages", []))),
            short_time(chat.get("updated_at")),
            chat["id"],
        )
    console.print(table)


def print_history(chat, theme, count=8):
    messages = chat.get("messages", [])[-count:]
    if not messages:
        console.print(Panel("[dim]No messages in this chat yet.[/dim]", border_style=theme["accent"], box=ROUNDED))
        return
    for message in messages:
        role = "You" if message["role"] == "user" else "GapGPT"
        color = theme["accent_2"] if message["role"] == "user" else theme["accent"]
        content = Markdown(message["text"]) if message["role"] == "assistant" else Text(message["text"])
        console.print(Panel(
            content,
            title=f"{role} • {short_time(message.get('created_at'))}",
            title_align="left",
            border_style=color,
            box=ROUNDED,
        ))


def export_chat(chat, destination=None):
    filename = destination or f"gapcode-{chat['id']}.md"
    path = Path(filename).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    lines = [
        f"# {chat['title']}",
        "",
        f"- Chat id: `{chat['id']}`",
        f"- Model: `{chat.get('model', 'A-GAP')}`",
        f"- Updated: `{chat.get('updated_at', '')}`",
        "",
    ]
    for message in chat.get("messages", []):
        heading = "You" if message["role"] == "user" else "GapGPT"
        lines.extend([f"## {heading}", "", message["text"], ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def set_active_model(store, chat, client, config, model):
    chat["model"] = model["value"]
    chat["updated_at"] = now_iso()
    client.set_model(model["value"])
    config["model"] = model["value"]
    save_config(config)
    save_chat_store(store)


def handle_command(command, store, chat, client, config, models):
    theme = get_theme(config)
    parts = command.split(maxsplit=1)
    name = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if name == "/exit":
        console.print(f"[{theme['muted']}]Goodbye.[/]")
        sys.exit(0)

    if name == "/help":
        print_help(theme)
        return chat

    if name == "/clear":
        console.clear()
        print_status(theme, chat, models)
        return chat

    if name == "/banner":
        console.clear()
        boot_banner(theme, config, chat, models)
        print_status(theme, chat, models)
        return chat

    if name == "/new":
        chat = create_chat(store, config.get("model", client.model), title=arg or None)
        client.set_chat_token(None)
        client.set_model(chat["model"])
        console.print(f"[{theme['accent']}]New chat started:[/] {chat['title']} [dim]#{chat['id']}[/dim]")
        return chat

    if name == "/chats":
        print_chats(store, theme, models)
        return chat

    if name == "/open":
        target = resolve_chat(store, arg)
        if not target:
            console.print("[yellow]Chat not found. Use /chats to see available chats.[/yellow]")
            return chat
        store["active_chat_id"] = target["id"]
        save_chat_store(store)
        client.set_model(target.get("model", config.get("model", "A-GAP")))
        client.set_chat_token(target.get("chat_token"))
        config["model"] = client.model
        save_config(config)
        print_status(theme, target, models)
        print_history(target, theme, count=4)
        return target

    if name == "/delete":
        target = resolve_chat(store, arg or "current")
        if not target:
            console.print("[yellow]Chat not found.[/yellow]")
            return chat
        if not Confirm.ask(f"[red]Delete local chat '{target['title']}'?[/red]", default=False):
            return chat
        store["chats"] = [item for item in store["chats"] if item["id"] != target["id"]]
        if store.get("active_chat_id") == target["id"]:
            store["active_chat_id"] = store["chats"][-1]["id"] if store["chats"] else None
        if not store["active_chat_id"]:
            chat = create_chat(store, config.get("model", client.model))
        else:
            chat = get_active_chat(store, config.get("model", client.model))
            save_chat_store(store)
        client.set_model(chat.get("model", config.get("model", "A-GAP")))
        client.set_chat_token(chat.get("chat_token"))
        console.print(f"[{theme['accent']}]Deleted local chat.[/]")
        return chat

    if name == "/rename":
        if not arg:
            arg = Prompt.ask(f"[{theme['prompt']}]New chat title[/]").strip()
        if arg:
            chat["title"] = arg
            chat["updated_at"] = now_iso()
            save_chat_store(store)
            console.print(f"[{theme['accent']}]Renamed chat:[/] {arg}")
        return chat

    if name == "/history":
        count = int(arg) if arg.isdigit() else 8
        print_history(chat, theme, count=count)
        return chat

    if name == "/models":
        print_models(models, theme, arg)
        return chat

    if name == "/model":
        selected = choose_model(models, theme, arg, current_model=client.model)
        if selected:
            set_active_model(store, chat, client, config, selected)
            console.print(f"[{theme['accent']}]Model set:[/] {model_label(selected['value'], models)}")
        else:
            console.print(f"[{theme['muted']}]Model unchanged.[/]")
        return chat

    if name == "/theme":
        selected_theme = arg if arg else choose_theme(config, theme)
        if not selected_theme:
            console.print(f"[{theme['muted']}]Theme unchanged.[/]")
            return chat
        if selected_theme not in THEMES:
            console.print("[yellow]Unknown theme. Try aurora, synthwave, ember, ocean, or mono.[/yellow]")
            return chat
        config["theme"] = selected_theme
        save_config(config)
        console.print(f"[{THEMES[selected_theme]['accent']}]Theme set to {THEMES[selected_theme]['name']}.[/]")
        return chat

    if name == "/settings":
        settings = Table.grid(padding=(0, 2))
        settings.add_column(style=theme["accent"])
        settings.add_column(style="white")
        settings.add_row("Theme", THEMES.get(config.get("theme"), THEMES["aurora"])["name"])
        settings.add_row("Model", model_label(chat.get("model", client.model), models))
        settings.add_row("Verbose footers", "on" if config.get("verbose", True) else "off")
        settings.add_row("Chat store", str(CHATS_FILE))
        settings.add_row("Current chat", f"{chat['title']} #{chat['id']}")
        console.print(Panel(settings, title="Settings", title_align="left", border_style=theme["accent"], box=ROUNDED))
        return chat

    if name == "/verbose":
        if arg.lower() in ("on", "true", "yes", "1"):
            config["verbose"] = True
        elif arg.lower() in ("off", "false", "no", "0"):
            config["verbose"] = False
        else:
            config["verbose"] = not config.get("verbose", True)
        save_config(config)
        state = "on" if config.get("verbose", True) else "off"
        console.print(f"[{theme['accent']}]Verbose footers:[/] {state}")
        return chat

    if name == "/export":
        path = export_chat(chat, arg or None)
        console.print(f"[{theme['accent']}]Exported:[/] {path}")
        return chat

    if name == "/token":
        try:
            token = login()
            client.token = token
            console.print(f"[{theme['accent']}]Token updated.[/]")
        except (KeyboardInterrupt, SystemExit):
            pass
        return chat

    console.print(f"[yellow]Unknown command: {command}[/yellow] [dim]Type /help for commands.[/dim]")
    return chat


def main():
    config = load_config()
    models = load_models()
    store = load_chat_store()
    active_chat = get_active_chat(store, config.get("model", "A-GAP"))
    theme = get_theme(config)

    boot_banner(theme, config, active_chat, models)

    token = get_token()
    if not token:
        try:
            token = login()
        except (KeyboardInterrupt, SystemExit):
            console.print("\n[dim]Goodbye.[/dim]")
            sys.exit(0)

    client = GapClient(
        token,
        model=active_chat.get("model", config.get("model", "A-GAP")),
        chat_token=active_chat.get("chat_token"),
    )
    print_status(theme, active_chat, models)

    while True:
        theme = get_theme(config)
        try:
            user_input = Prompt.ask(f"\n[{theme['prompt']}]You[/]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye.[/dim]")
            sys.exit(0)

        erase_prompt_echo()

        if not user_input:
            continue

        if user_input.startswith("/"):
            active_chat = handle_command(user_input, store, active_chat, client, config, models)
            continue

        verbose = config.get("verbose", True)
        input_tokens = count_tokens(user_input)
        console.print(Panel(
            Text(user_input, style=theme["user"]),
            title="You",
            title_align="left",
            subtitle=user_footer(user_input, verbose),
            subtitle_align="right",
            border_style=theme["accent_2"],
            box=ROUNDED,
        ))
        add_message(active_chat, "user", user_input, client.model)
        save_chat_store(store)

        response = client.send_message(
            user_input,
            accent=theme["accent"],
            bot_title="GapGPT",
            subtitle=assistant_footer(client.model, models, input_tokens, "", 0.0, 0.0, verbose),
            subtitle_factory=lambda text, input_seconds, output_seconds: assistant_footer(
                client.model,
                models,
                input_tokens,
                text,
                input_seconds,
                output_seconds,
                verbose,
            ),
        )
        if client.chat_token:
            active_chat["chat_token"] = client.chat_token
        if response:
            add_message(active_chat, "assistant", response, client.model)
        active_chat["model"] = client.model
        save_chat_store(store)
        console.print(Rule(style=theme["muted"]))


if __name__ == "__main__":
    main()
