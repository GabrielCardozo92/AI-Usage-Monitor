"""
cli_view.py - Dashboard em modo terminal utilizando a biblioteca Rich.
Ideal para quem gosta de deixar o monitor rodando em um terminal ao lado do código.
"""
import sys
import time
from datetime import datetime

# Garantir UTF-8 no terminal Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config import get_claude_token, load_config
from monitor_core import ClaudeMonitor, UsageData, TokenUsage

console = Console(legacy_windows=False)

def get_color_for_pct(pct: float) -> str:
    if pct < 50:
        return "bright_green"
    elif pct < 80:
        return "bright_yellow"
    return "bright_red"

def make_gauge_bar(pct: float, total_bars: int = 24) -> Text:
    filled = int(round((pct / 100.0) * total_bars))
    filled = max(0, min(total_bars, filled))
    empty = total_bars - filled
    color = get_color_for_pct(pct)
    t = Text()
    t.append("█" * filled, style=color)
    t.append("░" * empty, style="dim white")
    return t

def build_dashboard(monitor: ClaudeMonitor, usage: UsageData, tokens: TokenUsage,
                    probes: list, incidents: list, last_update_str: str,
                    next_refresh_sec: int) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main", size=14),
        Layout(name="details", size=7),
        Layout(name="footer", size=3)
    )

    # ── Cabeçalho ─────────────────────────────────────────────
    title_text = Text.assemble(
        (" ✦ CLAUDE USAGE MONITOR ", "bold #D97757"),
        ("· Desktop Edition ", "bold white"),
        (f"[{'ONLINE' if usage.ok else 'ERRO'}]", "bold green" if usage.ok else "bold red")
    )
    header_panel = Panel(
        Align.center(title_text),
        border_style="#D97757",
        padding=(0, 1)
    )
    layout["header"].update(header_panel)

    # ── Principal (Janela 5h e Janela 7d) ──────────────────────
    layout["main"].split_row(
        Layout(name="h5_panel"),
        Layout(name="d7_panel")
    )

    # Janela 5h
    h5_color = get_color_for_pct(usage.h5_utilization)
    now = time.time()
    h5_sec_left = max(0, usage.h5_reset_epoch - now)
    h5_countdown = monitor.format_countdown(h5_sec_left)
    h5_reset_time = datetime.fromtimestamp(usage.h5_reset_epoch).strftime("%H:%M") if usage.h5_reset_epoch else "--:--"
    proj_text, proj_color = monitor.get_projection_text(usage.h5_utilization, usage.h5_reset_epoch)

    h5_content = Text()
    h5_content.append(f"\n   {usage.h5_utilization:.0f}%\n", style=f"bold {h5_color}")
    h5_content.append("   ")
    h5_content.append_text(make_gauge_bar(usage.h5_utilization, 26))
    h5_content.append("\n\n")
    h5_content.append("   ⏳ Reset em: ", style="dim white")
    h5_content.append(f"{h5_countdown} ", style="bold white")
    h5_content.append(f"(às {h5_reset_time})\n", style="dim cyan")
    h5_content.append("   🚦 Status: ", style="dim white")
    h5_content.append(f"{usage.h5_status.upper()}\n", style=f"bold {h5_color}")
    h5_content.append("   🔮 Projeção: ", style="dim white")
    h5_content.append(f"{proj_text}\n", style=f"bold {proj_color}")

    h5_panel = Panel(
        h5_content,
        title="[bold white]Janela de 5 Horas[/bold white]",
        border_style=h5_color,
        subtitle=f"[dim]claim: {usage.representative_claim}[/dim]"
    )
    layout["h5_panel"].update(h5_panel)

    # Janela 7d
    d7_color = get_color_for_pct(usage.d7_utilization)
    d7_sec_left = max(0, usage.d7_reset_epoch - now)
    d7_countdown = monitor.format_countdown(d7_sec_left)
    d7_reset_time = datetime.fromtimestamp(usage.d7_reset_epoch).strftime("%d/%m %H:%M") if usage.d7_reset_epoch else "--:--"

    d7_content = Text()
    d7_content.append(f"\n   {usage.d7_utilization:.0f}%\n", style=f"bold {d7_color}")
    d7_content.append("   ")
    d7_content.append_text(make_gauge_bar(usage.d7_utilization, 26))
    d7_content.append("\n\n")
    d7_content.append("   ⏳ Reset em: ", style="dim white")
    d7_content.append(f"{d7_countdown}\n", style="bold white")
    d7_content.append("   📅 Data do Reset: ", style="dim white")
    d7_content.append(f"{d7_reset_time}\n", style="dim cyan")
    d7_content.append("   🚦 Status: ", style="dim white")
    d7_content.append(f"{usage.d7_status.upper()}\n", style=f"bold {d7_color}")
    d7_content.append("   ⚡ Status Geral: ", style="dim white")
    d7_content.append(f"{usage.unified_status.upper()}\n", style="bold green" if usage.unified_status == "allowed" else "bold yellow")

    d7_panel = Panel(
        d7_content,
        title="[bold white]Janela Semanal (7 Dias)[/bold white]",
        border_style=d7_color
    )
    layout["d7_panel"].update(d7_panel)

    # ── Detalhes (Tokens Locais & Status Modelos) ─────────────
    layout["details"].split_row(
        Layout(name="tokens_panel", ratio=1),
        Layout(name="status_panel", ratio=1)
    )

    # Tabela de Tokens
    token_table = Table.grid(expand=True, padding=(0, 1))
    token_table.add_column(style="dim white", width=14)
    token_table.add_column(style="bold white")
    token_table.add_row("Entrada:", f"{monitor.format_tokens(tokens.input_tokens)} tokens")
    token_table.add_row("Saída:", f"{monitor.format_tokens(tokens.output_tokens)} tokens")
    token_table.add_row("Cache:", f"{monitor.format_tokens(tokens.cache_tokens)} tokens")
    token_table.add_row("Sessões Ativas:", f"{tokens.sessions_count} arquivos")

    tokens_panel = Panel(
        token_table,
        title="[bold white]Tokens na Janela (Claude Code Local)[/bold white]",
        border_style="#4ADE80" if tokens.sessions_count > 0 else "dim white"
    )
    layout["tokens_panel"].update(tokens_panel)

    # Modelos & Incidentes
    models_table = Table.grid(expand=True, padding=(0, 1))
    models_table.add_column(style="dim white", width=12)
    models_table.add_column(style="bold white")

    if probes:
        for p in probes:
            if p.status_code == 200:
                status_style = "bold green"
                lat_str = f"{p.latency_ms}ms"
            elif p.status_code == 429:
                status_style = "bold yellow"
                lat_str = "LIMITADO (429)"
            else:
                status_style = "bold red"
                lat_str = f"ERRO ({p.status_code})"
            models_table.add_row(f"{p.display_name}:", f"[{status_style}]{lat_str}[/]")
    else:
        models_table.add_row("Sonda:", "[dim]Desativada nas configs[/dim]")

    if incidents:
        models_table.add_row("Incidentes:", f"[bold red]{len(incidents)} ativo(s)[/]")
    else:
        models_table.add_row("Incidentes:", "[bold green]Nenhum (status.claude.com OK)[/]")

    status_panel = Panel(
        models_table,
        title="[bold white]Saúde da API & Latência[/bold white]",
        border_style="#D97757"
    )
    layout["status_panel"].update(status_panel)

    # ── Rodapé ────────────────────────────────────────────────
    footer_text = Text.assemble(
        ("Atualizado às ", "dim white"),
        (last_update_str, "bold cyan"),
        (" · Próxima consulta em ", "dim white"),
        (f"{next_refresh_sec}s", "bold yellow"),
        (" · Pressione ", "dim white"),
        ("Ctrl+C", "bold red"),
        (" para sair", "dim white")
    )
    footer_panel = Panel(
        Align.center(footer_text),
        border_style="dim white",
        padding=(0, 0)
    )
    layout["footer"].update(footer_panel)

    return layout

