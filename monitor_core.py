"""
monitor_core.py - Núcleo de monitoramento de uso, limites e status do Claude.
Implementa a mesma lógica de consulta de headers unificados e contagem de tokens
de sessão do projeto Claude Usage Stick, além do estado das sessões do Claude Code
e da saúde dos serviços pelo status.claude.com.
"""
import glob
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from config import CLAUDE_DIR, PROJECTS_DIR

MESSAGES_ENDPOINT = "https://api.anthropic.com/v1/messages"
STATUS_ENDPOINT = "https://status.claude.com/api/v2/summary.json"
ANTHROPIC_VERSION = "2023-06-01"
CLAUDE_CODE_USER_AGENT = "claude-code/2.1.5"
PROBE_MODEL = "claude-haiku-4-5-20251001"

# Serviços do status.claude.com exibidos no painel: (nome exibido, prefixo do nome no status page)
WATCHED_COMPONENTS = [
    ("Claude API", "Claude API"),
    ("Claude Code", "Claude Code"),
    ("claude.ai", "claude.ai"),
]

# Status de componente do status page -> (texto, cor)
COMPONENT_STATUS = {
    "operational": ("Operacional", "#4ADE80"),
    "degraded_performance": ("Lento", "#FBBF24"),
    "partial_outage": ("Falha parcial", "#FB923C"),
    "major_outage": ("Fora do ar", "#F87171"),
    "under_maintenance": ("Manutenção", "#60A5FA"),
}

# Cabeçalhos unificados da Anthropic (iguais ao firmware C++)
H5U = "anthropic-ratelimit-unified-5h-utilization"
H5R = "anthropic-ratelimit-unified-5h-reset"
H5S = "anthropic-ratelimit-unified-5h-status"
D7U = "anthropic-ratelimit-unified-7d-utilization"
D7R = "anthropic-ratelimit-unified-7d-reset"
D7S = "anthropic-ratelimit-unified-7d-status"
UST = "anthropic-ratelimit-unified-status"
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
    latency_ms: int = 0                  # Tempo de resposta da própria consulta (Haiku)
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
    last_stop_reason: str = ""
    waiting_for: str = ""
    model: str = ""

@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0
    sessions_count: int = 0
    window_start_epoch: float = 0.0

@dataclass
class Incident:
    name: str
    status: str
    impact: str
    created_at: str

@dataclass
class ServiceStatus:
    """Resumo do status.claude.com. ok=False indica que a consulta falhou (dados desconhecidos)."""
    components: List[Tuple[str, str]] = field(default_factory=list)  # (nome exibido, status bruto)
    incidents: List[Incident] = field(default_factory=list)
    ok: bool = False

# (timestamp, id da mensagem, tokens de entrada, de saída, de leitura de cache)
UsageRecord = Tuple[float, Optional[str], int, int, int]

@dataclass
class _TranscriptCache:
    """O que já foi lido de um transcript .jsonl."""
    inode: int
    offset: int = 0                      # Bytes já processados (sempre no fim de uma linha)
    records: List[UsageRecord] = field(default_factory=list)

