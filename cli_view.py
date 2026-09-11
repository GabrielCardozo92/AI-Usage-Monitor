"""
cli_view.py - Dashboard em modo terminal utilizando a biblioteca Rich.
Ideal para quem gosta de deixar o monitor rodando em um terminal ao lado do código.
Executado a partir do main.py.
"""
import sys
import os
import time
import queue
import subprocess
import winsound
import threading
from datetime import datetime

from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config import get_claude_token
from monitor_core import ClaudeMonitor, ClaudeSession, ServiceStatus, UsageData, TokenUsage
from statusline import context_thresholds, format_window

console = Console(legacy_windows=False)

ROBOT_FRAMES = ["( 🤖 ) zZ ", "( 🤖 )  zZ", "( 🤖 )   z"]

# Alturas do dashboard, em linhas (com as bordas)
HEADER_ROWS, MAIN_ROWS, FOOTER_ROWS = 3, 14, 3
DETAILS_MIN_ROWS = 9  # O painel de saúde usa 7 linhas + bordas
TOKEN_ROWS = 3        # Entrada, Saída, Cache

# ---- CONFIGURAÇÕES DA BANDEJA DO SISTEMA (SYSTEM TRAY) ----
tray_icon = None
is_hidden = False
is_topmost = False
keep_running = True
console_hwnd = None  # Janela que hospeda este terminal, localizada uma única vez

# Constantes Win32
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SW_HIDE = 0
SW_SHOW = 5
SW_RESTORE = 9
GA_ROOTOWNER = 3

_user32 = None

def get_user32():
    """user32 com assinaturas declaradas (HWND de 64 bits não pode ser truncado para int)."""
    global _user32
    if _user32 is None:
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32")
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsIconic.argtypes = [wintypes.HWND]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetAncestor.restype = wintypes.HWND
        _user32 = u
    return _user32

def find_console_window():
    """
    Localiza a janela visível que hospeda este console (conhost clássico ou Windows Terminal).
    Retorna None se não encontrar — nunca cai para a janela em foco, senão o
    'Fixar no Topo' acabaria fixando uma janela qualquer do usuário.
    """
    import ctypes
    from ctypes import wintypes

    user32 = get_user32()
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetConsoleWindow.restype = wintypes.HWND
    kernel32.SetConsoleTitleW.argtypes = [wintypes.LPCWSTR]

    hwnd = kernel32.GetConsoleWindow()
    if hwnd:
        # No Windows Terminal, GetConsoleWindow devolve uma pseudo-janela de tamanho 0
        # (classe PseudoConsoleWindow) cujo dono é a janela real do terminal.
        owner = user32.GetAncestor(hwnd, GA_ROOTOWNER)
        if owner and owner != hwnd and user32.IsWindowVisible(owner):
            return owner

        class_buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, class_buf, 64)
        if class_buf.value != "PseudoConsoleWindow" and user32.IsWindowVisible(hwnd):
            return hwnd  # conhost clássico

    # Versões antigas do Windows Terminal não definem o dono da pseudo-janela.
    # Definimos um título único e procuramos a janela do terminal que passa a exibi-lo.
    title = f"Claude Usage Monitor [{os.getpid()}]"
    kernel32.SetConsoleTitleW(title)

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    def foreach_window(h, _):
        if not user32.IsWindowVisible(h):
            return True
        length = user32.GetWindowTextLengthW(h)
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(h, buff, length + 1)
        if buff.value == title:
            found.append(h)
            return False
        return True

    callback = EnumWindowsProc(foreach_window)
    # O terminal propaga o novo título de forma assíncrona
    for _ in range(30):
        user32.EnumWindows(callback, 0)
        if found:
            return found[0]
        time.sleep(0.1)
    return None

def set_topmost(hwnd, on: bool):
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    get_user32().SetWindowPos(hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0, flags)

