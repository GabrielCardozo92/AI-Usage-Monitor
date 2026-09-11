"""Renderização do dashboard e um teste de fumaça do loop principal."""
import contextlib
import io
import threading
import time

from rich.console import Console

import cli_view
import monitor_core
from conftest import session
from monitor_core import ClaudeMonitor, Incident, ServiceStatus, TokenUsage, UsageData

def render(usage=None, sessions=None, service=None, height=40, width=140, projects=None) -> str:
    now = time.time()
    usage = usage or UsageData(ok=True, h5_utilization=22, h5_reset_epoch=int(now) + 9000,
                               d7_utilization=16, d7_reset_epoch=int(now) + 3 * 86400)
    c = Console(file=io.StringIO(), width=width, height=height, color_system=None, legacy_windows=False)
    c.print(cli_view.build_dashboard(ClaudeMonitor(), usage, TokenUsage(), service or ServiceStatus(ok=True),
                                     "12:00:00", "100s", sessions or {}, "", term_height=height, projects=projects))
    return c.file.getvalue()

def test_marked_gauge():
    assert cli_view.make_marked_gauge(20).plain == "█████┊░░░░░┊░░░░░┊░░░░░┊░░░░░┊"
    assert cli_view.make_marked_gauge(63).plain == "█████┊█████┊█████┊█░░░░┊░░░░░┊"
    bar = cli_view.make_marked_gauge(63)
    markers = [str(s.style) for s in bar.spans if bar.plain[s.start:s.end] == "┊"]
    assert markers == ["bold bright_yellow"] * 3 + ["dim white"] * 2   # alcançados na cor da barra

def test_format_projects():
    assert cli_view.format_projects([("a", .62), ("bb", .30), ("c", .05), ("d", .03)]) == "a 62% · bb 30% · c 5% · outros 3%"
    assert cli_view.format_projects([("claude-usage-monitor", .993), ("x", .007)]) == "claude-usage-… 99%"
    assert cli_view.format_projects([]) == ""

def test_loading_error_and_ok_states():
    assert "[CONECTANDO]" in render(UsageData())
    err = render(UsageData(ok=False, error_msg="Token inválido ou expirado (HTTP 401)"))
    assert "[ERRO]" in err and "HTTP 401" in err
    ok = render(projects=[("alpha", 0.9), ("beta", 0.1)])
    assert "[ONLINE]" in ok and "Chega a ~" in ok and "alpha 90% · beta 10%" in ok

def test_sessions_sorted_and_overflow_reported():
    sessions = {i: s for i, s in enumerate([
        session(1, "zeta", "idle"), session(2, "api", "busy"), session(3, "front", "waiting", waiting_for="input needed"),
        session(4, "docs", "shell"), session(5, "auto", "busy"), session(6, "old", "idle", stop="tool_use"),
        session(7, "bi", "idle")], start=1)}
    tall = render(sessions=sessions, height=40)
    order = [name for line in tall.splitlines() for name in ("front", "old", "api", "auto", "bi", "docs", "zeta")
             if f"  {name} (" in line]
    assert order == ["front", "old", "api", "auto", "bi", "docs", "zeta"]   # aguardando > trabalhando > livres
    short = render(sessions=sessions, height=30)
    assert "+4 sessões sem espaço" in short

def test_health_panel_shows_translated_incident():
    inc = Incident("Degraded functionality for Claude Cowork on Windows", "identified", "major", "", "x", ["Claude Cowork"])
    inc.name_pt = "Funcionalidade degradada"
    out = render(service=ServiceStatus(ok=True, components=[("Claude Cowork", "partial_outage")], incidents=[inc]))
    assert "Falha parcial" in out and "1 ativo · Claude Cowork · identificado" in out and "⚠ Funcionalidade degradada" in out

def test_loop_shows_api_error_and_exits_quickly(monkeypatch, toasts):
    """Liga o loop de verdade com uma API que falha: a tela mostra o erro e o loop encerra na hora."""
    def failing_fetch(self, token):
        raise RuntimeError("falha simulada")
    monkeypatch.setattr(monitor_core.ClaudeMonitor, "read_statusline_usage", lambda self: None)
    monkeypatch.setattr(monitor_core.ClaudeMonitor, "fetch_usage", failing_fetch)
    monkeypatch.setattr(monitor_core.ClaudeMonitor, "fetch_service_status", lambda self: ServiceStatus(ok=True))
    monkeypatch.setattr(cli_view, "get_claude_token", lambda manual=None: ("token", "teste"))
    monkeypatch.setattr(cli_view, "find_console_window", lambda: None)
    monkeypatch.setattr(cli_view, "setup_system_tray", lambda: None)
    monkeypatch.setattr(cli_view, "Live", lambda **kw: contextlib.nullcontext(
        type("L", (), {"update": lambda self, *a, **k: None})()))
    seen = []
    original = cli_view.build_dashboard
    monkeypatch.setattr(cli_view, "build_dashboard", lambda m, u, *a, **k: seen.append(u.error_msg) or original(m, u, *a, **k))

    threading.Timer(2.0, lambda: setattr(cli_view, "keep_running", False)).start()
    start = time.time()
    cli_view.run_cli_loop(poll_interval=120)
    assert time.time() - start < 4
    assert "Erro inesperado: falha simulada" in seen
