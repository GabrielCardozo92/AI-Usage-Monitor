"""
config.py - Gerenciamento de configurações e credenciais para o Claude Usage Monitor.
"""
import json
import os
from pathlib import Path
from typing import Optional, Tuple

APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "config.json"
CLAUDE_DIR = Path.home() / ".claude"
CREDENTIALS_FILE = CLAUDE_DIR / ".credentials.json"
PROJECTS_DIR = CLAUDE_DIR / "projects"

# Configurações padrão
DEFAULT_CONFIG = {
    "poll_interval_sec": 120,      # Intervalo padrão de atualização em segundos
    "manual_token": "",            # Token manual caso o usuário não use o login local
}

def load_config() -> dict:
    """Carrega as configurações salvas ou retorna as padrões."""
    cfg = DEFAULT_CONFIG.copy()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                cfg.update(saved)
        except Exception:
            pass
    return cfg

def get_claude_token(manual_token: Optional[str] = None) -> Tuple[Optional[str], str]:
    """
    Localiza o token de autenticação do Claude na seguinte ordem:
    1. Token manual configurado
    2. Variável de ambiente CLAUDE_CODE_OAUTH_TOKEN
    3. Arquivo de credenciais local do Claude Code (~/.claude/.credentials.json)
    
    Retorna (token, origem)
    """
    if manual_token and manual_token.strip():
        return manual_token.strip(), "Configuração Manual"

    env_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if env_token:
        return env_token, "Variável de Ambiente ($CLAUDE_CODE_OAUTH_TOKEN)"

    if CREDENTIALS_FILE.exists():
        try:
            with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                creds = json.load(f)
                access_token = creds.get("claudeAiOauth", {}).get("accessToken")
                if access_token:
                    return access_token.strip(), "Claude Code Local (~/.claude/.credentials.json)"
        except Exception:
            pass

    return None, "Nenhum token encontrado"
