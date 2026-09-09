"""
monitor_core.py - Núcleo de monitoramento de uso, limites e status do Claude.
Implementa a mesma lógica de consulta de headers unificados, sondagem de modelos
e contagem de tokens de sessão do projeto Claude Usage Stick.
"""
import glob
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from config import PROJECTS_DIR

MESSAGES_ENDPOINT = "https://api.anthropic.com/v1/messages"
STATUS_ENDPOINT = "https://status.claude.com/api/v2/incidents/unresolved.json"
ANTHROPIC_VERSION = "2023-06-01"
PROBE_MODEL = "claude-haiku-4-5-20251001"

# Cabeçalhos unificados da Anthropic (iguais ao firmware C++)
H5U = "anthropic-ratelimit-unified-5h-utilization"
H5R = "anthropic-ratelimit-unified-5h-reset"
H5S = "anthropic-ratelimit-unified-5h-status"
D7U = "anthropic-ratelimit-unified-7d-utilization"
D7R = "anthropic-ratelimit-unified-7d-reset"
D7S = "anthropic-ratelimit-unified-7d-status"
UST = "anthropic-ratelimit-unified-status"
URS = "anthropic-ratelimit-unified-reset"
URC = "anthropic-ratelimit-unified-representative-claim"
UFB = "anthropic-ratelimit-unified-fallback-percentage"
UOS = "anthropic-ratelimit-unified-overage-status"
UOR = "anthropic-ratelimit-unified-overage-disabled-reason"

@dataclass
class UsageData:
    h5_utilization: float = 0.0          # 0 a 100%
    h5_reset_epoch: int = 0              # Epoch timestamp
    h5_status: str = "unknown"           # allowed | allowed_warning | rejected
    d7_utilization: float = 0.0          # 0 a 100%
    d7_reset_epoch: int = 0
    d7_status: str = "unknown"
    unified_status: str = "unknown"
    representative_claim: str = ""       # five_hour | seven_day
    fallback_pct: float = 0.0
    overage_status: str = ""
    overage_reason: str = ""
    timestamp: float = field(default_factory=time.time)
    ok: bool = False
    error_msg: str = ""

@dataclass
class ClaudeSession:
    pid: int
    name: str
    cwd: str
    status: str
    context_tokens: int = 0
    status_updated_at: float = 0.0

@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0
    sessions_count: int = 0
    window_start_epoch: float = 0.0

@dataclass
class ModelProbe:
    model_id: str
    display_name: str
    latency_ms: int = 0
    status_code: int = 0
    ok: bool = False

@dataclass
class Incident:
    name: str
    status: str
    impact: str
    created_at: str

