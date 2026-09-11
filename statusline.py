"""
statusline.py - Status line do Claude Code (fonte oficial de uso para o monitor).

O Claude Code executa este script a cada nova resposta e passa, pelo stdin, um JSON com
o uso de 5h/7d do plano (rate_limits) e a janela de contexto da sessão (context_window).
O script grava esses dados em statusline_data/<session_id>.json, que o monitor lê, anota
o uso em usage_history.jsonl (histórico por dia) e imprime uma linha compacta no rodapé.

Configuração em ~/.claude/settings.json:
    "statusLine": {"type": "command", "command": "python C:/caminho/para/statusline.py"}

Mantido leve de propósito (só biblioteca padrão): roda a cada resposta do Claude.
"""
import json
import os
import sys
import time

from config import STATUSLINE_DIR
from usage_history import append_sample

# Para janelas grandes, o alerta de contexto usa limites absolutos (contexto grande
# consome cota rápido); para janelas pequenas, uma fração da janela (perto do auto-compact).
CTX_WARN_TOKENS, CTX_WARN_FRACTION = 300_000, 0.5
CTX_CRIT_TOKENS, CTX_CRIT_FRACTION = 600_000, 0.8

def context_thresholds(window: int):
    """Tokens de contexto a partir dos quais avisar (amarelo) e alertar (vermelho)."""
    if not window:
        return CTX_WARN_TOKENS, CTX_CRIT_TOKENS  # Janela desconhecida: limites absolutos
    return (min(CTX_WARN_TOKENS, int(window * CTX_WARN_FRACTION)),
            min(CTX_CRIT_TOKENS, int(window * CTX_CRIT_FRACTION)))

def format_window(window: int) -> str:
    """1000000 -> '1M', 200000 -> '200k'."""
    if window >= 1_000_000 and window % 1_000_000 == 0:
        return f"{window // 1_000_000}M"
    return f"{window // 1000}k"

def _short_tokens(count: int) -> str:
    return f"{count / 1000:.0f}k" if count >= 1000 else str(count)

def _color(text: str, level: int) -> str:
    """level 0 = verde, 1 = amarelo, 2 = vermelho (códigos ANSI)."""
    return f"\033[{(32, 33, 31)[level]}m{text}\033[0m"

def _pct_level(pct: float) -> int:
    return 0 if pct < 50 else 1 if pct < 80 else 2

def write_snapshot(data: dict) -> None:
    """Grava o que o monitor precisa desta sessão (escrita atômica)."""
    session_id = data.get("session_id")
    if not session_id:
        return
    ctx = data.get("context_window") or {}
    snapshot = {
        "session_id": session_id,
        "captured_at": time.time(),
        "model_id": (data.get("model") or {}).get("id", ""),
        "context_window_size": ctx.get("context_window_size") or 0,
        "context_used_percentage": ctx.get("used_percentage"),
        "rate_limits": data.get("rate_limits") or {},
    }
    STATUSLINE_DIR.mkdir(exist_ok=True)
    target = STATUSLINE_DIR / f"{session_id}.json"
    tmp = target.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(snapshot), encoding="utf-8")
    try:
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)  # Monitor lendo o arquivo agora: a próxima atualização grava

def render_line(data: dict) -> str:
    """Ex.: '5h 13% · 7d 15% · ctx 254k/1M (25%)'."""
    parts = []
    rate = data.get("rate_limits") or {}
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        pct = (rate.get(key) or {}).get("used_percentage")
        if pct is not None:
            parts.append(f"{label} {_color(f'{pct:.0f}%', _pct_level(pct))}")

    ctx = data.get("context_window") or {}
    used = ctx.get("total_input_tokens") or 0
    window = ctx.get("context_window_size") or 0
    if used and window:
        warn, crit = context_thresholds(window)
        level = 2 if used >= crit else 1 if used >= warn else 0
        text = f"{_short_tokens(used)}/{format_window(window)}"
        if ctx.get("used_percentage") is not None:
            text += f" ({ctx['used_percentage']:.0f}%)"
        parts.append(f"ctx {_color(text, level)}")
    return " · ".join(parts)

def main() -> None:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception:
        return
    try:
        write_snapshot(data)
        rate = data.get("rate_limits") or {}
        d7 = rate.get("seven_day") or {}
        append_sample((rate.get("five_hour") or {}).get("used_percentage"),
                      d7.get("used_percentage"), d7.get("resets_at"))
    except Exception:
        pass  # Falha ao gravar não pode apagar a linha do rodapé
    try:
        line = render_line(data)
        if line:
            # newline="\n": no Windows o print escreveria "\r\n", e o "\r" sobra no rodapé
            sys.stdout.reconfigure(encoding="utf-8", newline="\n")
            print(line)
    except Exception:
        pass

if __name__ == "__main__":
    main()
