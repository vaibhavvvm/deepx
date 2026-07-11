"""Rich rendering: banners, tool logs, the persistent cloud/local status line."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from autodev.providers.base import ProviderSpec

console = Console()


def status_line(spec: ProviderSpec, network: str) -> Text:
    """The persistent footer showing active model and data-flow posture."""
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
    return text


def print_status(spec: ProviderSpec, network: str) -> None:
    console.print(status_line(spec, network))


def cloud_switch_warning(spec: ProviderSpec) -> None:
    """The one-line data-leaves-your-machine warning (blueprint 4.3)."""
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
    table = Table(title="autodev doctor", show_header=True, header_style="bold")
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
                ("auto-dev", "bold cyan"),
                (f"  v{version}\n", "dim"),
                ("local-first, provider-agnostic, sandboxed coding agent", "dim"),
            ),
            border_style="cyan",
        )
    )
