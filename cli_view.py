"""
cli_view.py - Dashboard em modo terminal utilizando a biblioteca Rich.
Ideal para quem gosta de deixar o monitor rodando em um terminal ao lado do código.
"""
import sys
import os
import time
import subprocess
import winsound
import threading
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

from config import get_claude_token
from monitor_core import ClaudeMonitor, UsageData, TokenUsage

console = Console(legacy_windows=False)

# ---- CONFIGURAÇÕES DA BANDEJA DO SISTEMA (SYSTEM TRAY) ----
tray_icon = None
is_hidden = False
keep_running = True

def setup_system_tray():
    if sys.platform != "win32":
        return
        
    try:
        import ctypes
        import pystray
        from PIL import Image, ImageDraw

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()

        def toggle_window(icon, item):
            global is_hidden
            if is_hidden:
                ctypes.windll.user32.ShowWindow(hwnd, 5) # SW_SHOW
                is_hidden = False
                icon.notify("Terminal exibido novamente.")
            else:
                ctypes.windll.user32.ShowWindow(hwnd, 0) # SW_HIDE
                is_hidden = True
                icon.notify("Rodando em segundo plano...")

        def quit_app(icon, item):
            global keep_running
            keep_running = False
            if is_hidden:
                ctypes.windll.user32.ShowWindow(hwnd, 5)
            icon.stop()

        # Criar ícone simples (Quadrado Laranja)
        img = Image.new('RGB', (64, 64), color=(30, 30, 30))
        d = ImageDraw.Draw(img)
        d.rectangle((16, 16, 48, 48), fill=(217, 119, 87))

        menu = pystray.Menu(
            pystray.MenuItem("Ocultar / Mostrar Terminal", toggle_window, default=True),
            pystray.MenuItem("Sair", quit_app)
        )
        
        global tray_icon
        tray_icon = pystray.Icon("Claude Monitor", img, "Claude Usage Monitor", menu)
        tray_icon.run()
    except Exception as e:
        pass

