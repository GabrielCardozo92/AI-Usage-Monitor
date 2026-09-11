"""Projeções de consumo (5h e semanal), meta diária e formatação."""
import pytest

from monitor_core import ClaudeMonitor as M

NOW = 1_800_000_000

@pytest.mark.parametrize("util,mins_left,expected", [
    (0, 290, "Início da janela de 5h"),
    (10, 299.5, "Início da janela de 5h"),        # menos de 1 min de janela
    (40, 180, "NÃO acaba antes do reset (~100%)"),
    (50, -1, "Janela resetando agora"),
])
def test_5h_projection_texts(freeze_time, util, mins_left, expected):
    freeze_time(NOW)
    assert M.get_projection_text(util, int(NOW + mins_left * 60))[0] == expected

def test_5h_projection_exhausts_soon_is_red(freeze_time):
    freeze_time(NOW)
    text, color = M.get_projection_text(90, NOW + 200 * 60)
    assert text.startswith("No ritmo atual, esgota às") and "(em 11m)" in text
    assert color == "#F87171"

@pytest.mark.parametrize("util,days_left,expected,color", [
    (16, 3.8, "Chega a ~35% no reset", "#4ADE80"),
    (70, 3.0, "(em 1d 17h)", "#FBBF24"),
    (95, 1.5, "(em 6h56m)", "#F87171"),
    (99.5, 0.3, "(em 48m)", "#F87171"),
    (3, 6.9, "Início da semana (projeção após 12h)", "#8E8E9B"),
])
def test_weekly_projection(freeze_time, util, days_left, expected, color):
    freeze_time(NOW)
    text, got_color = M.get_weekly_projection_text(util, int(NOW + days_left * 86400))
    assert expected in text and got_color == color

@pytest.mark.parametrize("util,days_left,expected", [
    (16, 3.8, "~22%/dia até o reset (3,8 dias)"),
    (60, 0.5, "40% restante até o reset"),
    (99.5, 0.3, "menos de 1% restante até o reset"),
    (10, -1, "--"),
])
def test_daily_budget(freeze_time, util, days_left, expected):
    freeze_time(NOW)
    assert M.get_daily_budget_text(util, int(NOW + days_left * 86400)) == expected

def test_formatters():
    assert M.format_countdown(0) == "00m 00s"
    assert M.format_countdown(3725) == "01h 02m 05s"
    assert M.format_countdown(3 * 86400 + 3600 * 5) == "3d 05h 00m"
    assert M.format_tokens(950) == "950"
    assert M.format_tokens(253_741) == "253.7k"
    assert M.format_tokens(1_200_000) == "1.2M"
