"""
Configuração comum dos testes. Todo teste roda com as pastas redirecionadas para um
diretório temporário: nada lê ou grava em ~/.claude nem nos arquivos reais do projeto.
"""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_view  # noqa: E402
import monitor_core  # noqa: E402
import statusline  # noqa: E402
import usage_history  # noqa: E402

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Redireciona todas as pastas e arquivos usados pelo monitor para tmp_path."""
    claude_dir = tmp_path / "claude"
    (claude_dir / "sessions").mkdir(parents=True)
    (claude_dir / "projects").mkdir()
    statusline_dir = tmp_path / "statusline_data"

    monkeypatch.setattr(monitor_core, "CLAUDE_DIR", claude_dir)
    monkeypatch.setattr(monitor_core, "PROJECTS_DIR", claude_dir / "projects")
    monkeypatch.setattr(monitor_core, "STATUSLINE_DIR", statusline_dir)
    monkeypatch.setattr(monitor_core, "TRANSLATIONS_FILE", tmp_path / "translations.json")
    monkeypatch.setattr(monitor_core, "APP_DIR", tmp_path)
    monkeypatch.setattr(statusline, "STATUSLINE_DIR", statusline_dir)
    monkeypatch.setattr(usage_history, "HISTORY_FILE", tmp_path / "usage_history.jsonl")
    monkeypatch.setitem(usage_history._cache, "key", None)
    monkeypatch.setattr(cli_view, "keep_running", True)
    return tmp_path

@pytest.fixture
def toasts(monkeypatch):
    """Captura as notificações (título, mensagem) em vez de mostrá-las."""
    sent = []
    monkeypatch.setattr(cli_view, "send_windows_toast", lambda title, msg: sent.append((title, msg)))
    monkeypatch.setattr(cli_view, "play_sound", lambda *a: None)
    return sent

@pytest.fixture
def freeze_time(monkeypatch):
    """Congela time.time() em um instante escolhido pelo teste."""
    def _freeze(ts: float):
        monkeypatch.setattr(time, "time", lambda: ts)
        return ts
    return _freeze

def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))

def usage_line(msg_id, ts, inp=0, out=0, cache_read=0, cache_write=0,
               cwd=None, model="claude-opus-5", stop="end_turn") -> str:
    """Uma linha de transcript do Claude Code com registro de uso."""
    record = {
        "timestamp": iso(ts),
        "message": {
            "id": msg_id, "model": model, "stop_reason": stop,
            "usage": {"input_tokens": inp, "output_tokens": out,
                      "cache_read_input_tokens": cache_read, "cache_creation_input_tokens": cache_write},
        },
    }
    if cwd:
        record["cwd"] = cwd
    return json.dumps(record) + "\n"

def session(pid=1, name="proj", status="idle", ctx=0, window=0, waiting_for="", stop="end_turn", model=""):
    return monitor_core.ClaudeSession(pid=pid, name=name, cwd="", status=status, context_tokens=ctx,
                                      last_stop_reason=stop, waiting_for=waiting_for, model=model,
                                      context_window=window)
