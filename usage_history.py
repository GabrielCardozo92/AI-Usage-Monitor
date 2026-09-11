"""
usage_history.py - Histórico do uso semanal, para mostrar quanto foi usado em cada dia.

A status line (a cada resposta do Claude, mesmo com o monitor fechado) e o monitor
(quando consulta a API) anotam o uso atual em usage_history.jsonl sempre que ele muda.
Mantido leve (só biblioteca padrão): é importado pelo statusline.py.
"""
import json
import os
import time
from datetime import datetime
from typing import List, Optional, Tuple

from config import HISTORY_FILE

HISTORY_MAX_AGE_SEC = 8 * 86400  # Um pouco mais que uma janela semanal
WEEKDAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")

_cache = {"key": None, "samples": []}

def _last_line() -> Optional[dict]:
    try:
        with open(HISTORY_FILE, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 512))
            lines = f.read().splitlines()
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError, IndexError):
        return None

def append_sample(h5: Optional[float], d7: Optional[float], d7_reset: Optional[int]) -> None:
    """Anota o uso atual, se ele mudou desde a última anotação."""
    if d7 is None or not d7_reset:
        return
    # Arredonda: 0.28 * 100 vira 28.000000000000004, que pareceria uma mudança
    entry = {"h5": None if h5 is None else round(h5, 1), "d7": round(d7, 1), "d7_reset": int(d7_reset)}
    last = _last_line()
    if last and all(last.get(k) == v for k, v in entry.items()):
        return
    entry["t"] = round(time.time(), 1)
    try:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass

def load_samples() -> List[dict]:
    """Todas as anotações; relê o arquivo só quando ele muda."""
    try:
        st = os.stat(HISTORY_FILE)
    except OSError:
        return []
    key = (st.st_mtime, st.st_size)
    if _cache["key"] != key:
        samples = []
        with open(HISTORY_FILE, encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    samples.append(json.loads(line))
                except ValueError:
                    continue  # Linha corrompida (duas escritas ao mesmo tempo): ignora
        _cache.update(key=key, samples=samples)
    return _cache["samples"]

def prune() -> None:
    """Remove anotações mais velhas que uma semana (chamado pelo monitor ao iniciar)."""
    cutoff = time.time() - HISTORY_MAX_AGE_SEC
    samples = load_samples()
    kept = [s for s in samples if s.get("t", 0) >= cutoff]
    if len(kept) == len(samples):
        return
    tmp = HISTORY_FILE.with_suffix(".tmp")
    try:
        tmp.write_text("".join(json.dumps(s) + "\n" for s in kept), encoding="utf-8")
        os.replace(tmp, HISTORY_FILE)
    except OSError:
        pass

def daily_usage(samples: List[dict], d7_reset: int, now: float) -> List[Tuple[str, Optional[float], bool]]:
    """
    Para cada dia já iniciado da janela semanal: (dia da semana, % da cota semanal usado
    naquele dia, parcial?). Os "dias" são fatias de 24h a partir do início da janela.
    None = sem anotações suficientes; parcial = conta só a partir da 1ª anotação do dia
    (não havia registro anterior), então é um mínimo garantido.
    """
    start = d7_reset - 7 * 86400
    points = sorted((s["t"], s["d7"]) for s in samples
                    if abs(s.get("d7_reset", 0) - d7_reset) < 3600 and start <= s.get("t", 0) <= now
                    and s.get("d7") is not None)

    def usage_at(t: float) -> Optional[float]:
        # O maior valor anotado até t: dentro da janela o uso só cresce, então uma
        # leitura menor é de uma sessão parada, com dado atrasado
        values = [v for ts, v in points if ts <= t]
        return max(values) if values else None

    days = []
    for k in range(7):
        d0 = start + k * 86400
        if d0 >= now:
            break
        d1 = min(d0 + 86400, now)
        label = WEEKDAYS[datetime.fromtimestamp(d0).weekday()]
        begin = 0.0 if k == 0 else usage_at(d0)  # No início da janela o uso é zero
        end = usage_at(d1)
        if end is None:
            days.append((label, None, False))
        elif begin is None:
            in_day = [v for ts, v in points if d0 <= ts <= d1]
            growth = end - min(in_day) if in_day else 0.0
            # Sem registro anterior e sem crescimento no dia, "≥0%" não diria nada
            days.append((label, growth, True) if growth >= 0.5 else (label, None, False))
        else:
            days.append((label, end - begin, False))
    return days

def format_daily(days: List[Tuple[str, Optional[float], bool]]) -> str:
    """Ex.: 'ter -- · qua 8% · qui ≥3%'."""
    parts = []
    for label, value, partial in days:
        if value is None:
            parts.append(f"{label} --")
        else:
            parts.append(f"{label} {'≥' if partial else ''}{value:.0f}%")
    return " · ".join(parts)
