"""Contagem de tokens dos transcripts (janela de 5h) e consumo por projeto (semana)."""
import os
import time

import pytest

import monitor_core
from conftest import usage_line

def totals(t):
    return t.input_tokens, t.output_tokens, t.cache_tokens, t.sessions_count

@pytest.fixture
def project():
    d = monitor_core.PROJECTS_DIR / "C--proj"
    (d / "s1" / "subagents").mkdir(parents=True)
    return d

def test_counts_window_dedups_and_includes_subagents(project):
    now = time.time()
    (project / "s1.jsonl").write_text(
        usage_line("a", now - 100, inp=10, out=1, cache_read=5, cache_write=2)
        + usage_line("a", now - 100, inp=10, out=1, cache_read=5, cache_write=2)  # mesma mensagem (streaming)
        + usage_line("old", now - 9 * 3600, inp=999, out=999),                    # fora da janela de 5h
        encoding="utf-8")
    (project / "s1" / "subagents" / "agent-x.jsonl").write_text(usage_line("b", now - 50, inp=20, out=2), encoding="utf-8")

    # Entrada inclui a escrita de cache; o subagente conta como a mesma sessão (s1)
    assert totals(monitor_core.ClaudeMonitor().collect_local_tokens()) == (32, 3, 5, 1)

def test_incremental_read_waits_for_complete_line_and_handles_rewrite(project):
    now = time.time()
    f = project / "s1.jsonl"
    f.write_text(usage_line("a", now - 10, inp=10, out=1), encoding="utf-8")
    m = monitor_core.ClaudeMonitor()
    assert totals(m.collect_local_tokens())[:2] == (10, 1)

    line = usage_line("b", now - 5, inp=40, out=4)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(line[:25])                    # linha ainda sendo escrita
    assert totals(m.collect_local_tokens())[:2] == (10, 1)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(line[25:])
    assert totals(m.collect_local_tokens())[:2] == (50, 5)

    f.write_text(usage_line("c", now - 1, inp=1, out=1), encoding="utf-8")  # reescrito (menor)
    assert totals(m.collect_local_tokens())[:2] == (1, 1)

def test_file_outside_every_window_is_evicted(project):
    now = time.time()
    f = project / "s1.jsonl"
    f.write_text(usage_line("a", now - 10, inp=10), encoding="utf-8")
    m = monitor_core.ClaudeMonitor()
    m.collect_local_tokens()
    assert str(f) in m._transcripts
    os.utime(f, (now - 9 * 86400, now - 9 * 86400))
    m.collect_local_tokens()
    assert str(f) not in m._transcripts

def test_ignores_malformed_lines(project):
    now = time.time()
    (project / "s1.jsonl").write_text(
        '{"usage": quebrado\n["uma lista", "usage"]\n' + usage_line("a", now - 10, inp=7), encoding="utf-8")
    assert totals(monitor_core.ClaudeMonitor().collect_local_tokens())[0] == 7

def test_project_breakdown_weights_and_names():
    now = time.time()
    a = monitor_core.PROJECTS_DIR / "C--Users-x-alpha"
    b = monitor_core.PROJECTS_DIR / "C--Users-x-beta"
    a.mkdir()
    b.mkdir()
    # alpha: 100 de entrada + 20 de saída (x5) = 200 ponderados
    (a / "s.jsonl").write_text(usage_line("a1", now - 3600, inp=100, out=20, cwd="C:/x/alpha"), encoding="utf-8")
    # beta: 1000 de leitura de cache (x0.1) = 100 ponderados; sem cwd -> usa o nome da pasta
    (b / "s.jsonl").write_text(usage_line("b1", now - 3600, cache_read=1000), encoding="utf-8")
    # Fora da semana: não conta
    (a / "velho.jsonl").write_text(usage_line("z", now - 7.5 * 86400, inp=10_000, cwd="C:/x/alpha"), encoding="utf-8")

    shares = dict(monitor_core.ClaudeMonitor().project_breakdown(None))
    assert shares["alpha"] == pytest.approx(2 / 3)
    assert shares["C--Users-x-beta"] == pytest.approx(1 / 3)

def test_project_breakdown_empty():
    assert monitor_core.ClaudeMonitor().project_breakdown(None) == []
