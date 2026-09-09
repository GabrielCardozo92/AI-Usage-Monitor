"""
gui.py - Interface gráfica desktop moderna para o Claude Usage Monitor.
Inspirada no Claude Usage Stick (ESP32), com paleta dark, coral (#D97757),
medidores segmentados de 18 blocos, contagem regressiva ao vivo e modo mini-widget.
"""
import ctypes
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from typing import List, Optional

from config import get_claude_token, load_config, save_config
from monitor_core import ClaudeMonitor, Incident, ModelProbe, TokenUsage, UsageData

# ── Paleta de Cores do Claude ────────────────────────────────
BG_DARK = "#131316"          # Fundo da janela principal
BG_CARD = "#1C1C22"          # Fundo dos cartões
BG_CARD_HOVER = "#24242C"    # Hover nos cartões/botões
BORDER_COLOR = "#2A2A36"     # Bordas sutis
TEXT_PRIMARY = "#F4F4F6"     # Texto principal
TEXT_MUTED = "#8E8E9B"       # Texto secundário/cinza
CORAL_ACCENT = "#D97757"     # Laranja/Coral oficial Claude
CORAL_LIGHT = "#EA8C6F"      # Coral claro para destaque
COLOR_GREEN = "#34D399"      # Verde seguro (<50%)
COLOR_AMBER = "#FBBF24"      # Âmbar atenção (50%-80%)
COLOR_RED = "#F87171"        # Vermelho crítico (>80%)
BG_SEG_OFF = "#282834"       # Segmento desligado do medidor

class SegmentedMeter(tk.Canvas):
    """
    Medidor segmentado de 18 blocos exatamente como a tela touch de 3.5" do Usage Stick.
    Muda suavemente de verde para âmbar e vermelho conforme a porcentagem aumenta.
    """
    def __init__(self, parent, total_segments=18, height=18, **kwargs):
        super().__init__(parent, height=height, bg=BG_CARD, highlightthickness=0, **kwargs)
        self.total_segments = total_segments
        self.percentage = 0.0
        self.bind("<Configure>", lambda e: self.draw())

    def set_percentage(self, pct: float):
        self.percentage = max(0.0, min(100.0, pct))
        self.draw()

    def get_segment_color(self, index: int, active_count: int) -> str:
        ratio = index / float(self.total_segments)
        if ratio < 0.5:
            return COLOR_GREEN
        elif ratio < 0.8:
            return COLOR_AMBER
        else:
            return COLOR_RED

    def draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1:
            return

        spacing = 3
        seg_w = max(4, (w - (self.total_segments - 1) * spacing) / self.total_segments)
        active_count = int(round((self.percentage / 100.0) * self.total_segments))

        for i in range(self.total_segments):
            x0 = i * (seg_w + spacing)
            x1 = x0 + seg_w
            y0 = 2
            y1 = h - 2
            color = self.get_segment_color(i, active_count) if i < active_count else BG_SEG_OFF
            self.create_rectangle(x0, y0, x1, y1, fill=color, outline="", width=0)

class ClaudeUsageApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Claude Usage Monitor")
        self.root.geometry("680x560")
        self.root.minsize(580, 480)
        self.root.configure(bg=BG_DARK)

        # Aplicar Dark Mode na barra de título do Windows 10/11
        self._apply_windows_dark_titlebar()

        # Configurações e Estado
        self.config = load_config()
        self.poll_interval = self.config.get("poll_interval_sec", 120)
        self.probe_models = self.config.get("probe_models", True)
        self.always_on_top = self.config.get("always_on_top", False)
        self.is_mini_mode = self.config.get("mini_mode", False)

        self.root.attributes("-topmost", self.always_on_top)

        # Monitor Core
        self.monitor = ClaudeMonitor()
        self.token, self.token_origin = get_claude_token(self.config.get("manual_token"))

        self.usage_data = UsageData()
        self.token_data = TokenUsage()
        self.model_probes: List[ModelProbe] = []
        self.incidents: List[Incident] = []

        self.last_poll_time = 0.0
        self.is_fetching = False

        # Construção da Interface
        self._build_ui()

        # Iniciar primeiro refresh e loop de temporizador
        self.refresh_data()
        self.root.after(1000, self._timer_tick)

    def _apply_windows_dark_titlebar(self):
        """Aplica tema escuro nativo na barra de título no Windows."""
        if sys.platform == "win32":
            try:
                DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                set_window_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
                hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
                value = ctypes.c_int(2)
                set_window_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value))
            except Exception:
                pass

    def _build_ui(self):
        # Container Principal
        self.main_container = tk.Frame(self.root, bg=BG_DARK)
        self.main_container.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)

        # ── 1. CABEÇALHO ───────────────────────────────────────
        header_frame = tk.Frame(self.main_container, bg=BG_DARK)
        header_frame.pack(fill=tk.X, pady=(0, 8))

        # Título e Ícone
        title_box = tk.Frame(header_frame, bg=BG_DARK)
        title_box.pack(side=tk.LEFT)

        clawd_icon = tk.Label(
            title_box, text="✦", font=("Segoe UI", 16, "bold"),
            fg=CORAL_ACCENT, bg=BG_DARK
        )
        clawd_icon.pack(side=tk.LEFT, padx=(0, 6))

        title_lbl = tk.Label(
            title_box, text="CLAUDE USAGE", font=("Segoe UI", 13, "bold"),
            fg=TEXT_PRIMARY, bg=BG_DARK
        )
        title_lbl.pack(side=tk.LEFT)

        sub_title = tk.Label(
            title_box, text="MONITOR", font=("Segoe UI", 13),
            fg=CORAL_ACCENT, bg=BG_DARK
        )
        sub_title.pack(side=tk.LEFT, padx=(4, 8))

        # Chip de Status Geral
        self.status_chip = tk.Label(
            title_box, text="CONECTANDO...", font=("Segoe UI", 8, "bold"),
            fg="#FFFFFF", bg="#3B3B48", padx=8, pady=2
        )
        self.status_chip.pack(side=tk.LEFT)

        # Botões de Ação na Direita
        btn_box = tk.Frame(header_frame, bg=BG_DARK)
        btn_box.pack(side=tk.RIGHT)

        self.btn_top = tk.Button(
            btn_box, text="📌 Topo", font=("Segoe UI", 9),
            bg=CORAL_ACCENT if self.always_on_top else BG_CARD,
            fg="#FFFFFF" if self.always_on_top else TEXT_MUTED,
            relief=tk.FLAT, activebackground=BG_CARD_HOVER,
            activeforeground=TEXT_PRIMARY, cursor="hand2",
            padx=8, pady=3, command=self.toggle_always_on_top
        )
        self.btn_top.pack(side=tk.LEFT, padx=3)

        self.btn_mini = tk.Button(
            btn_box, text="🗗 Mini", font=("Segoe UI", 9),
            bg=BG_CARD, fg=TEXT_MUTED, relief=tk.FLAT,
            activebackground=BG_CARD_HOVER, activeforeground=TEXT_PRIMARY,
            cursor="hand2", padx=8, pady=3, command=self.toggle_mini_mode
        )
        self.btn_mini.pack(side=tk.LEFT, padx=3)

        self.btn_refresh = tk.Button(
            btn_box, text="🔄 Atualizar", font=("Segoe UI", 9, "bold"),
            bg=CORAL_ACCENT, fg="#FFFFFF", relief=tk.FLAT,
            activebackground=CORAL_LIGHT, activeforeground="#FFFFFF",
            cursor="hand2", padx=10, pady=3, command=self.refresh_data
        )
        self.btn_refresh.pack(side=tk.LEFT, padx=3)

        # Barra fina de escoamento até o próximo refresh (como no Stick original)
        self.countdown_canvas = tk.Canvas(
            self.main_container, height=3, bg=BG_CARD,
            highlightthickness=0
        )
        self.countdown_canvas.pack(fill=tk.X, pady=(0, 10))

        # ── 2. CARDS PRINCIPAIS (5h e 7d) ──────────────────────
        cards_row = tk.Frame(self.main_container, bg=BG_DARK)
        cards_row.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        cards_row.columnconfigure(0, weight=1)
        cards_row.columnconfigure(1, weight=1)

        # ── Card Janela 5h ─────────────────────────────────────
        self.card_5h = tk.Frame(
            cards_row, bg=BG_CARD, highlightbackground=BORDER_COLOR,
            highlightthickness=1, padx=14, pady=12
        )
        self.card_5h.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        h5_head = tk.Frame(self.card_5h, bg=BG_CARD)
        h5_head.pack(fill=tk.X)
        tk.Label(
            h5_head, text="JANELA DE 5 HORAS", font=("Segoe UI", 9, "bold"),
            fg=TEXT_MUTED, bg=BG_CARD
        ).pack(side=tk.LEFT)
        self.h5_chip = tk.Label(
            h5_head, text="--", font=("Segoe UI", 8, "bold"),
            fg="#FFFFFF", bg=BG_SEG_OFF, padx=6, pady=1
        )
        self.h5_chip.pack(side=tk.RIGHT)

        # Percentual Grande
        self.h5_pct_lbl = tk.Label(
            self.card_5h, text="0%", font=("Segoe UI", 32, "bold"),
            fg=COLOR_GREEN, bg=BG_CARD
        )
        self.h5_pct_lbl.pack(anchor="w", pady=(4, 2))

        # Medidor Segmentado 5h
        self.meter_5h = SegmentedMeter(self.card_5h, height=14)
        self.meter_5h.pack(fill=tk.X, pady=(0, 10))

        # Contagem regressiva ao vivo
        self.h5_countdown_lbl = tk.Label(
            self.card_5h, text="⏳ Reset em: --:--:--",
            font=("Segoe UI", 11, "bold"), fg=TEXT_PRIMARY, bg=BG_CARD
        )
        self.h5_countdown_lbl.pack(anchor="w")

        self.h5_reset_time_lbl = tk.Label(
            self.card_5h, text="Horário local do reset: --:--",
            font=("Segoe UI", 9), fg=TEXT_MUTED, bg=BG_CARD
        )
        self.h5_reset_time_lbl.pack(anchor="w", pady=(2, 6))

        # Projeção de Consumo
        self.h5_proj_lbl = tk.Label(
            self.card_5h, text="Projeção: Calculando...",
            font=("Segoe UI", 9, "bold"), fg=COLOR_GREEN, bg=BG_CARD,
            wraplength=250, justify=tk.LEFT
        )
        self.h5_proj_lbl.pack(anchor="w")

        # ── Card Janela 7d (Semanal) ───────────────────────────
        self.card_7d = tk.Frame(
            cards_row, bg=BG_CARD, highlightbackground=BORDER_COLOR,
            highlightthickness=1, padx=14, pady=12
        )
        self.card_7d.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        d7_head = tk.Frame(self.card_7d, bg=BG_CARD)
        d7_head.pack(fill=tk.X)
        tk.Label(
            d7_head, text="JANELA SEMANAL (7 DIAS)", font=("Segoe UI", 9, "bold"),
            fg=TEXT_MUTED, bg=BG_CARD
        ).pack(side=tk.LEFT)
        self.d7_chip = tk.Label(
            d7_head, text="--", font=("Segoe UI", 8, "bold"),
            fg="#FFFFFF", bg=BG_SEG_OFF, padx=6, pady=1
        )
        self.d7_chip.pack(side=tk.RIGHT)

        # Percentual Grande
        self.d7_pct_lbl = tk.Label(
            self.card_7d, text="0%", font=("Segoe UI", 32, "bold"),
            fg=COLOR_GREEN, bg=BG_CARD
        )
        self.d7_pct_lbl.pack(anchor="w", pady=(4, 2))

        # Medidor Segmentado 7d
        self.meter_7d = SegmentedMeter(self.card_7d, height=14)
        self.meter_7d.pack(fill=tk.X, pady=(0, 10))

        # Contagem regressiva ao vivo
        self.d7_countdown_lbl = tk.Label(
            self.card_7d, text="⏳ Reset em: --d --h",
            font=("Segoe UI", 11, "bold"), fg=TEXT_PRIMARY, bg=BG_CARD
        )
        self.d7_countdown_lbl.pack(anchor="w")

        self.d7_reset_time_lbl = tk.Label(
            self.card_7d, text="Data do reset: --/-- --:--",
            font=("Segoe UI", 9), fg=TEXT_MUTED, bg=BG_CARD
        )
        self.d7_reset_time_lbl.pack(anchor="w", pady=(2, 6))

        self.claim_lbl = tk.Label(
            self.card_7d, text="Fator limitante: --",
            font=("Segoe UI", 9), fg=CORAL_LIGHT, bg=BG_CARD
        )
        self.claim_lbl.pack(anchor="w")

        # ── 3. LINHA INFERIOR (Tokens Locais + Modelos/Status) ──
        bottom_row = tk.Frame(self.main_container, bg=BG_DARK)
        bottom_row.pack(fill=tk.X, pady=(0, 6))
        bottom_row.columnconfigure(0, weight=1)
        bottom_row.columnconfigure(1, weight=1)

        # Card de Tokens Locais (Claude Code)
        card_tokens = tk.Frame(
            bottom_row, bg=BG_CARD, highlightbackground=BORDER_COLOR,
            highlightthickness=1, padx=12, pady=10
        )
        card_tokens.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        tk.Label(
            card_tokens, text="TOKENS NA JANELA DE 5H (CLAUDE CODE)",
            font=("Segoe UI", 8, "bold"), fg=TEXT_MUTED, bg=BG_CARD
        ).pack(anchor="w", pady=(0, 6))

        token_grid = tk.Frame(card_tokens, bg=BG_CARD)
        token_grid.pack(fill=tk.X)

        self.lbl_token_in = tk.Label(
            token_grid, text="Entrada: --", font=("Segoe UI", 9, "bold"),
            fg=TEXT_PRIMARY, bg=BG_CARD
        )
        self.lbl_token_in.grid(row=0, column=0, sticky="w", padx=(0, 12))

        self.lbl_token_out = tk.Label(
            token_grid, text="Saída: --", font=("Segoe UI", 9, "bold"),
            fg=TEXT_PRIMARY, bg=BG_CARD
        )
        self.lbl_token_out.grid(row=0, column=1, sticky="w")

        self.lbl_token_cache = tk.Label(
            token_grid, text="Cache: --", font=("Segoe UI", 9),
            fg=TEXT_MUTED, bg=BG_CARD
        )
        self.lbl_token_cache.grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.lbl_sessions = tk.Label(
            token_grid, text="Sessões: --", font=("Segoe UI", 9),
            fg=TEXT_MUTED, bg=BG_CARD
        )
        self.lbl_sessions.grid(row=1, column=1, sticky="w", pady=(4, 0))

        # Card de Modelos e Incidentes
        card_health = tk.Frame(
            bottom_row, bg=BG_CARD, highlightbackground=BORDER_COLOR,
            highlightthickness=1, padx=12, pady=10
        )
        card_health.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        tk.Label(
            card_health, text="SAÚDE DA API & MODELOS",
            font=("Segoe UI", 8, "bold"), fg=TEXT_MUTED, bg=BG_CARD
        ).pack(anchor="w", pady=(0, 6))

        self.models_box = tk.Frame(card_health, bg=BG_CARD)
        self.models_box.pack(fill=tk.X)

        # Chips dos Modelos
        self.model_labels: dict = {}
        for m_name in ["Haiku", "Sonnet", "Opus", "Fable"]:
            lbl = tk.Label(
                self.models_box, text=f"{m_name}: --", font=("Segoe UI", 8, "bold"),
                fg=TEXT_MUTED, bg=BG_SEG_OFF, padx=6, pady=2
            )
            lbl.pack(side=tk.LEFT, padx=(0, 6))
            self.model_labels[m_name] = lbl

        self.incidents_lbl = tk.Label(
            card_health, text="status.claude.com: Verificando...",
            font=("Segoe UI", 8), fg=COLOR_GREEN, bg=BG_CARD
        )
        self.incidents_lbl.pack(anchor="w", pady=(6, 0))

        # ── 4. RODAPÉ ──────────────────────────────────────────
        footer = tk.Frame(self.main_container, bg=BG_DARK)
        footer.pack(fill=tk.X, pady=(6, 0))

        self.lbl_account_info = tk.Label(
            footer, text=f"Conta: {self.token_origin}",
            font=("Segoe UI", 8), fg=TEXT_MUTED, bg=BG_DARK
        )
        self.lbl_account_info.pack(side=tk.LEFT)

        self.lbl_last_update = tk.Label(
            footer, text="Última atualização: --:--:--",
            font=("Segoe UI", 8), fg=TEXT_MUTED, bg=BG_DARK
        )
        self.lbl_last_update.pack(side=tk.RIGHT)

    def refresh_data(self):
        """Dispara a busca de dados em segundo plano para não travar a interface."""
        if self.is_fetching:
            return

        self.is_fetching = True
        self.btn_refresh.config(text="⏳ Buscando...", state=tk.DISABLED)

        # Thread de rede
        def worker():
            token, origin = get_claude_token(self.config.get("manual_token"))
            self.token = token
            self.token_origin = origin

            if not token:
                usage = UsageData(ok=False, error_msg="Nenhum token encontrado!")
                tok_usage = TokenUsage()
                probes = []
                incidents = []
            else:
                usage = self.monitor.fetch_usage(token)
                tok_usage = self.monitor.collect_local_tokens(usage.h5_reset_epoch if usage.ok else None)
                probes = self.monitor.probe_models(token) if self.probe_models else []
                incidents = self.monitor.fetch_incidents()

            # Retornar para a thread do Tkinter
            self.root.after(0, lambda: self._on_data_ready(usage, tok_usage, probes, incidents))

        threading.Thread(target=worker, daemon=True).start()

    def _on_data_ready(self, usage: UsageData, tokens: TokenUsage,
                       probes: List[ModelProbe], incidents: List[Incident]):
        self.is_fetching = False
        self.btn_refresh.config(text="🔄 Atualizar", state=tk.NORMAL)
        self.last_poll_time = time.time()

        self.usage_data = usage
        self.token_data = tokens
        self.model_probes = probes
        self.incidents = incidents

        if not usage.ok:
            self.status_chip.config(text="ERRO API", bg=COLOR_RED)
            messagebox.showwarning("Aviso de Conexão", f"Não foi possível consultar os limites do Claude:\n{usage.error_msg}")
            return

        # Status geral
        st = usage.unified_status.lower()
        if st == "allowed":
            self.status_chip.config(text="● ONLINE", bg="#059669")
        elif st == "allowed_warning":
            self.status_chip.config(text="⚠ ATENÇÃO", bg="#D97706")
        else:
            self.status_chip.config(text="⛔ BLOQUEADO", bg=COLOR_RED)

        # Atualizar Janela 5h
        self.h5_pct_lbl.config(
            text=f"{usage.h5_utilization:.0f}%",
            fg=self._get_color(usage.h5_utilization)
        )
        self.meter_5h.set_percentage(usage.h5_utilization)
        self.h5_chip.config(
            text=usage.h5_status.upper(),
            bg=self._get_color(usage.h5_utilization)
        )

        h5_reset_time = datetime.fromtimestamp(usage.h5_reset_epoch).strftime("%H:%M") if usage.h5_reset_epoch else "--:--"
        self.h5_reset_time_lbl.config(text=f"Horário local do reset: {h5_reset_time}")

        proj_text, proj_color = self.monitor.get_projection_text(usage.h5_utilization, usage.h5_reset_epoch)
        self.h5_proj_lbl.config(text=f"Projeção: {proj_text}", fg=proj_color)

        # Atualizar Janela 7d
        self.d7_pct_lbl.config(
            text=f"{usage.d7_utilization:.0f}%",
            fg=self._get_color(usage.d7_utilization)
        )
        self.meter_7d.set_percentage(usage.d7_utilization)
        self.d7_chip.config(
            text=usage.d7_status.upper(),
            bg=self._get_color(usage.d7_utilization)
        )

        d7_reset_date = datetime.fromtimestamp(usage.d7_reset_epoch).strftime("%d/%m %H:%M") if usage.d7_reset_epoch else "--/-- --:--"
        self.d7_reset_time_lbl.config(text=f"Data do reset semanal: {d7_reset_date}")

        claim = "Janela de 5h" if usage.representative_claim == "five_hour" else "Janela de 7d"
        self.claim_lbl.config(text=f"Fator limitante principal: {claim}")

        # Atualizar Tokens
        self.lbl_token_in.config(text=f"Entrada: {self.monitor.format_tokens(tokens.input_tokens)}")
        self.lbl_token_out.config(text=f"Saída: {self.monitor.format_tokens(tokens.output_tokens)}")
        self.lbl_token_cache.config(text=f"Cache: {self.monitor.format_tokens(tokens.cache_tokens)}")
        self.lbl_sessions.config(text=f"Sessões: {tokens.sessions_count} ativas")

        # Atualizar Modelos
        for probe in probes:
            if probe.display_name in self.model_labels:
                lbl = self.model_labels[probe.display_name]
                if probe.status_code == 200:
                    lbl.config(text=f"{probe.display_name}: {probe.latency_ms}ms", bg="#065F46", fg="#A7F3D0")
                elif probe.status_code == 429:
                    lbl.config(text=f"{probe.display_name}: LIMITADO", bg="#B45309", fg="#FDE68A")
                else:
                    lbl.config(text=f"{probe.display_name}: ERRO", bg="#991B1B", fg="#FECACA")

        # Atualizar Incidentes
        if incidents:
            self.incidents_lbl.config(
                text=f"⚠ Incidentes ativos: {len(incidents)} problema(s) reportado(s)",
                fg=COLOR_RED
            )
        else:
            self.incidents_lbl.config(
                text="✓ status.claude.com: Todos os sistemas operacionais",
                fg=COLOR_GREEN
            )

        self.lbl_account_info.config(text=f"Autenticação: {self.token_origin}")
        self.lbl_last_update.config(text=f"Última atualização: {datetime.now().strftime('%H:%M:%S')}")

        # Atualizar timer imediatamente
        self._update_countdowns()

    def _timer_tick(self):
        """Executado a cada 1 segundo para manter contadores ao vivo."""
        self._update_countdowns()
        self._update_refresh_bar()

        # Verificar se é hora do refresh automático
        if self.last_poll_time > 0 and time.time() - self.last_poll_time >= self.poll_interval:
            self.refresh_data()

        self.root.after(1000, self._timer_tick)

    def _update_countdowns(self):
        now = time.time()
        # Janela 5h
        if self.usage_data.h5_reset_epoch:
            sec_5h = max(0, self.usage_data.h5_reset_epoch - now)
            self.h5_countdown_lbl.config(text=f"⏳ Reset em: {self.monitor.format_countdown(sec_5h)}")

        # Janela 7d
        if self.usage_data.d7_reset_epoch:
            sec_7d = max(0, self.usage_data.d7_reset_epoch - now)
            self.d7_countdown_lbl.config(text=f"⏳ Reset em: {self.monitor.format_countdown(sec_7d)}")

    def _update_refresh_bar(self):
        """Desenha a barrinha fina de progresso até a próxima consulta."""
        w = self.countdown_canvas.winfo_width()
        if w <= 1 or self.last_poll_time == 0:
            return

        elapsed = time.time() - self.last_poll_time
        remaining = max(0, self.poll_interval - elapsed)
        fraction = remaining / float(self.poll_interval)
        fill_w = int(w * fraction)

        self.countdown_canvas.delete("all")
        if fill_w > 0:
            self.countdown_canvas.create_rectangle(0, 0, fill_w, 3, fill=CORAL_ACCENT, width=0)

    def _get_color(self, pct: float) -> str:
        if pct < 50:
            return COLOR_GREEN
        elif pct < 80:
            return COLOR_AMBER
        return COLOR_RED

    def toggle_always_on_top(self):
        self.always_on_top = not self.always_on_top
        self.root.attributes("-topmost", self.always_on_top)
        self.btn_top.config(
            bg=CORAL_ACCENT if self.always_on_top else BG_CARD,
            fg="#FFFFFF" if self.always_on_top else TEXT_MUTED
        )
        self.config["always_on_top"] = self.always_on_top
        save_config(self.config)

    def toggle_mini_mode(self):
        """Alterna entre janela completa e mini-widget discreto."""
        if not self.is_mini_mode:
            # Ativar modo mini
            self.is_mini_mode = True
            self.root.geometry("380x160")
            self.card_7d.grid_remove()
            self.lbl_token_cache.grid_remove()
            self.lbl_sessions.grid_remove()
            self.models_box.pack_forget()
            self.btn_mini.config(text="🗖 Completo", fg=CORAL_ACCENT)
        else:
            # Voltar ao modo completo
            self.is_mini_mode = False
            self.root.geometry("680x560")
            self.card_7d.grid()
            self.lbl_token_cache.grid()
            self.lbl_sessions.grid()
            self.models_box.pack(fill=tk.X)
            self.btn_mini.config(text="🗗 Mini", fg=TEXT_MUTED)

        self.config["mini_mode"] = self.is_mini_mode
        save_config(self.config)

def main():
    root = tk.Tk()
    app = ClaudeUsageApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
