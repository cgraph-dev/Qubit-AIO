"""Brain off/on voice actions: deterministic Ollama control, no model call."""
import pytest

from voicebridge.local import actions as LA


@pytest.mark.parametrize("phrase", [
    "stop brain",
    "stop the brain",
    "stop brain while I'm playing",
    "unload the brain",
    "free the VRAM",
    "free VRAM",
    "I'm going to game",
    "I'm going gaming",
    "qubit, I'm going to game",
    "sleep",
    "go to sleep",
    "release the brain",
    "drop the model",
    "clear the GPU",
    "brain off",
])
def test_brain_unload_routes(phrase):
    m = LA.match(phrase)
    assert m is not None and m.name == "brain_unload", (phrase, m)


@pytest.mark.parametrize("phrase", [
    "wake up",
    "wake up the brain",
    "start the brain",
    "start brain",
    "boot the brain",
    "resume the brain",
    "welcome to the brain",
    "load the brain",
    "load brain",
    "warm up the brain",
    "reload the model",
    "bring the brain back",
    "I'm done gaming",
    "I'm finished playing",
])
def test_brain_warm_routes(phrase):
    m = LA.match(phrase)
    assert m is not None and m.name == "brain_warm", (phrase, m)


def test_brain_actions_do_not_shadow_media_or_youtube():
    assert LA.match("stop the music").name == "youtube_stop"
    assert LA.match("pause the music").name == "youtube_pause"


def test_unload_is_registered_as_data():
    assert LA.ACTIONS["brain_unload"].fn is LA._a_brain_unload
    assert LA.ACTIONS["brain_warm"].fn is LA._a_brain_warm


def test_unload_when_ollama_is_down(monkeypatch):
    called = []
    monkeypatch.setattr(LA, "_ollama_loaded_models", lambda: None)
    monkeypatch.setattr(LA, "_brain_stop", lambda m: called.append(m) or (True, ""))
    msg = LA.execute(LA.match("stop brain"))
    assert "nothing to unload" in msg, msg
    assert called == []


def test_unload_when_nothing_loaded(monkeypatch):
    called = []
    monkeypatch.setattr(LA, "_ollama_loaded_models", lambda: {"llama3"})
    monkeypatch.setattr(LA, "_brain_stop", lambda m: called.append(m) or (True, ""))
    msg = LA.execute(LA.match("free the VRAM"))
    assert "nothing to unload" in msg, msg
    assert called == []


def test_unload_stops_brain_and_embedding(monkeypatch):
    called = []
    monkeypatch.setattr(LA, "_ollama_loaded_models",
                        lambda: {"qubit-brain", "nomic-embed-text"})
    monkeypatch.setattr(LA, "_brain_stop", lambda m: called.append(m) or (True, ""))
    msg = LA.execute(LA.match("I'm going to game"))
    assert msg == "Brain and embeddings unloaded.", msg
    assert called == ["qubit-brain", "nomic-embed-text"], called


def test_unload_brain_only(monkeypatch):
    called = []
    monkeypatch.setattr(LA, "_ollama_loaded_models", lambda: {"qubit-brain"})
    monkeypatch.setattr(LA, "_brain_stop", lambda m: called.append(m) or (True, ""))
    msg = LA.execute(LA.match("unload the brain"))
    assert msg == "Brain unloaded.", msg
    assert called == ["qubit-brain"], called


def test_unload_failure_is_honest(monkeypatch):
    monkeypatch.setattr(LA, "_ollama_loaded_models", lambda: {"qubit-brain"})
    monkeypatch.setattr(LA, "_brain_stop", lambda m: (False, "daemon busy"))
    msg = LA.execute(LA.match("stop brain"))
    assert "couldn't unload" in msg and "daemon busy" in msg, msg


def test_warm_when_already_loaded(monkeypatch):
    monkeypatch.setattr(LA, "_ollama_loaded_models", lambda: {"qubit-brain"})
    spawned = []
    monkeypatch.setattr(LA, "_brain_spawn_warm", lambda: spawned.append(True) or True)
    msg = LA.execute(LA.match("load the brain"))
    assert "already loaded" in msg, msg
    assert spawned == []


def test_warm_when_ollama_is_down(monkeypatch):
    monkeypatch.setattr(LA, "_ollama_loaded_models", lambda: None)
    msg = LA.execute(LA.match("wake up"))
    assert "Ollama isn't running" in msg, msg