def run_cli_loop(poll_interval: int = 120, probe_models: bool = True) -> None:
    token, origin = get_claude_token()
    if not token:
        console.print("[bold red]Erro:[/] Nenhum token do Claude encontrado!")
        console.print("Faça login com o Claude Code ('claude setup-token' ou 'claude') ou defina $CLAUDE_CODE_OAUTH_TOKEN.")
        sys.exit(1)

    console.print(f"[dim]Autenticado via: {origin}[/dim]")
    monitor = ClaudeMonitor()

    last_api_time = 0.0
    usage = UsageData()
    tokens = TokenUsage()
    probes = []
    incidents = []
    last_update_str = "--:--:--"

    with Live(console=console, screen=True, auto_refresh=False) as live:
        try:
            while True:
                now = time.time()
                # Atualização periódica da API
                if now - last_api_time >= poll_interval or last_api_time == 0.0:
                    usage = monitor.fetch_usage(token)
                    if usage.ok:
                        tokens = monitor.collect_local_tokens(usage.h5_reset_epoch)
                        if probe_models:
                            probes = monitor.probe_models(token)
                        incidents = monitor.fetch_incidents()
                        last_update_str = datetime.now().strftime("%H:%M:%S")
                    last_api_time = now

                next_sec = max(0, int(poll_interval - (now - last_api_time)))
                dashboard = build_dashboard(
                    monitor, usage, tokens, probes, incidents,
                    last_update_str, next_sec
                )
                live.update(dashboard, refresh=True)
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    cfg = load_config()
    run_cli_loop(
        poll_interval=cfg.get("poll_interval_sec", 120),
        probe_models=cfg.get("probe_models", True)
    )
