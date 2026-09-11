"""statusline.py (rodapé do Claude Code) e a leitura desses dados pelo monitor."""
import io
import json
import sys
import time

import pytest

import monitor_core
import statusline
import usage_history

def payload(session_id="s1", h5=13.2, d7=16.0, used=253_741, window=1_000_000, pct=25.4, reset_in=3600):
    now = time.time()
    data = {"session_id": session_id, "model": {"id": "claude-opus-5"},
            "context_window": {"total_input_tokens": used, "context_window_size": window, "used_percentage": pct}}
    if h5 is not None:
        data["rate_limits"] = {"five_hour": {"used_percentage": h5, "resets_at": now + reset_in},
                               "seven_day": {"used_percentage": d7, "resets_at": now + 3 * 86400}}
    return data

def run_main(monkeypatch, capsys, raw: bytes) -> str:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))
    statusline.main()
    return capsys.readouterr().out

def test_context_thresholds_and_window_format():
    assert statusline.context_thresholds(0) == (300_000, 600_000)          # janela desconhecida
    assert statusline.context_thresholds(200_000) == (100_000, 160_000)
    assert statusline.context_thresholds(1_000_000) == (300_000, 600_000)
    assert statusline.format_window(1_000_000) == "1M"
    assert statusline.format_window(200_000) == "200k"

def test_render_line_strips_colors_to_expected_text():
    plain = lambda s: __import__("re").sub(r"\033\[[0-9;]*m", "", s)
    assert plain(statusline.render_line(payload())) == "5h 13% · 7d 16% · ctx 254k/1M (25%)"
    assert plain(statusline.render_line(payload(h5=None, used=170_000, window=200_000, pct=85))) == "ctx 170k/200k (85%)"
    assert statusline.render_line({"context_window": {"total_input_tokens": 0}}) == ""

def test_main_writes_snapshot_history_and_line(monkeypatch, capsys):
    out = run_main(monkeypatch, capsys, json.dumps(payload()).encode())
    assert out.endswith("\n") and "\r" not in out and "5h" in out
    snap = json.loads((statusline.STATUSLINE_DIR / "s1.json").read_text(encoding="utf-8"))
    assert snap["context_window_size"] == 1_000_000 and snap["rate_limits"]["five_hour"]["used_percentage"] == 13.2
    assert usage_history.load_samples()[-1]["d7"] == 16.0

def test_main_survives_invalid_input(monkeypatch, capsys):
    assert run_main(monkeypatch, capsys, b"isso nao e json") == ""

def write_snap(name, age, h5=None, d7=None):
    now = time.time()
    rate = {}
    if h5:
        rate["five_hour"] = {"used_percentage": h5[0], "resets_at": now + h5[1]}
    if d7:
        rate["seven_day"] = {"used_percentage": d7[0], "resets_at": now + d7[1]}
    statusline.STATUSLINE_DIR.mkdir(exist_ok=True)
    (statusline.STATUSLINE_DIR / f"{name}.json").write_text(
        json.dumps({"session_id": name, "captured_at": now - age, "rate_limits": rate}), encoding="utf-8")

def read():
    u = monitor_core.ClaudeMonitor().read_statusline_usage()
    return None if u is None else (u.h5_utilization, u.d7_utilization)

def test_prefers_highest_usage_in_newest_window_over_last_written():
    write_snap("fresh", 10, (17, 3600), (16, 90000))
    write_snap("stale", 5, (15, 3600), (16, 90000))  # gravado por último, mas com dado velho
    assert read() == (17.0, 16.0)

def test_new_window_beats_previous_one():
    write_snap("old", 10, (95, 60), (40, 90000))
    write_snap("new", 20, (2, 18000), (41, 90000))
    assert read() == (2.0, 41.0)

@pytest.mark.parametrize("age,h5,d7", [
    (400, (17, 3600), (16, 90000)),  # mais de 5 min: volta para a API
    (10, None, (16, 90000)),         # sem a janela de 5h (acabou de resetar)
    (10, (17, -5), (16, 90000)),     # reset da 5h já passou
])
def test_falls_back_to_api_when_data_is_unusable(age, h5, d7):
    write_snap("a", age, h5, d7)
    assert read() is None

def test_ignores_corrupt_and_deletes_day_old_files():
    statusline.STATUSLINE_DIR.mkdir(exist_ok=True)
    (statusline.STATUSLINE_DIR / "bad.json").write_text("{nao", encoding="utf-8")
    write_snap("antigo", 2 * 86400, (1, 3600), (1, 90000))
    write_snap("ok", 10, (17, 3600), (16, 90000))
    assert read() == (17.0, 16.0)
    assert not (statusline.STATUSLINE_DIR / "antigo.json").exists()

def test_statusline_usage_has_source_and_derived_status():
    write_snap("a", 10, (100, 3600), (16, 90000))
    u = monitor_core.ClaudeMonitor().read_statusline_usage()
    assert u.source == "status line" and u.h5_status == "rejected" and u.d7_status == "allowed"
    assert u.unified_status == "rejected"
