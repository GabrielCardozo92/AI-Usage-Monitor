"""Histórico do uso semanal (usage_history.py)."""
import time

import usage_history

RESET = 1_800_000_000
START = RESET - 7 * 86400
DAY = 86400

def sample(day, hour, d7, reset=RESET):
    return {"t": START + day * DAY + hour * 3600, "d7": d7, "d7_reset": reset}

def values(days):
    return [(value, partial) for _, value, partial in days]

def test_daily_usage_full_week_ignores_stale_readings():
    samples = [sample(0, 2, 3), sample(0, 20, 5), sample(1, 5, 12), sample(2, 10, 12),
               sample(3, 4, 20), sample(3, 6, 18),  # 18 = sessão parada, com dado atrasado
               sample(3, 9, 24)]
    days = usage_history.daily_usage(samples, RESET, now=START + 3 * DAY + 10 * 3600)
    assert values(days) == [(5, False), (7, False), (0, False), (12, False)]

def test_daily_usage_started_mid_week_is_partial():
    days = usage_history.daily_usage([sample(3, 4, 15), sample(3, 9, 18)], RESET, now=START + 3 * DAY + 10 * 3600)
    assert values(days) == [(None, False), (None, False), (None, False), (3, True)]

def test_single_reading_in_day_is_unknown_not_zero():
    days = usage_history.daily_usage([sample(3, 4, 15)], RESET, now=START + 3 * DAY + 10 * 3600)
    assert values(days)[-1] == (None, False)

def test_readings_from_another_week_are_ignored():
    days = usage_history.daily_usage([sample(1, 5, 50, reset=RESET - 7 * DAY)], RESET, now=START + 2 * DAY)
    assert values(days) == [(None, False), (None, False)]

def test_format_daily():
    labels = [("ter", None, False), ("qua", 8.2, False), ("qui", 3.0, True)]
    assert usage_history.format_daily(labels) == "ter -- · qua 8% · qui ≥3%"

def test_append_only_when_changed_and_prune(freeze_time):
    now = freeze_time(time.time())
    usage_history.append_sample(10, 16.0, RESET)
    usage_history.append_sample(10, 16.0, RESET)   # igual: não anota
    usage_history.append_sample(11, 16.0, RESET)
    usage_history.append_sample(None, None, RESET)  # sem uso semanal: ignora
    usage_history.append_sample(11.000000000000002, 16.0, RESET)  # ruído de ponto flutuante: igual
    assert [s["h5"] for s in usage_history.load_samples()] == [10, 11]

    with open(usage_history.HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write('{"h5": 1, "d7": 1, "d7_reset": 1, "t": %d}\n{corrompida\n' % (now - 9 * DAY))
    usage_history.prune()
    assert [s["h5"] for s in usage_history.load_samples()] == [10, 11]
