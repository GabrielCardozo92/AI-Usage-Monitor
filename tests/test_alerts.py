"""Notificações: uso (5h e semanal), sessões e incidentes."""
import time

import cli_view
from conftest import session
from monitor_core import ClaudeMonitor, Incident, ServiceStatus, UsageData

def titles(sent):
    return [t for t, _ in sent]

def test_5h_reset_ignores_small_differences_between_sources(toasts):
    a = cli_view.UsageAlerts()
    base = 1_800_000_000
    for pct, reset in [(50, base), (52, base + 20), (53, base)]:   # API e status line, mesmo reset
        a.check(UsageData(ok=True, h5_utilization=pct, h5_reset_epoch=reset))
    assert toasts == []
    a.check(UsageData(ok=True, h5_utilization=1, h5_reset_epoch=base + 5 * 3600))
    assert toasts == [("Claude Monitor", "Seu limite de 5 horas acabou de resetar! 🎉")]

def test_5h_80_percent_alert_once(toasts):
    a = cli_view.UsageAlerts()
    reset = int(time.time()) + 3600
    for pct in (79, 81, 90, 85):
        a.check(UsageData(ok=True, h5_utilization=pct, h5_reset_epoch=reset))
    assert titles(toasts) == ["Alerta de Limite"]

def test_weekly_80_percent_alert_once_per_week(toasts):
    a = cli_view.UsageAlerts()
    reset = int(time.time()) + 3 * 86400
    for pct in (70, 81, 85):
        a.check(UsageData(ok=True, d7_utilization=pct, d7_reset_epoch=reset))
    assert titles(toasts) == ["Alerta de Limite Semanal"]
    next_week = reset + 7 * 86400
    a.check(UsageData(ok=True, d7_utilization=2, d7_reset_epoch=next_week))
    a.check(UsageData(ok=True, d7_utilization=80, d7_reset_epoch=next_week))
    assert titles(toasts) == ["Alerta de Limite Semanal"] * 2

def test_failed_usage_does_not_alert(toasts):
    cli_view.UsageAlerts().check(UsageData(ok=False, h5_utilization=99, d7_utilization=99))
    assert toasts == []

def run_sessions(states, toasts):
    alerts = cli_view.SessionAlerts(ClaudeMonitor())
    for s in states:
        alerts.check({s.pid: s})
    return titles(toasts)

def test_task_done_and_waiting(toasts):
    got = run_sessions([session(status="busy"), session(status="waiting", waiting_for="input needed"),
                        session(status="waiting", waiting_for="input needed"), session(status="busy"),
                        session(status="shell")], toasts)
    assert got == ["Claude aguardando você: proj", "Tarefa Concluída: proj"]

def test_old_claude_code_waiting_is_not_task_done(toasts):
    # Versões antigas ficam "idle" com stop_reason tool_use enquanto esperam autorização
    got = run_sessions([session(status="busy"), session(status="idle", stop="tool_use")], toasts)
    assert got == ["Claude aguardando você: proj"]

def test_session_seen_first_time_does_not_alert(toasts):
    assert run_sessions([session(status="waiting")], toasts) == []

def test_large_context_alert_once_and_rearms_after_compact(toasts):
    states = [session(ctx=c, window=200_000) for c in (150_000, 170_000, 190_000, 40_000, 165_000)]
    got = run_sessions(states, toasts)
    assert got == ["Contexto grande: proj"] * 2
    assert "170.0k/200k" in toasts[0][1] and "/compact" in toasts[0][1]

def test_large_context_uses_absolute_limit_for_1m(toasts):
    assert run_sessions([session(ctx=590_000, window=1_000_000)], toasts) == []
    assert run_sessions([session(ctx=610_000, window=1_000_000)], toasts) == ["Contexto grande: proj"]

def incident(id, name="Erro", comps=("Claude API",)):
    return Incident(name, "identified", "major", "", id, list(comps))

def test_incidents_by_id_and_status_page_failure(toasts):
    alerts = cli_view.IncidentAlerts()
    a, b, c = incident("a", "Erros na API"), incident("b"), incident("c", "Cowork", ("Claude Cowork",))
    for status in [ServiceStatus(ok=True, incidents=[a]), ServiceStatus(ok=True, incidents=[a]),
                   ServiceStatus(),                                     # status page fora do ar
                   ServiceStatus(ok=True, incidents=[a, b]),
                   ServiceStatus(ok=True, incidents=[b, c])]:           # a resolvido + c novo
        alerts.check(status)
    assert [m for _, m in toasts] == ["Erros na API (Claude API)", "Erro (Claude API)", "Cowork (Claude Cowork)"]

def test_incident_toast_uses_translation(toasts):
    inc = incident("x", "Degraded performance")
    inc.name_pt = "Desempenho degradado"
    cli_view.IncidentAlerts().check(ServiceStatus(ok=True, incidents=[inc]))
    assert toasts == [("Incidente na Anthropic", "Desempenho degradado (Claude API)")]