def _parse_ts(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0

def _parse_usage_line(line: str) -> Optional[UsageRecord]:
    """Extrai o registro de uso de uma linha do transcript, ou None se ela não tiver um."""
    try:
        record = json.loads(line)
        msg = record.get("message") or {}
        usage = msg.get("usage")
        if not usage:
            return None
        return (
            _parse_ts(record.get("timestamp", "")),
            msg.get("id") or record.get("uuid"),
            (usage.get("input_tokens", 0) or 0) + (usage.get("cache_creation_input_tokens", 0) or 0),
            usage.get("output_tokens", 0) or 0,
            usage.get("cache_read_input_tokens", 0) or 0,
        )
    except Exception:
        return None

class ClaudeMonitor:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": CLAUDE_CODE_USER_AGENT,
            "content-type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
            "anthropic-beta": "oauth-2025-04-20"
        })
        self.session_context_cache = {}
        self.session_stop_reason_cache = {}
        self.session_model_cache = {}
        self._session_log_paths: Dict[str, Path] = {}   # sessionId -> transcript
        self._session_log_sizes: Dict[str, int] = {}    # sessionId -> tamanho já analisado
        self._transcripts: Dict[str, _TranscriptCache] = {}

    def fetch_usage(self, token: str) -> UsageData:
        """
        Executa um POST mínimo com max_tokens=1 para /v1/messages e lê
        as cotas e prazos de reset diretamente dos headers da resposta.
        """
        headers = {"Authorization": f"Bearer {token}"}
        body = {
            "model": PROBE_MODEL,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "."}]
        }

        t0 = time.time()
        try:
            resp = self.session.post(
                MESSAGES_ENDPOINT,
                headers=headers,
                json=body,
                timeout=12
            )
        except Exception as e:
            return UsageData(ok=False, error_msg=f"Falha de conexão: {e}")
        latency_ms = int((time.time() - t0) * 1000)

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
            latency_ms=latency_ms,
            ok=True
        )

    def fetch_service_status(self) -> ServiceStatus:
        """
        Busca no status.claude.com (público, sem token e sem gastar cota) o status
        de cada serviço monitorado e os incidentes em aberto.
        """
        try:
            r = requests.get(STATUS_ENDPOINT, timeout=10)
            if r.status_code != 200:
                return ServiceStatus()
            data = r.json()
        except Exception:
            return ServiceStatus()

        components = []
        for display_name, prefix in WATCHED_COMPONENTS:
            status = next(
                (c.get("status", "unknown") for c in data.get("components", [])
                 if c.get("name", "").startswith(prefix)),
                "unknown"
            )
            components.append((display_name, status))

        incidents = [
            Incident(
                name=inc.get("name", "Incidente Desconhecido"),
                status=inc.get("status", "unknown"),
                impact=inc.get("impact", "none"),
                created_at=inc.get("created_at", "")
            )
            for inc in data.get("incidents", [])
        ]
        return ServiceStatus(components=components, incidents=incidents, ok=True)

    @staticmethod
    def component_status_display(status: str) -> Tuple[str, str]:
        """Converte o status bruto de um componente em (texto, cor)."""
        return COMPONENT_STATUS.get(status, ("Desconhecido", "#8E8E9B"))

    def get_claude_sessions(self) -> Dict[int, ClaudeSession]:
        """
        Lê os arquivos ~/.claude/sessions/*.json para rastrear instâncias e calcula o tamanho do contexto.
        """
        sessions_dir = CLAUDE_DIR / "sessions"
        sessions = {}

        if not sessions_dir.exists():
            return sessions

        for session_file in sessions_dir.glob("*.json"):
            try:
                with open(session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            pid = data.get("pid")
            if not pid:
                continue

            cwd = data.get("cwd", "")
            clean_name = Path(cwd).name if cwd else data.get("name", f"Terminal-{pid}")
            session_id = data.get("sessionId")
            if session_id:
                self._refresh_session_log(session_id)

            sessions[pid] = ClaudeSession(
                pid=pid,
                name=clean_name,
                cwd=cwd,
                status=data.get("status", "unknown"),
                context_tokens=self.session_context_cache.get(session_id, 0),
                status_updated_at=data.get("statusUpdatedAt", 0) / 1000.0,
                last_stop_reason=self.session_stop_reason_cache.get(session_id, ""),
                waiting_for=data.get("waitingFor", ""),
                model=self.session_model_cache.get(session_id, "")
            )

        return sessions

    def _refresh_session_log(self, session_id: str) -> None:
        """
        Atualiza os caches de contexto, stop_reason e modelo da sessão a partir do fim
        do seu transcript. Só lê o arquivo de novo quando o tamanho dele muda.
        """
        path = self._session_log_paths.get(session_id)
        if path is None or not path.exists():
            # Localizar o transcript varre todas as pastas de projeto: faz isso uma vez só
            found = list(PROJECTS_DIR.glob(f"*/{session_id}.jsonl"))
            if not found:
                return
            path = self._session_log_paths[session_id] = found[0]

        try:
            size = path.stat().st_size
            if self._session_log_sizes.get(session_id) == size:
                return
            self._session_log_sizes[session_id] = size

            with open(path, "rb") as lf:
                # Ler até 1 MB do final para garantir que pega outputs gigantes
                lf.seek(max(0, size - 1024 * 1024), 0)
                lines = lf.read(size).decode("utf-8", errors="ignore").splitlines()
        except OSError:
            return

        for line in reversed(lines):
            if '"usage":' not in line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue  # A primeira linha do trecho lido pode estar cortada

            # Tenta extrair dados da mensagem do assistant
            msg_data = msg.get("message", {})
            if msg_data:
                self.session_stop_reason_cache[session_id] = msg_data.get("stop_reason", "")
                m_name = msg_data.get("model", "")
                if m_name:
                    self.session_model_cache[session_id] = m_name

            usage = msg_data.get("usage") or msg.get("usage")
            if usage:
                inp = usage.get("input_tokens", 0)
                cc = usage.get("cache_creation_input_tokens", 0)
                cr = usage.get("cache_read_input_tokens", 0)
                self.session_context_cache[session_id] = inp + cc + cr
                break

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

        # Recursivo para incluir os transcripts de subagentes,
        # que ficam em projects/<projeto>/<sessão>/subagents/*.jsonl
        pattern = os.path.join(str(PROJECTS_DIR), "**", "*.jsonl")

        for file_path in glob.glob(pattern, recursive=True):
            try:
                if os.path.getmtime(file_path) < window_start - 60:
                    self._transcripts.pop(file_path, None)  # Saiu da janela: libera a memória
                    continue
                records = self._read_transcript_usage(file_path)
            except OSError:
                continue

            # Um subagente conta como parte da sessão que o criou
            p = Path(file_path)
            session_key = p.parent.parent.name if p.parent.name == "subagents" else p.stem

            for ts, msg_id, r_in, r_out, r_cache in records:
                if ts < window_start:
                    continue
                if msg_id:
                    if msg_id in seen_ids:
                        continue
                    seen_ids.add(msg_id)
                tin += r_in
                tout += r_out
                tcache += r_cache
                active_sessions.add(session_key)

        return TokenUsage(
            input_tokens=tin,
            output_tokens=tout,
            cache_tokens=tcache,
            sessions_count=len(active_sessions),
            window_start_epoch=window_start
        )

    def _read_transcript_usage(self, file_path: str) -> List[UsageRecord]:
        """
        Devolve os registros de uso de um transcript, lendo do disco só o que foi
        acrescentado desde a última chamada (os transcripts só crescem).
        """
        st = os.stat(file_path)
        cache = self._transcripts.get(file_path)
        if cache is None or st.st_size < cache.offset or st.st_ino != cache.inode:
            # Arquivo novo, ou foi reescrito: começa do zero
            cache = self._transcripts[file_path] = _TranscriptCache(inode=st.st_ino)

        if st.st_size > cache.offset:
            with open(file_path, "rb") as f:
                f.seek(cache.offset)
                chunk = f.read(st.st_size - cache.offset)
            # Só processa até a última linha completa: uma linha ainda sendo
            # escrita pelo Claude Code fica para a próxima leitura
            end = chunk.rfind(b"\n") + 1
            cache.offset += end
            for raw in chunk[:end].splitlines():
                if b'"usage"' in raw:
                    record = _parse_usage_line(raw.decode("utf-8", errors="ignore"))
                    if record:
                        cache.records.append(record)
        return cache.records

    @staticmethod
    def format_countdown(seconds: float) -> str:
        """Formata segundos em "HHh MMm SSs" ou, acima de um dia, "Nd HHh MMm"."""
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