def setup_system_tray():
    if sys.platform != "win32":
        return

    try:
        import pystray
        from PIL import Image, ImageDraw

        user32 = get_user32()
        hwnd = console_hwnd
        not_found_msg = "Não foi possível localizar a janela do terminal."

        def toggle_window(icon, item):
            global is_hidden
            if not hwnd:
                icon.notify(not_found_msg)
                return
            if is_hidden:
                user32.ShowWindow(hwnd, SW_SHOW)
                is_hidden = False
                icon.notify("Terminal exibido novamente.")
            else:
                user32.ShowWindow(hwnd, SW_HIDE)
                is_hidden = True
                icon.notify("Rodando em segundo plano...")

        def toggle_topmost(icon, item):
            global is_topmost
            if not hwnd:
                icon.notify(not_found_msg)
                return
            is_topmost = not is_topmost
            set_topmost(hwnd, is_topmost)
            if is_topmost:
                icon.notify("Sempre no topo: ATIVADO. (Sobrevive ao Win+D)")
            else:
                icon.notify("Sempre no topo: DESATIVADO.")

        def quit_app(icon, item):
            global keep_running
            keep_running = False
            if is_hidden and hwnd:
                user32.ShowWindow(hwnd, SW_SHOW)
            icon.stop()

        # Criar ícone simples (Quadrado Laranja)
        img = Image.new('RGB', (64, 64), color=(30, 30, 30))
        d = ImageDraw.Draw(img)
        d.rectangle((16, 16, 48, 48), fill=(217, 119, 87))

        menu = pystray.Menu(
            pystray.MenuItem("Ocultar / Mostrar Terminal", toggle_window, default=True),
            pystray.MenuItem("Fixar no Topo (Ignorar Win+D)", toggle_topmost, checked=lambda item: is_topmost),
            pystray.MenuItem("Sair", quit_app)
        )
        
        global tray_icon
        tray_icon = pystray.Icon("Claude Monitor", img, "Claude Usage Monitor", menu)
        tray_icon.run()
    except Exception:
        pass

def send_windows_toast(title: str, message: str):
    """Envia uma notificação nativa do Windows usando PowerShell sem dependências extras."""
    if sys.platform != "win32":
        return
    
    # Os textos vão por variável de ambiente, nunca interpolados no script:
    # um nome de pasta como "$(comando)" seria executado pelo PowerShell.
    ps_script = '''
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $textNodes = $template.GetElementsByTagName("text")
    $textNodes.Item(0).AppendChild($template.CreateTextNode($env:CLAUDE_MONITOR_TOAST_TITLE)) | Out-Null
    $textNodes.Item(1).AppendChild($template.CreateTextNode($env:CLAUDE_MONITOR_TOAST_MSG)) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Claude Monitor").Show($toast)
    '''
    env = {**os.environ, "CLAUDE_MONITOR_TOAST_TITLE": title, "CLAUDE_MONITOR_TOAST_MSG": message}
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-Command", ps_script],
                         env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        pass

def play_sound(sound_type: str):
    """Toca sons nativos do Windows de acordo com a severidade."""
    if sys.platform != "win32":
        return
    alias = {"success": "SystemAsterisk", "warning": "SystemExclamation", "error": "SystemHand"}.get(sound_type)
    if not alias:
        return
    try:
        winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        pass

def is_waiting(s: ClaudeSession) -> bool:
    """Sessão parada esperando o usuário (autorização, pergunta...)."""
    # Versões antigas do Claude Code não usam "waiting": ficam "idle" com a última
    # resposta parada num tool_use, esperando a autorização da ferramenta.
    return s.status == "waiting" or (s.status == "idle" and s.last_stop_reason == "tool_use")

def waiting_reason(s: ClaudeSession) -> str:
    return s.waiting_for or "Autorização"

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

