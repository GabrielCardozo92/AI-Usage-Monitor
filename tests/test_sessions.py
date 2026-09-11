"""Leitura das sessões do Claude Code (~/.claude/sessions + transcripts + status line)."""
import json
import time

import monitor_core
import statusline
from conftest import usage_line

def write_session(pid, cwd, status="busy", entrypoint="cli", session_id=None, **extra):
    data = {"pid": pid, "cwd": cwd, "status": status, "entrypoint": entrypoint,
            "sessionId": session_id or f"sid-{pid}", "statusUpdatedAt": 1_000_000, **extra}
    (monitor_core.CLAUDE_DIR / "sessions" / f"{pid}.json").write_text(json.dumps(data), encoding="utf-8")

def test_lists_interactive_sessions_and_skips_headless():
    write_session(1, "C:/x/meu-projeto")
    write_session(2, "C:/x/claude-usage-monitor", status=None, entrypoint="sdk-cli")  # claude -p
    sessions = monitor_core.ClaudeMonitor().get_claude_sessions()
    assert [s.name for s in sessions.values()] == ["meu-projeto"]

def test_context_model_and_stop_reason_from_transcript_tail():
    write_session(1, "C:/x/proj", session_id="abc")
    project = monitor_core.PROJECTS_DIR / "C--x-proj"
    project.mkdir()
    now = time.time()
    (project / "abc.jsonl").write_text(
        usage_line("m1", now - 60, inp=100, cache_read=900, model="claude-sonnet-5")
        + usage_line("m2", now - 10, inp=5, cache_read=2000, cache_write=45, model="claude-opus-5", stop="tool_use")
        + '{"type": "user", "message": {"role": "user", "content": "oi"}}\n',
        encoding="utf-8")
    s = monitor_core.ClaudeMonitor().get_claude_sessions()[1]
    assert (s.context_tokens, s.model, s.last_stop_reason) == (2050, "claude-opus-5", "tool_use")

def test_transcript_reread_only_when_it_grows():
    write_session(1, "C:/x/proj", session_id="abc")
    project = monitor_core.PROJECTS_DIR / "C--x-proj"
    project.mkdir()
    f = project / "abc.jsonl"
    f.write_text(usage_line("m1", time.time(), inp=100), encoding="utf-8")
    m = monitor_core.ClaudeMonitor()
    assert m.get_claude_sessions()[1].context_tokens == 100
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(usage_line("m2", time.time(), inp=300))
    assert m.get_claude_sessions()[1].context_tokens == 300

def test_context_window_comes_from_status_line():
    write_session(1, "C:/x/proj", session_id="abc")
    statusline.STATUSLINE_DIR.mkdir()
    (statusline.STATUSLINE_DIR / "abc.json").write_text(json.dumps({"context_window_size": 200_000}), encoding="utf-8")
    assert monitor_core.ClaudeMonitor().get_claude_sessions()[1].context_window == 200_000

def test_ignores_broken_session_files():
    (monitor_core.CLAUDE_DIR / "sessions" / "9.json").write_text("{quebrado", encoding="utf-8")
    (monitor_core.CLAUDE_DIR / "sessions" / "8.json").write_text(json.dumps({"cwd": "sem pid"}), encoding="utf-8")
    assert monitor_core.ClaudeMonitor().get_claude_sessions() == {}