class ClaudeMonitor:
    def __init__(self):
        self.session = requests.Session()
        self.session_context_cache = {}

    def fetch_usage(self, token: str) -> UsageData:
        """
        Executa um POST mínimo com max_tokens=1 para /v1/messages e lê
        as cotas e prazos de reset diretamente dos headers da resposta.
        """
        headers = {
            "Authorization": f"Bearer {token}",
            "anthropic-version": ANTHROPIC_VERSION,
            "anthropic-beta": "oauth-2025-04-20",
            "content-type": "application/json",
            "User-Agent": "claude-code/2.1.5"
        }
        body = {
            "model": PROBE_MODEL,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "."}]
        }

        try:
            resp = self.session.post(
                MESSAGES_ENDPOINT,
                headers=headers,
                json=body,
                timeout=12
            )
        except Exception as e:
            return UsageData(ok=False, error_msg=f"Falha de conexão: {e}")

        if resp.status_code == 401:
            return UsageData(ok=False, error_msg="Token inválido ou expirado (HTTP 401)")

        h = resp.headers
        h5u = h.get(H5U)
        d7u = h.get(D7U)

        if h5u is None and d7u is None:
            return UsageData(
                ok=False,
                error_msg=f"Resposta sem headers de rate limit (HTTP {resp.status_code})"
            )

        def to_pct(val) -> float:
            try:
                return float(val) * 100.0
            except (TypeError, ValueError):
                return 0.0

        def to_int(val) -> int:
            try:
                return int(val)
            except (TypeError, ValueError):
                return 0

        return UsageData(
            h5_utilization=to_pct(h5u),
            h5_reset_epoch=to_int(h.get(H5R)),
            h5_status=h.get(H5S, "unknown"),
            d7_utilization=to_pct(d7u),
            d7_reset_epoch=to_int(h.get(D7R)),
            d7_status=h.get(D7S, "unknown"),
            unified_status=h.get(UST, "allowed"),
            representative_claim=h.get(URC, ""),
            fallback_pct=to_pct(h.get(UFB)),
            overage_status=h.get(UOS, ""),
            overage_reason=h.get(UOR, ""),
            ok=True
        )

    def probe_models(self, token: str) -> List[ModelProbe]:
        """
        Testa a latência e disponibilidade dos principais modelos da Anthropic.
        """
        models_to_test = [
            ("claude-haiku-4-5-20251001", "Haiku"),
            ("claude-sonnet-5", "Sonnet"),
            ("claude-opus-4-8", "Opus"),
            ("claude-fable-5", "Fable")
        ]
        headers = {
            "Authorization": f"Bearer {token}",
            "anthropic-version": ANTHROPIC_VERSION,
            "anthropic-beta": "oauth-2025-04-20",
            "content-type": "application/json",
            "User-Agent": "claude-code/2.1.5"
        }

        results = []
        for model_id, name in models_to_test:
            body = {
                "model": model_id,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "."}]
            }
            t0 = time.time()
            try:
                r = self.session.post(MESSAGES_ENDPOINT, headers=headers, json=body, timeout=10)
                latency = int((time.time() - t0) * 1000)
                results.append(ModelProbe(
                    model_id=model_id,
                    display_name=name,
                    latency_ms=latency,
                    status_code=r.status_code,
                    ok=(r.status_code == 200)
                ))
            except Exception:
                latency = int((time.time() - t0) * 1000)
                results.append(ModelProbe(
                    model_id=model_id,
                    display_name=name,
                    latency_ms=latency,
                    status_code=-1,
                    ok=False
                ))
        return results

    def fetch_incidents(self) -> List[Incident]:
        """Busca incidentes ativos na página de status da Anthropic."""
        try:
            r = requests.get("https://status.anthropic.com/api/v2/incidents/unresolved.json", timeout=10)
            if r.status_code == 200:
                data = r.json()
                incidents = []
                for inc in data.get("incidents", []):
                    incidents.append(Incident(
                        name=inc.get("name", "Incidente Desconhecido"),
                        status=inc.get("status", "unknown"),
                        impact=inc.get("impact", "none"),
                        created_at=inc.get("created_at", "")
                    ))
                return incidents
            return []
        except Exception:
            return []

    def get_claude_sessions(self) -> dict[int, ClaudeSession]:
        """
        Lê os arquivos ~/.claude/sessions/*.json para rastrear instâncias e calcula o tamanho do contexto.
        """
        claude_dir = Path.home() / ".claude"
        sessions_dir = claude_dir / "sessions"
        projects_dir = claude_dir / "projects"
        sessions = {}
        
        if not sessions_dir.exists():
            return sessions
            
        try:
            for session_file in sessions_dir.glob("*.json"):
                try:
                    with open(session_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        pid = data.get("pid")
                        if pid:
                            cwd = data.get("cwd", "")
                            if cwd:
                                clean_name = Path(cwd).name
                            else:
                                clean_name = data.get("name", f"Terminal-{pid}")
                                
                            session_id = data.get("sessionId")
                            context_size = self.session_context_cache.get(session_id, 0)
                            status_updated_at = data.get("statusUpdatedAt", 0) / 1000.0
                            
                            # Buscar o arquivo .jsonl para ver o tamanho do contexto
                            if session_id and projects_dir.exists():
                                log_files = list(projects_dir.glob(f"*/{session_id}.jsonl"))
                                if log_files:
                                    try:
                                        with open(log_files[0], "rb") as lf:
                                            lf.seek(0, 2)
                                            size = lf.tell()
                                            # Ler até 1 MB do final para garantir que pega outputs gigantes
                                            lf.seek(max(0, size - 1024 * 1024), 0)
                                            lines = lf.read().decode("utf-8", errors="ignore").splitlines()
                                            for line in reversed(lines):
                                                if '"usage":' in line:
                                                    msg = json.loads(line)
                                                    usage = msg.get("message", {}).get("usage")
                                                    if not usage:
                                                        usage = msg.get("usage")
                                                    if usage:
                                                        inp = usage.get("input_tokens", 0)
                                                        cc = usage.get("cache_creation_input_tokens", 0)
                                                        cr = usage.get("cache_read_input_tokens", 0)
                                                        context_size = inp + cc + cr
                                                        self.session_context_cache[session_id] = context_size
                                                        break
                                    except Exception:
                                        pass
                                
                            sessions[pid] = ClaudeSession(
                                pid=pid,
                                name=clean_name,
                                cwd=cwd,
                                status=data.get("status", "unknown"),
                                context_tokens=context_size,
                                status_updated_at=status_updated_at
                            )
                except Exception:
                    continue
        except Exception:
            pass
            
        return sessions

    def collect_local_tokens(self, h5_reset_epoch: Optional[int] = None) -> TokenUsage:
        """
        Lê as sessões locais do Claude Code (~/.claude/projects/**/*.jsonl)
        e calcula os tokens consumidos na janela de 5h atual.
        """
        now = time.time()
        if h5_reset_epoch and h5_reset_epoch > now - 86400:
            window_start = h5_reset_epoch - 5 * 3600
        else:
            window_start = now - 5 * 3600

        tin = tout = tcache = 0
        seen_ids = set()
        active_sessions = set()

        projects_path = str(PROJECTS_DIR)
        pattern = os.path.join(projects_path, "*", "*.jsonl")

        def parse_ts(ts: str) -> float:
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except Exception:
                return 0.0

        for file_path in glob.glob(pattern):
            try:
                if os.path.getmtime(file_path) < window_start - 60:
                    continue

                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if '"usage"' not in line:
                            continue
                        try:
                            record = json.loads(line)
                        except Exception:
                            continue

                        msg = record.get("message") or {}
                        usage = msg.get("usage")
                        if not usage:
                            continue

                        ts = parse_ts(record.get("timestamp", ""))
                        if ts < window_start:
                            continue

                        msg_id = msg.get("id") or record.get("uuid")
                        if msg_id and msg_id in seen_ids:
                            continue
                        if msg_id:
                            seen_ids.add(msg_id)

                        tin += (usage.get("input_tokens", 0) or 0) + (usage.get("cache_creation_input_tokens", 0) or 0)
                        tout += (usage.get("output_tokens", 0) or 0)
                        tcache += (usage.get("cache_read_input_tokens", 0) or 0)
                        active_sessions.add(file_path)
            except OSError:
                continue

        return TokenUsage(
            input_tokens=tin,
            output_tokens=tout,
            cache_tokens=tcache,
            sessions_count=len(active_sessions),
            window_start_epoch=window_start
        )

    @staticmethod
    def format_countdown(seconds: float) -> str:
        """Formata segundos em HH:MM:SS ou Nd HHh."""
        if seconds <= 0:
            return "00m 00s"
        total_sec = int(seconds)
        days = total_sec // 86400
        rem = total_sec % 86400
        hours = rem // 3600
        minutes = (rem % 3600) // 60
        secs = rem % 60

        if days > 0:
            return f"{days}d {hours:02d}h {minutes:02d}m"
        return f"{hours:02d}h {minutes:02d}m {secs:02d}s"

    @staticmethod
    def format_tokens(count: int) -> str:
        """Formata contagem de tokens de forma legível (ex: 1.2M, 850k)."""
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        if count >= 1_000:
            return f"{count / 1_000:.1f}k"
        return str(count)

    @staticmethod
    def get_projection_text(h5_utilization: float, h5_reset_epoch: int) -> Tuple[str, str]:
        """
        Calcula a projeção de consumo dentro da janela de 5 horas.
        Retorna (texto, status_color).
        """
        now = time.time()
        window_start = h5_reset_epoch - 5 * 3600
        elapsed = now - window_start

        if elapsed <= 60 or h5_utilization <= 0:
            return "Início da janela de 5h", "#4ADE80"

        rate_per_sec = h5_utilization / elapsed
        remaining_sec = h5_reset_epoch - now

        if remaining_sec <= 0:
            return "Janela resetando agora", "#4ADE80"

        projected_total = h5_utilization + (rate_per_sec * remaining_sec)

        if projected_total <= 100:
            return f"NÃO acaba antes do reset (~{projected_total:.0f}%)", "#4ADE80"
        else:
            # Vai estourar a cota antes do reset
            sec_to_exhaust = (100 - h5_utilization) / rate_per_sec
            exhaust_time = datetime.fromtimestamp(now + sec_to_exhaust).strftime("%H:%M")
            h = int(sec_to_exhaust // 3600)
            m = int((sec_to_exhaust % 3600) // 60)
            tempo_str = f"{h}h{m:02d}m" if h > 0 else f"{m}m"
            color = "#F87171" if sec_to_exhaust < 1800 else "#FBBF24"
            return f"No ritmo atual, esgota às {exhaust_time} (em {tempo_str})", color