def make_marked_gauge(pct: float, marks: int = 5, cells_per_mark: int = 5) -> Text:
    """
    Barra com um marcador pontilhado a cada 100/marks % (padrão: a cada 20%).
    O terminal não desenha entre dois caracteres, então cada marcador ocupa uma coluna
    própria depois de cada grupo de blocos; por isso a barra tem marks * cells_per_mark blocos.
    Marcadores já alcançados ficam na cor da barra; os que faltam, apagados.
    """
    total = marks * cells_per_mark
    filled = max(0, min(total, int(round(pct / 100.0 * total))))
    color = get_color_for_pct(pct)
    bar = Text()
    for m in range(marks):
        seg_filled = max(0, min(cells_per_mark, filled - m * cells_per_mark))
        bar.append("█" * seg_filled, style=color)
        bar.append("░" * (cells_per_mark - seg_filled), style="dim white")
        reached = filled >= (m + 1) * cells_per_mark
        bar.append("┊", style=f"bold {color}" if reached else "dim white")
    return bar

def session_sort_key(s: ClaudeSession):
    """Quem espera por você primeiro, depois quem está trabalhando, depois as livres."""
    group = 0 if is_waiting(s) else 1 if s.status == "busy" else 2
    return group, s.name.lower()

def build_dashboard(monitor: ClaudeMonitor, usage: UsageData, tokens: TokenUsage,
                    service: ServiceStatus, last_update_str: str,
                    next_refresh_text: str, active_sessions: dict, robot_frame: str,
                    term_height: int = 0) -> Layout:
    # UsageData() vazio (sem erro) = a primeira consulta ainda não voltou
    loading = not usage.ok and not usage.error_msg

    # O painel de baixo cresce com o número de sessões, até o que cabe no terminal.
    # Por dentro: 3 linhas de tokens, 1 em branco e 1 por sessão (+2 das bordas).
    height = term_height or console.size.height
    wanted = TOKEN_ROWS + 1 + max(1, len(active_sessions)) + 2
    details_rows = max(DETAILS_MIN_ROWS, min(wanted, height - HEADER_ROWS - MAIN_ROWS - FOOTER_ROWS))
    session_capacity = details_rows - 2 - TOKEN_ROWS - 1

    layout = Layout()
    layout.split_column(
        Layout(name="header", size=HEADER_ROWS),
        Layout(name="main", size=MAIN_ROWS),
        Layout(name="details", size=details_rows),
        Layout(name="footer", size=FOOTER_ROWS)
    )

    # ── Cabeçalho ─────────────────────────────────────────────
    title_text = Text.assemble(
        (" ✦ CLAUDE USAGE MONITOR ", "bold #D97757"),
        ("· Terminal Edition ", "bold white"),
        ("[CONECTANDO]", "bold yellow") if loading else
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

    if loading:
        h5_content.append("\n   Consultando a API da Anthropic...\n", style="dim white")
    elif not usage.ok:
        h5_content.append("\n   [FALHA DE COMUNICAÇÃO]\n", style="bold red")
        h5_content.append(f"   {usage.error_msg}\n\n", style="dim red")
        h5_content.append("   O monitor tentará reconectar\n   automaticamente em 10 segundos...", style="dim white")
    else:
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
        subtitle=f"[dim]claim: {usage.representative_claim}[/dim]" if usage.representative_claim else None
    )
    layout["h5_panel"].update(h5_panel)

    # Janela 7d
    d7_color = get_color_for_pct(usage.d7_utilization)
    d7_sec_left = max(0, usage.d7_reset_epoch - now)
    d7_countdown = monitor.format_countdown(d7_sec_left)
    d7_reset_time = datetime.fromtimestamp(usage.d7_reset_epoch).strftime("%d/%m %H:%M") if usage.d7_reset_epoch else "--:--"

    d7_content = Text()
    if loading:
        d7_content.append("\n   Consultando a API da Anthropic...\n", style="dim white")
    elif not usage.ok:
        d7_content.append("\n   [DADOS INDISPONÍVEIS]\n", style="bold red")
        d7_content.append("   Sem conexão com Anthropic API.\n", style="dim white")
    else:
        d7_content.append(f"\n   {usage.d7_utilization:.0f}%\n", style=f"bold {d7_color}")
        d7_content.append("   ")
        d7_content.append_text(make_marked_gauge(usage.d7_utilization))  # Marcadores a cada 20%
        d7_content.append("\n\n")
        d7_content.append("   ⏳ Reset em: ", style="dim white")
        d7_content.append(f"{d7_countdown}\n", style="bold white")
        d7_content.append("   📅 Data do Reset: ", style="dim white")
        d7_content.append(f"{d7_reset_time}\n", style="dim cyan")
        d7_proj, d7_proj_color = monitor.get_weekly_projection_text(usage.d7_utilization, usage.d7_reset_epoch)
        d7_content.append("   🔮 Projeção: ", style="dim white")
        d7_content.append(f"{d7_proj}\n", style=f"bold {d7_proj_color}")
        d7_content.append("   📊 Por dia: ", style="dim white")
        d7_content.append(f"{monitor.get_daily_budget_text(usage.d7_utilization, usage.d7_reset_epoch)}\n", style="bold white")
        # Os status só aparecem como alerta: "allowed" não acrescenta nada à porcentagem
        # (e, com a status line, é deduzido dela). A API pode mandar "allowed_warning".
        if usage.d7_status not in ("allowed", "unknown"):
            d7_content.append("   🚦 Status: ", style="dim white")
            d7_content.append(f"{usage.d7_status.upper()}\n", style="bold red" if usage.d7_status == "rejected" else "bold yellow")
        if usage.unified_status not in ("allowed", "unknown"):
            d7_content.append("   ⚡ Status Geral: ", style="dim white")
            d7_content.append(f"{usage.unified_status.upper()}\n", style="bold red" if usage.unified_status == "rejected" else "bold yellow")

    d7_panel = Panel(
        d7_content,
        title="[bold white]Janela Semanal (7 Dias)[/bold white]",
        border_style=d7_color
    )
    layout["d7_panel"].update(d7_panel)

    # ── Detalhes (Tokens Locais & Saúde da API) ───────────────
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
    
    # Monta a lista visual das sessões: uma linha por sessão, cortada com "…" se não couber
    # (uma linha quebrada empurraria as sessões seguintes para fora do painel)
    mascot_text = Text(no_wrap=True, overflow="ellipsis")
    mascot_text.append("\n")
    if not active_sessions:
        mascot_text.append(f" {robot_frame} ", style="bold cyan")
        mascot_text.append(" Nenhum terminal rodando Claude...", style="dim")
    else:
        ordered = sorted(active_sessions.values(), key=session_sort_key)
        # Se não couber tudo, a última linha vira o aviso de quantas ficaram de fora
        shown = ordered if len(ordered) <= session_capacity else ordered[:session_capacity - 1]
        for s in shown:
            folder_name = s.name
            if len(folder_name) > 15:
                folder_name = folder_name[:12] + "..."

            # Formatação do Contexto (avisos calibrados pela janela da sessão, se conhecida)
            ctx_used = monitor.format_tokens(s.context_tokens)
            if s.context_window:
                ctx_str = f"[{ctx_used}/{format_window(s.context_window)} ctx]"
            else:
                ctx_str = f"[{ctx_used} ctx]"
            warn, crit = context_thresholds(s.context_window)
            if s.context_tokens > crit:
                ctx_style = "bold red"
                ctx_str += " ⚠️"
            elif s.context_tokens > warn:
                ctx_style = "bold yellow"
            else:
                ctx_style = "dim white"

            if s.status == "busy":
                # Calcula o tempo decorrido
                elapsed = max(0, int(time.time() - s.status_updated_at)) if s.status_updated_at > 0 else 0
                m, sec = divmod(elapsed, 60)
                icon, state, style = "✍️", f"Trabalhando... {m:02d}:{sec:02d}", "bold yellow"
            elif is_waiting(s):
                icon, state, style = "⏳", f"Aguardando você - {waiting_reason(s)}", "bold magenta"
            else:
                icon, state, style = "✨", "Livre", "bold green"

            # Do mais para o menos importante: se a linha não couber, o "…" corta o modelo
            mascot_text.append(f" {icon}  ", style=style)
            mascot_text.append(f"{folder_name} ", style="bold white")
            mascot_text.append(f"({state}) ", style=style)
            mascot_text.append(f"{ctx_str} ", style=ctx_style)
            if s.model:
                mascot_text.append(f"[{s.model.replace('claude-', '').title()}]", style="dim cyan")
            mascot_text.append("\n")

        hidden = len(ordered) - len(shown)
        if hidden:
            plural = "sessão" if hidden == 1 else "sessões"
            mascot_text.append(f" … +{hidden} {plural} sem espaço (aumente a janela do terminal)\n", style="dim")

    tokens_content = Layout()
    tokens_content.split_column(
        Layout(token_table, size=3),
        Layout(mascot_text)
    )

    tokens_panel = Panel(
        tokens_content,
        title="[bold white]Tokens Locais & Sessões[/bold white]",
        border_style="#4ADE80" if tokens.sessions_count > 0 else "dim white"
    )
    layout["tokens_panel"].update(tokens_panel)

    # Latência & Status dos serviços (status.claude.com)
    health_table = Table.grid(expand=True, padding=(0, 1))
    health_table.add_column(style="dim white", width=14, min_width=14, no_wrap=True)
    # O painel tem altura fixa: texto longo é cortado com "…" em vez de quebrar a linha.
    # ratio=1 faz esta coluna ficar só com o espaço restante, sem espremer a de nomes.
    health_table.add_column(style="bold white", ratio=1, no_wrap=True, overflow="ellipsis")

    if not usage.ok:
        health_table.add_row("Fonte:", "[dim]--[/dim]")
    elif usage.source == "status line":
        age = max(0, int(time.time() - usage.timestamp))
        health_table.add_row("Fonte:", f"[bold white]Status line do Claude Code[/] [dim]· há {age}s[/dim]")
    else:
        health_table.add_row("Fonte:", f"[bold white]API[/] [dim]· {usage.latency_ms}ms (Haiku)[/dim]")

    if service.ok:
        for name, status in service.components:
            label, color = monitor.component_status_display(status)
            health_table.add_row(f"{name}:", f"[bold {color}]{label}[/]")
        worst = service.worst_incident()
        if worst:
            count = len(service.incidents)
            where = escape(", ".join(worst.components) or "serviço não informado")
            health_table.add_row(
                "Incidentes:",
                f"[bold red]{count} ativo{'s' if count > 1 else ''}[/] [dim]· {where} · {worst.status_display}[/dim]"
            )
            prefix = "mais grave: " if count > 1 else ""
            health_table.add_row("", f"[red]⚠ {prefix}{escape(worst.display_name)}[/]")
        else:
            health_table.add_row("Incidentes:", "[bold green]Nenhum[/]")
    else:
        health_table.add_row("Status:", "[dim]status.claude.com indisponível[/dim]")

    status_panel = Panel(
        health_table,
        title="[bold white]Saúde da API & Latência[/bold white]",
        border_style="#D97757"
    )
    layout["status_panel"].update(status_panel)

    # ── Rodapé ────────────────────────────────────────────────
    footer_text = Text.assemble(
        ("Atualizado às ", "dim white"),
        (last_update_str, "bold cyan"),
        (" · Próxima consulta em ", "dim white"),
        (next_refresh_text, "bold yellow"),
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

def fetch_api_data(monitor: ClaudeMonitor, manual_token: str):
    """
    Consulta a API em segundo plano (roda fora do loop da tela).
    Nunca levanta exceção: qualquer falha vira um UsageData/ServiceStatus com ok=False.
    Retorna (usage, service).
    """
    try:
        # Fonte oficial primeiro: o uso que o próprio Claude Code passa para a status line.
        # A API só é consultada quando nenhuma sessão local tem dado recente.
        usage = monitor.read_statusline_usage()
        if usage is None:
            # Relê o token a cada consulta: o Claude Code renova o access token
            # periodicamente e o antigo passa a devolver 401.
            token, _ = get_claude_token(manual_token)
            if token:
                usage = monitor.fetch_usage(token)
            else:
                usage = UsageData(ok=False, error_msg="Nenhum token do Claude encontrado")
    except Exception as e:
        usage = UsageData(ok=False, error_msg=f"Erro inesperado: {e}")
    # O status page é consultado mesmo se a API falhar: é justamente quando ele explica o porquê
    try:
        service = monitor.fetch_service_status()
    except Exception:
        service = ServiceStatus()
    try:
        # Roda aqui, fora da tela: um nome novo leva alguns segundos para traduzir
        monitor.translate_incidents(service)
    except Exception:
        pass  # Sem tradução, o nome aparece em inglês
    return usage, service

class UsageAlerts:
    """Notificações ligadas ao uso de 5h: reset da janela e aviso de 80%."""

    def __init__(self):
        self.last_h5_reset = 0
        self.warned_80 = False

    def check(self, usage: UsageData) -> None:
        if not usage.ok or not usage.h5_reset_epoch:
            return
        # Uma janela nova termina horas depois da anterior; a margem evita alarme falso
        # quando API e status line informam o mesmo reset com segundos de diferença.
        if self.last_h5_reset and usage.h5_reset_epoch > self.last_h5_reset + 3600:
            send_windows_toast("Claude Monitor", "Seu limite de 5 horas acabou de resetar! 🎉")
            play_sound("success")
            self.warned_80 = False
        self.last_h5_reset = usage.h5_reset_epoch

        if usage.h5_utilization >= 80.0 and not self.warned_80:
            send_windows_toast("Alerta de Limite", f"Você já usou {usage.h5_utilization:.0f}% da sua cota de 5 horas. Vá com calma!")
            play_sound("warning")
            self.warned_80 = True
        elif usage.h5_utilization < 80.0:
            self.warned_80 = False

def run_cli_loop(poll_interval: int = 120, manual_token: str = "") -> None:
    global keep_running, console_hwnd

    token, origin = get_claude_token(manual_token)
    if not token:
        console.print("[bold red]Erro:[/] Nenhum token do Claude encontrado!")
        console.print("Faça login com o Claude Code ('claude setup-token' ou 'claude') ou defina $CLAUDE_CODE_OAUTH_TOKEN.")
        sys.exit(1)

    console.print(f"[dim]Autenticado via: {origin}[/dim]")

    # Inicia a thread da Bandeja do Sistema (System Tray)
    if sys.platform == "win32":
        console_hwnd = find_console_window()
        threading.Thread(target=setup_system_tray, daemon=True).start()
        time.sleep(0.5)
        send_windows_toast("Claude Monitor", "Estou rodando! Clique no ícone perto do relógio para Ocultar/Mostrar a tela preta.")

    monitor = ClaudeMonitor()

    last_api_time = 0.0
    last_local_time = 0.0
    usage = UsageData()
    tokens = TokenUsage()
    service = ServiceStatus()
    last_update_str = "--:--:--"

    alerts = UsageAlerts()
    last_statusline_check = 0.0

    # Frame da animação do robozinho
    tick_counter = 0

    # Controle Exato de Status (via sessões do Claude Code)
    last_sessions = {}

    # A consulta à API roda numa thread e entrega o resultado por esta fila,
    # para a tela, as sessões e o "Fixar no Topo" não congelarem durante a rede.
    api_results = queue.Queue()
    fetching = False

    with Live(console=console, screen=True, auto_refresh=False) as live:
        try:
            while keep_running:
                now = time.time()
                
                # --- FAST POLLING: Lendo Status da Sessão (A cada 1 segundo) ---
                current_sessions = monitor.get_claude_sessions()
                
                for pid, sess in current_sessions.items():
                    prev_sess = last_sessions.get(pid)
                    if not prev_sess:
                        continue
                    if is_waiting(sess) and not is_waiting(prev_sess):
                        # Parou no meio da tarefa esperando o usuário (autorização, pergunta...)
                        send_windows_toast(f"Claude aguardando você: {sess.name}", f"Motivo: {waiting_reason(sess)}")
                        play_sound("warning")
                    elif prev_sess.status == "busy" and sess.status != "busy" and not is_waiting(sess):
                        # Acabou de terminar uma tarefa nessa sessão! O status final pode ser
                        # "idle" ou "shell" (ocioso, mas com um comando rodando em segundo plano).
                        send_windows_toast(f"Tarefa Concluída: {sess.name}", "O Claude terminou o processo e aguarda comando.")
                        play_sound("success")
                
                last_sessions = current_sessions
                
                # Polling de Tokens (A cada 3 segundos)
                if now - last_local_time >= 3.0:
                    tokens = monitor.collect_local_tokens(usage.h5_reset_epoch)
                    last_local_time = now

                # --- Frames de Animação para o caso "Vazio/Offline" ---
                robot_frame = ROBOT_FRAMES[tick_counter % len(ROBOT_FRAMES)]

                # Status line: local e gratuita, então é conferida a cada 5s em vez de
                # esperar o intervalo da API. Só troca o uso se houver dado recente.
                if now - last_statusline_check >= 5.0:
                    last_statusline_check = now
                    statusline_usage = monitor.read_statusline_usage()
                    if statusline_usage:
                        usage = statusline_usage
                        last_update_str = datetime.fromtimestamp(usage.timestamp).strftime("%H:%M:%S")
                        alerts.check(usage)

                # Atualização periódica da API (Pesada, usa internet) — dispara em segundo plano
                if not fetching and (now - last_api_time >= poll_interval or last_api_time == 0.0):
                    fetching = True
                    threading.Thread(
                        target=lambda: api_results.put(fetch_api_data(monitor, manual_token)),
                        daemon=True
                    ).start()

                # Resultado da consulta chegou?
                try:
                    usage, new_service = api_results.get_nowait()
                except queue.Empty:
                    pass
                else:
                    fetching = False
                    now = time.time()

                    # Só atualiza se o status page respondeu: uma falha de rede não pode
                    # zerar a contagem de incidentes e depois realertar os mesmos.
                    if new_service.ok:
                        # Compara por id: um incidente resolvido e outro aberto na mesma
                        # consulta mantêm a contagem, mas o novo ainda precisa de aviso.
                        known = {i.id for i in service.incidents}
                        new = [i for i in new_service.incidents if i.id not in known]
                        if new:
                            first = new[0]
                            msg = first.display_name
                            if first.components:
                                msg += f" ({', '.join(first.components)})"
                            if len(new) > 1:
                                msg += f" e mais {len(new) - 1}"
                            send_windows_toast("Incidente na Anthropic", msg)
                            play_sound("error")
                        service = new_service

                    if usage.ok:
                        last_update_str = datetime.fromtimestamp(usage.timestamp).strftime("%H:%M:%S")
                        alerts.check(usage)
                        last_api_time = now
                    else:
                        # Falhou. Em vez de esperar 120s, espera só 10s pra tentar de novo.
                        last_api_time = now - poll_interval + 10

                if fetching:
                    next_refresh_text = "consultando..."
                else:
                    next_refresh_text = f"{max(0, int(poll_interval - (now - last_api_time)))}s"
                dashboard = build_dashboard(
                    monitor, usage, tokens, service,
                    last_update_str, next_refresh_text, current_sessions, robot_frame
                )
                live.update(dashboard, refresh=True)
                
                # Loop rápido de 1 segundo para atualizar animações
                for _ in range(10):
                    if not keep_running: break

                    # Combater o Win+D agressivo do Windows
                    if is_topmost and console_hwnd:
                        user32 = get_user32()
                        if user32.IsIconic(console_hwnd):
                            user32.ShowWindow(console_hwnd, SW_RESTORE)

                        # SWP_NOACTIVATE força a janela a ficar acima do Desktop
                        # (que o Win+D joga pra frente) sem roubar o foco do teclado.
                        set_topmost(console_hwnd, True)

                    time.sleep(0.1)
                tick_counter += 1

        except KeyboardInterrupt:
            pass
        finally:
            # Se o monitor rodou dentro de um terminal já aberto, ele continua
            # existindo após sair: não pode ficar preso no topo nem oculto.
            if console_hwnd:
                if is_topmost:
                    set_topmost(console_hwnd, False)
                if is_hidden:
                    get_user32().ShowWindow(console_hwnd, SW_SHOW)
            if tray_icon:
                tray_icon.stop()