def send_windows_toast(title: str, message: str):
    """Envia uma notificação nativa do Windows usando PowerShell sem dependências extras."""
    if sys.platform != "win32":
        return
    
    ps_script = f'''
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $textNodes = $template.GetElementsByTagName("text")
    $textNodes.Item(0).AppendChild($template.CreateTextNode("{title}")) | Out-Null
    $textNodes.Item(1).AppendChild($template.CreateTextNode("{message}")) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Claude Monitor").Show($toast)
    '''
    try:
        subprocess.Popen(["powershell", "-Command", ps_script], creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        pass

def play_sound(sound_type: str):
    """Toca sons nativos do Windows de acordo com a severidade."""
    if sys.platform != "win32":
        return
    try:
        if sound_type == "success":
            winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
        elif sound_type == "warning":
            winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
        elif sound_type == "error":
            winsound.PlaySound("SystemHand", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        pass

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
                    next_refresh_sec: int, robot_frame: str, robot_text: str) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main", size=14),
        Layout(name="details", size=9),
        Layout(name="footer", size=3)
    )

    # ── Cabeçalho ─────────────────────────────────────────────
    title_text = Text.assemble(
        (" ✦ CLAUDE USAGE MONITOR ", "bold #D97757"),
        ("· Terminal Edition ", "bold white"),
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

    # Tabela de Tokens e Mascote
    token_table = Table.grid(expand=True, padding=(0, 1))
    token_table.add_column(style="dim white", width=14)
    token_table.add_column(style="bold white")
    token_table.add_row("Entrada:", f"{monitor.format_tokens(tokens.input_tokens)} tokens")
    token_table.add_row("Saída:", f"{monitor.format_tokens(tokens.output_tokens)} tokens")
    token_table.add_row("Cache:", f"{monitor.format_tokens(tokens.cache_tokens)} tokens")
    token_table.add_row("Sessões Ativas:", f"{tokens.sessions_count} arquivos")

    # Robozinho Mascote
    mascot_text = Text()
    mascot_text.append("\n")
    mascot_text.append(f" {robot_frame} ", style="bold cyan")
    mascot_text.append_text(Text.from_markup(robot_text))
    
    tokens_content = Layout()
    tokens_content.split_column(
        Layout(token_table),
        Layout(mascot_text)
    )

    tokens_panel = Panel(
        tokens_content,
        title="[bold white]Tokens Locais & Rastreador[/bold white]",
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
    models_table.add_row("", "")
    models_table.add_row("Bandeja:", "[dim]Rodando... (Duplo clique no relógio)[/dim]")

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
        (" para sair (Ou oculte na bandeja)", "dim white")
    )
    footer_panel = Panel(
        Align.center(footer_text),
        border_style="dim white",
        padding=(0, 0)
    )
    layout["footer"].update(footer_panel)

    return layout

def run_cli_loop(poll_interval: int = 120, probe_models: bool = True) -> None:
    global keep_running

    token, origin = get_claude_token()
    if not token:
        console.print("[bold red]Erro:[/] Nenhum token do Claude encontrado!")
        console.print("Faça login com o Claude Code ('claude setup-token' ou 'claude') ou defina $CLAUDE_CODE_OAUTH_TOKEN.")
        sys.exit(1)

    console.print(f"[dim]Autenticado via: {origin}[/dim]")
    
    # Inicia a thread da Bandeja do Sistema (System Tray)
    if sys.platform == "win32":
        tray_thread = threading.Thread(target=setup_system_tray, daemon=True)
        tray_thread.start()
        time.sleep(0.5)
        send_windows_toast("Claude Monitor", "Estou rodando! Clique no ícone perto do relógio para Ocultar/Mostrar a tela preta.")

    monitor = ClaudeMonitor()

    last_api_time = 0.0
    last_local_time = 0.0
    usage = UsageData()
    tokens = TokenUsage()
    probes = []
    incidents = []
    last_update_str = "--:--:--"

    # Estados para acionar notificações
    state_last_h5_reset = 0
    state_warned_80 = False
    state_last_incidents = 0
    
    # Estados do Robozinho / Fast Polling
    last_output_tokens = 0
    robot_state = "idle"
    robot_state_time = 0.0
    robot_last_diff = 0
    tick_counter = 0
    
    # Controle de Debounce (para não apitar enquanto ele estiver digitando/pensando)
    is_working = False
    last_token_increase_time = 0.0

    with Live(console=console, screen=True, auto_refresh=False) as live:
        try:
            while keep_running:
                now = time.time()
                
                # --- FAST POLLING: Ler tokens locais a cada 3 segundos ---
                if now - last_local_time >= 3.0:
                    new_tokens = monitor.collect_local_tokens(usage.h5_reset_epoch)
                    
                    # Detecta se os tokens de saída aumentaram
                    if last_output_tokens > 0 and new_tokens.output_tokens > last_output_tokens:
                        diff = new_tokens.output_tokens - last_output_tokens
                        robot_last_diff += diff
                        last_token_increase_time = now
                        
                        if not is_working:
                            is_working = True
                            robot_state = "working"
                    
                    # Atualiza os estados de controle locais
                    last_output_tokens = new_tokens.output_tokens
                    tokens = new_tokens
                    last_local_time = now

                # --- Lógica de Máquina de Estados do Robozinho ---
                # 1. Se estava trabalhando e ficou 7 segundos sem aumentar os tokens: Terminou!
                if is_working and (now - last_token_increase_time > 7.0):
                    is_working = False
                    robot_state = "finished"
                    robot_state_time = now
                    
                    send_windows_toast("Claude Concluiu Tarefa!", f"A resposta terminou e gerou +{robot_last_diff} tokens.")
                    play_sound("success")
                
                # 2. Se estava comemorando e já passou o tempo (15s): Volta a dormir
                if robot_state == "finished" and (now - robot_state_time > 15.0):
                    robot_state = "idle"
                    robot_last_diff = 0
                
                # --- Frames de Animação ---
                if robot_state == "idle":
                    frames = ["( 🤖 ) z  ", "( 🤖 )  z ", "( 🤖 )   Z"]
                    robot_frame = frames[tick_counter % len(frames)]
                    robot_text = "[dim]Dormindo... (Avisarei quando ele trabalhar!)[/dim]"
                elif robot_state == "working":
                    frames = ["( 🤖 ) ✍️  ", "( 🤖 )  ✍️ ", "( 🤖 )   ✍️"]
                    robot_frame = frames[tick_counter % len(frames)]
                    robot_text = f"[bold yellow]Trabalhando/Escrevendo... (Já usou +{robot_last_diff})[/bold yellow]"
                else: # finished
                    frames = ["\\( 🤖 )/ ✨", " /( 🤖 )\\ 🌟"]
                    robot_frame = frames[tick_counter % len(frames)]
                    robot_text = f"[bold green]Acabou de Responder! (+{robot_last_diff} tokens)[/bold green]"


                # Atualização periódica da API (Pesada, usa internet)
                if now - last_api_time >= poll_interval or last_api_time == 0.0:
                    usage = monitor.fetch_usage(token)
                    if usage.ok:
                        if probe_models:
                            probes = monitor.probe_models(token)
                        incidents = monitor.fetch_incidents()
                        last_update_str = datetime.now().strftime("%H:%M:%S")
                        
                        # ── LÓGICA DE NOTIFICAÇÕES E SONS DA API ──
                        
                        # 1. Reset da janela
                        if state_last_h5_reset != 0 and usage.h5_reset_epoch > state_last_h5_reset:
                            send_windows_toast("Claude Monitor", "Seu limite de 5 horas acabou de resetar! 🎉")
                            play_sound("success")
                            state_warned_80 = False
                        state_last_h5_reset = usage.h5_reset_epoch

                        # 2. Aviso de 80% de limite
                        if usage.h5_utilization >= 80.0 and not state_warned_80:
                            send_windows_toast("Alerta de Limite", f"Você já usou {usage.h5_utilization:.0f}% da sua cota de 5 horas. Vá com calma!")
                            play_sound("warning")
                            state_warned_80 = True
                        elif usage.h5_utilization < 80.0:
                            state_warned_80 = False

                        # 3. Alerta de Incidente nos Servidores
                        if len(incidents) > state_last_incidents:
                            send_windows_toast("Incidente Anthropic", "Problema reportado nos servidores do Claude. Pode haver lentidão.")
                            play_sound("error")
                        state_last_incidents = len(incidents)

                    last_api_time = now

                next_sec = max(0, int(poll_interval - (now - last_api_time)))
                dashboard = build_dashboard(
                    monitor, usage, tokens, probes, incidents,
                    last_update_str, next_sec, robot_frame, robot_text
                )
                live.update(dashboard, refresh=True)
                
                # Loop rápido de 1 segundo para atualizar animações
                for _ in range(10):
                    if not keep_running: break
                    time.sleep(0.1)
                tick_counter += 1
                
        except KeyboardInterrupt:
            pass
        finally:
            if tray_icon:
                tray_icon.stop()

if __name__ == "__main__":
    cfg = load_config()
    run_cli_loop(
        poll_interval=cfg.get("poll_interval_sec", 120),
        probe_models=cfg.get("probe_models", True)
    )
