"""
main.py - Ponto de entrada do Claude Usage Monitor.
Inicia a interface gráfica moderna por padrão ou o modo terminal com --cli.
"""
import argparse
import sys
import tkinter as tk

# Garantir UTF-8 no terminal Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import get_claude_token, load_config
from monitor_core import ClaudeMonitor

def run_once():
    token, origin = get_claude_token()
    if not token:
        print("[ERRO] Nenhum token do Claude encontrado!")
        sys.exit(1)

    monitor = ClaudeMonitor()
    usage = monitor.fetch_usage(token)
    if not usage.ok:
        print(f"[ERRO] {usage.error_msg}")
        sys.exit(1)

    tokens = monitor.collect_local_tokens(usage.h5_reset_epoch)
    proj, _ = monitor.get_projection_text(usage.h5_utilization, usage.h5_reset_epoch)
    sec_5h = max(0, usage.h5_reset_epoch - usage.timestamp)
    sec_7d = max(0, usage.d7_reset_epoch - usage.timestamp)

    print("=" * 55)
    print(" [*] CLAUDE USAGE MONITOR - RELATORIO RAPIDO")
    print("=" * 55)
    print(f"Status Geral: {usage.unified_status.upper()}")
    print(f"Janela 5h:   {usage.h5_utilization:.0f}% | Reset em: {monitor.format_countdown(sec_5h)} | Status: {usage.h5_status.upper()}")
    print(f"Projecao:    {proj}")
    print(f"Janela 7d:   {usage.d7_utilization:.0f}% | Reset em: {monitor.format_countdown(sec_7d)} | Status: {usage.d7_status.upper()}")
    print(f"Limitador:   {'Janela 5h' if usage.representative_claim == 'five_hour' else 'Janela 7d'}")
    print("-" * 55)
    print(f"Tokens na janela de 5h (Claude Code local):")
    print(f"  - Entrada:  {monitor.format_tokens(tokens.input_tokens)} tokens")
    print(f"  - Saida:    {monitor.format_tokens(tokens.output_tokens)} tokens")
    print(f"  - Cache:    {monitor.format_tokens(tokens.cache_tokens)} tokens")
    print(f"  - Sessoes:  {tokens.sessions_count} ativas")
    print("=" * 55)

def main():
    parser = argparse.ArgumentParser(
        description="Claude Usage Monitor - Monitore seus limites e consumo do Claude Code no desktop."
    )
    parser.add_argument(
        "--cli", action="store_true",
        help="Executar em modo Terminal Interativo (Rich Dashboard)."
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Apenas exibir o uso atual uma vez no terminal e sair."
    )
    parser.add_argument(
        "--mini", action="store_true",
        help="Abrir a interface gráfica diretamente em modo mini-widget."
    )

    args = parser.parse_args()

    if args.once:
        run_once()
    elif args.cli:
        from cli_view import run_cli_loop
        cfg = load_config()
        run_cli_loop(
            poll_interval=cfg.get("poll_interval_sec", 120),
            probe_models=cfg.get("probe_models", True)
        )
    else:
        from gui import ClaudeUsageApp
        root = tk.Tk()
        app = ClaudeUsageApp(root)
        if args.mini and not app.is_mini_mode:
            app.toggle_mini_mode()
        root.mainloop()

if __name__ == "__main__":
    main()
