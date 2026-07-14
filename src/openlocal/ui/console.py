"""Rich rendering: banners, tool logs, the persistent cloud/local status line."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from openlocal.providers.base import ProviderSpec

console = Console()


def status_line(spec: ProviderSpec, network: str, mode: str = "local") -> Text:
    """The persistent footer showing active model, data-flow posture, and mode."""
    text = Text()
    text.append("model ", style="dim")
    text.append(spec.model_string, style="bold")
    text.append("  ")
    if spec.is_local:
        text.append("[local]", style="bold green")
    else:
        text.append(f"[{spec.label}]", style="bold red")
    text.append("  net ", style="dim")
    net_style = "green" if network == "none" else "yellow" if network == "restricted" else "red"
    text.append(network, style=net_style)
    text.append("  mode ", style="dim")
    mode_style = {"local": "green", "smart": "cyan", "web": "yellow"}.get(mode, "white")
    text.append(mode, style=mode_style)
    return text


def print_status(spec: ProviderSpec, network: str, mode: str = "local") -> None:
    console.print(status_line(spec, network, mode))


def cloud_switch_warning(spec: ProviderSpec) -> None:
    """The one-line data-leaves-your-machine warning."""
    console.print(
        Panel(
            Text.assemble(
                ("⚠  Switched to ", "bold yellow"),
                (spec.model_string, "bold"),
                (" (cloud).\n", "bold yellow"),
                (
                    f"Local file contents and commands will now be sent to {spec.name}'s API.",
                    "yellow",
                ),
            ),
            border_style="red",
            title="privacy",
        )
    )


def web_mode_warning() -> None:
    """Privacy notice shown when --mode web is active."""
    console.print(
        Panel(
            Text.assemble(
                ("⚠  Web mode active\n", "bold yellow"),
                (
                    "Search queries will be sent to DuckDuckGo (anonymous, no API key).\n"
                    "File contents are NOT sent to the web — only the search query text.",
                    "yellow",
                ),
            ),
            border_style="yellow",
            title="privacy · web search",
        )
    )


def smart_mode_notice() -> None:
    """Info notice shown when --mode smart is active."""
    console.print(
        "[dim cyan]Smart mode: semantic_search enabled via local embeddings "
        "(100% private, nothing leaves the machine).[/dim cyan]"
    )


def local_switch_notice(spec: ProviderSpec) -> None:
    """Switching *back* to local is quiet -- a single dim line."""
    console.print(
        f"[dim]Switched to {spec.model_string} [local]. Nothing leaves this machine.[/dim]"
    )


def tool_log(tool: str, detail: str = "") -> None:
    console.print(f"[cyan]›[/cyan] [bold]{tool}[/bold] [dim]{detail}[/dim]")


def agent_text(text: str) -> None:
    console.print(text)


def error(msg: str) -> None:
    console.print(f"[bold red]error:[/bold red] {msg}")


def warn(msg: str) -> None:
    console.print(f"[yellow]warning:[/yellow] {msg}")


def info(msg: str) -> None:
    console.print(f"[dim]{msg}[/dim]")


def success(msg: str) -> None:
    console.print(f"[bold green]✓[/bold green] {msg}")


def secret_findings(findings) -> None:
    """Show that secrets were redacted before a cloud call (previews only)."""
    if not findings:
        return
    table = Table(title="redacted before cloud send", show_header=True, header_style="bold red")
    table.add_column("kind")
    table.add_column("preview")
    for f in findings:
        table.add_row(f.kind, f.preview)
    console.print(table)


def doctor_table(rows: list[tuple[str, bool, str]]) -> None:
    table = Table(title="openlocal doctor", show_header=True, header_style="bold")
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail")
    for name, ok, detail in rows:
        mark = "[green]ok[/green]" if ok else "[red]fail[/red]"
        table.add_row(name, mark, detail)
    console.print(table)


def banner(version: str) -> None:
    console.print(
        Panel.fit(
            Text.assemble(
                ("openlocal", "bold cyan"),
                (f"  v{version}\n", "dim"),
                ("local-first, provider-agnostic, sandboxed coding agent", "dim"),
            ),
            border_style="cyan",
        )
    )
