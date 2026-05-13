"""Rich-powered renderer for WaveRunner streaming events."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console, Group
from rich.json import JSON
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .models import (
    ContentDeltaEvent,
    ContentStartEvent,
    ContentStopEvent,
    FunctionCallDelta,
    FunctionResultDelta,
    InteractionCompleteEvent,
    InteractionStartEvent,
    InteractionStatusUpdateEvent,
    StreamEvent,
    TextDelta,
    ThoughtSummaryDelta,
)

_STATUS_STYLE = {
    "in_progress": "yellow",
    "completed": "green",
    "failed": "red",
    "cancelled": "red",
}


def _fmt_args(args: Any) -> str:
    if isinstance(args, str):
        return args
    try:
        return json.dumps(args, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(args)


class StreamRenderer:
    """Consume `StreamEvent`s and render them prettily to the console.

    Keeps per-content-index buffers so that streamed deltas (text, thoughts,
    tool calls, tool results) are rendered as cohesive blocks instead of one
    line per delta.
    """

    def __init__(
        self, console: Console | None = None, title: str = "WaveRunner"
    ) -> None:
        self.console = console or Console()
        self.title = title
        # index -> { "kind": str, "buffer": str, "meta": dict }
        self._blocks: dict[int, dict[str, Any]] = {}
        self._live: Live | None = None
        self._current_index: int | None = None

    # ------------------------------------------------------------------ live
    def __enter__(self) -> "StreamRenderer":
        self._live = Live(
            Spinner("dots", text=Text(f"Connecting to {self.title}…", style="dim")),
            console=self.console,
            refresh_per_second=12,
            transient=True,
        )
        self._live.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._live is not None:
            self._live.__exit__(exc_type, exc, tb)
            self._live = None

    # ----------------------------------------------------------- dispatch
    def handle(self, event: StreamEvent) -> None:
        if isinstance(event, InteractionStartEvent):
            self._on_interaction_start(event)
        elif isinstance(event, InteractionStatusUpdateEvent):
            self._on_status_update(event)
        elif isinstance(event, ContentStartEvent):
            self._on_content_start(event)
        elif isinstance(event, ContentDeltaEvent):
            self._on_content_delta(event)
        elif isinstance(event, ContentStopEvent):
            self._on_content_stop(event)
        elif isinstance(event, InteractionCompleteEvent):
            self._on_interaction_complete(event)

    # ----------------------------------------------------- event handlers
    def _on_interaction_start(self, event: InteractionStartEvent) -> None:
        i = event.interaction
        header = Table.grid(padding=(0, 1))
        header.add_column(style="bold cyan")
        header.add_column()
        header.add_row("interaction", i.id)
        if i.environment_id:
            header.add_row("environment", i.environment_id)
        if i.agent:
            header.add_row("agent", i.agent)
        self._print_static(
            Panel(header, title=f"▶ {self.title} started", border_style="cyan")
        )
        self._set_spinner(f"interaction {i.id} • {i.status}")

    def _on_status_update(self, event: InteractionStatusUpdateEvent) -> None:
        style = _STATUS_STYLE.get(event.status, "dim")
        self._set_spinner(Text(f"status: {event.status}", style=style))

    def _on_content_start(self, event: ContentStartEvent) -> None:
        # If switching to a new index, flush whatever block was being built.
        if self._current_index is not None and self._current_index != event.index:
            self._flush_block(self._current_index)

        self._current_index = event.index
        ctype = event.content.type
        self._blocks[event.index] = {
            "kind": ctype,
            "buffer": "",
            "meta": {"id": event.content.id},
        }
        self._refresh_live()

    def _on_content_delta(self, event: ContentDeltaEvent) -> None:
        block = self._blocks.setdefault(
            event.index, {"kind": event.delta.type, "buffer": "", "meta": {}}
        )
        delta = event.delta
        if isinstance(delta, TextDelta):
            block["kind"] = "text"
            block["buffer"] += delta.text
        elif isinstance(delta, ThoughtSummaryDelta):
            block["kind"] = "thought"
            if delta.content.text not in block["buffer"]:
                block["buffer"] += delta.content.text
        elif isinstance(delta, FunctionCallDelta):
            block["kind"] = "function_call"
            block["meta"]["name"] = delta.name or block["meta"].get("name")
            block["meta"]["id"] = delta.id or block["meta"].get("id")
            if delta.arguments is not None:
                # arguments can stream as full dict each delta or as a chunked string
                if isinstance(delta.arguments, str):
                    block["buffer"] += delta.arguments
                else:
                    block["meta"]["arguments"] = delta.arguments
        elif isinstance(delta, FunctionResultDelta):
            block["kind"] = "function_result"
            block["meta"]["name"] = delta.name or block["meta"].get("name")
            if delta.result is not None:
                block["meta"]["result"] = delta.result
        self._refresh_live()

    def _on_content_stop(self, event: ContentStopEvent) -> None:
        self._flush_block(event.index)
        if self._current_index == event.index:
            self._current_index = None
        self._refresh_live()

    def _on_interaction_complete(self, event: InteractionCompleteEvent) -> None:
        # Flush any leftover open blocks.
        for idx in list(self._blocks.keys()):
            self._flush_block(idx)

        i = event.interaction
        style = _STATUS_STYLE.get(i.status, "green")
        body: list[Any] = []
        if i.usage:
            usage = i.usage
            t = Table.grid(padding=(0, 2))
            t.add_column(style="bold")
            t.add_column(justify="right")
            for label, value in [
                ("total", usage.total_tokens),
                ("input", usage.total_input_tokens),
                ("output", usage.total_output_tokens),
                ("cached", usage.total_cached_tokens),
                ("tool use", usage.total_tool_use_tokens),
                ("thoughts", usage.total_thought_tokens),
            ]:
                if value is not None:
                    t.add_row(label, str(value))
            body.append(t)
        self._print_static(
            Panel(
                Group(*body) if body else Text("(no usage reported)", style="dim"),
                title=f"■ interaction {i.status}",
                border_style=style,
            )
        )

    # ---------------------------------------------------------- rendering
    def _flush_block(self, index: int) -> None:
        block = self._blocks.pop(index, None)
        if block is None:
            return
        renderable = self._render_block(block)
        if renderable is not None:
            self._print_static(renderable)

    def _render_block(self, block: dict[str, Any]) -> Any:
        kind = block["kind"]
        buf = block["buffer"]
        meta = block["meta"]
        if kind == "text":
            text = buf.strip()
            if not text:
                return None
            return Panel(Markdown(text), title="✶ assistant", border_style="green")
        if kind == "thought":
            text = buf.strip()
            if not text:
                return None
            return Panel(
                Markdown(text),
                title="✦ thinking",
                border_style="magenta",
                style="dim",
            )
        if kind == "function_call":
            name = meta.get("name") or "?"
            args = meta.get("arguments")
            if args is None and buf:
                try:
                    args = json.loads(buf)
                except json.JSONDecodeError:
                    args = buf
            args_render: Any
            if isinstance(args, (dict, list)):
                args_render = JSON.from_data(args)
            elif args:
                args_render = Syntax(_fmt_args(args), "json", theme="ansi_dark")
            else:
                args_render = Text("(no arguments)", style="dim")
            header = Text.assemble(("→ tool call: ", "bold"), (name, "bold yellow"))
            if meta.get("id"):
                header.append(f"  [{meta['id']}]", style="dim")
            return Panel(args_render, title=header, border_style="yellow")
        if kind == "function_result":
            name = meta.get("name") or "?"
            result = meta.get("result")
            if isinstance(result, (dict, list)):
                result_render: Any = JSON.from_data(result)
            elif result is None:
                result_render = Text("(no result)", style="dim")
            else:
                result_render = Text(str(result))
            header = Text.assemble(("← tool result: ", "bold"), (name, "bold blue"))
            return Panel(result_render, title=header, border_style="blue")
        return None

    # -------------------------------------------------------- live helpers
    def _set_spinner(self, message: Any) -> None:
        if self._live is None:
            return
        text = message if isinstance(message, Text) else Text(str(message), style="dim")
        self._live.update(Spinner("dots", text=text))

    def _refresh_live(self) -> None:
        if self._live is None or self._current_index is None:
            return
        block = self._blocks.get(self._current_index)
        if not block:
            return
        kind = block["kind"]
        preview = block["buffer"]
        if kind == "text":
            label = "writing response…"
        elif kind == "thought":
            label = "thinking…"
        elif kind == "function_call":
            label = f"calling {block['meta'].get('name') or 'tool'}…"
        elif kind == "function_result":
            label = f"received result from {block['meta'].get('name') or 'tool'}"
        else:
            label = "streaming…"
        snippet = preview.strip().splitlines()[-1] if preview.strip() else ""
        text = Text(label, style="dim")
        if snippet:
            text.append("  ")
            text.append(snippet[-80:], style="white")
        self._set_spinner(text)

    def _print_static(self, renderable: Any) -> None:
        """Print above the live spinner so output is preserved."""
        if self._live is not None:
            self._live.console.print(renderable)
        else:
            self.console.print(renderable)


def render_raw_line(console: Console, line: str) -> None:
    """Fallback renderer used when a payload can't be parsed as a known event."""
    console.print(Rule("raw", style="red"))
    console.print()
    console.print(Syntax(line, "json", theme="ansi_dark", word_wrap=True))
    console.print()
    console.print(Rule("", style="red"))
    console.print()
