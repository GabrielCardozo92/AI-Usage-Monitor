"""Ordem de busca do token e tradução de incidentes via claude -p."""
import json
import subprocess

import config
import monitor_core

def test_token_order_manual_then_env_then_credentials(tmp_path, monkeypatch):
    creds = tmp_path / ".credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": " arquivo "}}), encoding="utf-8")
    monkeypatch.setattr(config, "CREDENTIALS_FILE", creds)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "env")

    assert config.get_claude_token(" manual ")[0] == "manual"
    assert config.get_claude_token("")[0] == "env"
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    assert config.get_claude_token(None)[0] == "arquivo"
    creds.unlink()
    assert config.get_claude_token(None) == (None, "Nenhum token encontrado")

class FakeClaude:
    """Substitui subprocess.run e registra como o claude -p foi chamado."""
    def __init__(self, stdout="Desempenho degradado", returncode=0):
        self.calls = []
        self.stdout, self.returncode = stdout, returncode

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, self.returncode, stdout=self.stdout, stderr="")

def make_translator(monkeypatch, fake):
    monkeypatch.setattr(monitor_core.shutil, "which", lambda name: "claude.CMD")
    monkeypatch.setattr(monitor_core.subprocess, "run", fake)
    return monitor_core.IncidentTranslator()

def test_translation_passes_title_on_stdin_and_caches(monkeypatch):
    fake = FakeClaude()
    title = "Degraded performance & errors | $(calc)"
    t = make_translator(monkeypatch, fake)
    assert t.translate(title) == "Desempenho degradado"
    args, kwargs = fake.calls[0]
    assert kwargs["input"] == title and not any(title in a for a in args)  # nunca na linha de comando
    assert "--no-session-persistence" in args and "haiku" in args

    assert t.translate(title) == "Desempenho degradado" and len(fake.calls) == 1   # cache em memória
    fresh = make_translator(monkeypatch, fake)                                     # cache em disco
    assert fresh.translate(title) == "Desempenho degradado" and len(fake.calls) == 1

def test_translation_failure_backs_off(monkeypatch):
    fake = FakeClaude(returncode=1, stdout="")
    t = make_translator(monkeypatch, fake)
    assert t.translate("Some incident") is None
    assert t.translate("Some incident") is None and len(fake.calls) == 1   # espera antes de tentar de novo

def test_translation_rejects_rambling_answer(monkeypatch):
    t = make_translator(monkeypatch, FakeClaude(stdout="Claro! " + "blá " * 100))
    assert t.translate("Short title") is None

def test_without_claude_code_installed(monkeypatch):
    monkeypatch.setattr(monitor_core.shutil, "which", lambda name: None)
    assert monitor_core.IncidentTranslator().translate("Some incident") is None
