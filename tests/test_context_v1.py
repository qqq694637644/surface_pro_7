from sp7_powerlab.context import ContextEngine


def infer(app="code", afk=False, classes=None, playing=False, cpu=10, load=0.5):
    return ContextEngine().infer(
        activity={"app": app, "executable": app, "title": "", "afk": afk},
        process_summary={"class_cpu_percent": classes or {}, "background_cpu_percent": cpu},
        media={"playing": playing},
        system={"cpu_usage": cpu, "load1": load, "battery_status": "Discharging"},
    )


def test_afk_is_idle():
    assert infer(afk=True).scene == "idle"


def test_compile_overrides_editor():
    result = infer(app="code", classes={"compile": 80.0}, cpu=80)
    assert result.scene == "compile"
    assert result.confidence >= 0.8


def test_media_and_compile_becomes_mixed():
    result = infer(app="firefox", classes={"compile": 30.0}, playing=True, cpu=50)
    assert result.scene == "mixed"


def test_unknown_is_explicit_not_guessed():
    result = infer(app="some-new-app", classes={}, cpu=5)
    assert result.scene == "unknown"
    assert result.confidence < 0.5