def test_warm_when_model_missing(monkeypatch):
    monkeypatch.setattr(LA, "_ollama_loaded_models", lambda: set())
    monkeypatch.setattr(LA, "_brain_installed", lambda m: False)
    msg = LA.execute(LA.match("load the brain"))
    assert "isn't installed" in msg, msg


def test_warm_spawns_without_blocking(monkeypatch):
    monkeypatch.setattr(LA, "_ollama_loaded_models", lambda: set())
    monkeypatch.setattr(LA, "_brain_installed", lambda m: True)
    spawned = []
    monkeypatch.setattr(LA, "_brain_spawn_warm",
                        lambda: spawned.append(True) or True)
    msg = LA.execute(LA.match("wake up the brain"))
    assert msg == "Warming the brain.", msg
    assert spawned == [True], spawned


def test_brain_catalog_examples_are_verified():
    from voicebridge.voice_edit import custom_commands as CC
    for action in ("brain_unload", "brain_warm"):
        examples = CC._verified_examples(action, LA.match)
        assert examples, action
        assert all(e["verified"] for e in examples), (action, examples)


def test_unload_stops_embeddinggemma_too(monkeypatch):
    called = []
    monkeypatch.setattr(LA, "_ollama_loaded_models",
                        lambda: {"qubit-brain", "embeddinggemma"})
    monkeypatch.setattr(LA, "_brain_stop", lambda m: called.append(m) or (True, ""))
    msg = LA.execute(LA.match("clear the GPU"))
    assert msg == "Brain and embeddings unloaded.", msg
    assert called == ["qubit-brain", "embeddinggemma"], called


def test_warm_payload_is_one_token_at_voice_context():
    payload = LA._brain_warm_payload()
    assert payload["model"] == "qubit-brain"
    assert payload["prompt"] == "hi"
    assert payload["stream"] is False
    assert payload["options"]["num_predict"] == 1
    assert payload["options"]["num_ctx"] == 32768
    assert payload["keep_alive"] == "10m"


def test_warm_spawn_is_detached(monkeypatch):
    import sys
    calls = []

    class _P:
        pass

    def _popen(argv, **kw):
        calls.append((argv, kw))
        return _P()

    monkeypatch.setattr(LA.subprocess, "Popen", _popen)
    assert LA._brain_spawn_warm() is True
    argv, kw = calls[0]
    assert argv[0] == sys.executable
    assert argv[1].endswith("brain_warm.py"), argv
    assert kw.get("start_new_session") is True
    assert kw.get("stdin") is LA.subprocess.DEVNULL


def test_unload_stops_full_model_tags(monkeypatch):
    called = []
    monkeypatch.setattr(LA, "_ollama_loaded_models",
                        lambda: {"qubit-brain:latest", "embeddinggemma:300m"})
    monkeypatch.setattr(LA, "_brain_stop", lambda m: called.append(m) or (True, ""))
    msg = LA.execute(LA.match("free the VRAM"))
    assert msg == "Brain and embeddings unloaded.", msg
    assert called == ["qubit-brain:latest", "embeddinggemma:300m"], called


def test_warm_detects_tagged_brain(monkeypatch):
    monkeypatch.setattr(LA, "_ollama_loaded_models",
                        lambda: {"qubit-brain:latest"})
    spawned = []
    monkeypatch.setattr(LA, "_brain_spawn_warm", lambda: spawned.append(True) or True)
    msg = LA.execute(LA.match("wake up the brain"))
    assert "already loaded" in msg, msg
    assert spawned == []


def test_ollama_ansi_noise_is_stripped():
    noisy = "\x1b[?25lError: model busy\x1b[2K\x1b[1G"
    assert LA._clean_ollama_detail(noisy) == "Error: model busy"


def test_bare_qubit_power_commands_can_execute():
    from voicebridge.voice_edit import wake
    for phrase in ("qubit, wake up", "qubit, start the brain",
                   "qubit, load the brain", "qubit, sleep",
                   "qubit, I'm going to game", "qubit, stop brain"):
        remainder = wake.strip_wake(phrase)
        assert remainder, phrase
        assert wake.is_safe_bare_wake_command(remainder), (phrase, remainder)


def test_other_bare_qubit_commands_still_drop():
    from voicebridge.voice_edit import wake
    assert not wake.is_safe_bare_wake_command("open youtube")
    assert not wake.is_safe_bare_wake_command("what is the weather")
