"""The settings screen: editing .env without damaging it, never sending a secret to the browser, and refusing remote use."""
import pytest
from fastapi.testclient import TestClient

from app import settings_admin as sa
from app.config import Settings
from app.main import create_app


def test_changing_a_value_keeps_comments_order_and_other_lines(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# my notes\nSTT_PROVIDER=assemblyai\nASSEMBLYAI_API_KEY=abc123\n\n# NATIVE_LANGUAGE=French\nPORT=8765\n")
    sa.write_env(f, {"STT_PROVIDER": "mock", "NATIVE_LANGUAGE": "Ewe", "TTS_VOICE": "Kore"})
    assert f.read_text().splitlines() == ["# my notes", "STT_PROVIDER=mock", "ASSEMBLYAI_API_KEY=abc123", "", "NATIVE_LANGUAGE=Ewe",
                                          "PORT=8765", "", "# Added from the settings screen", "TTS_VOICE=Kore"]
    assert sa.read_env(f)["ASSEMBLYAI_API_KEY"] == "abc123"


def test_secrets_are_masked_and_an_empty_secret_keeps_the_old_one():
    shown = sa.describe({"LLM_API_KEY": "sk-secret-value-1234", "LLM_MODEL": "m"}, {"LLM_MODEL": "other"})
    fields = {f["key"]: f for g in shown for f in g["fields"]}
    assert fields["LLM_API_KEY"]["value"] == "" and fields["LLM_API_KEY"]["hint"] == "…1234" and "sk-secret" not in str(shown)
    assert fields["LLM_MODEL"]["pending"] is True  # saved but not what is running
    assert sa.clean_updates({"LLM_API_KEY": "", "LLM_MODEL": " x "}, []) == {"LLM_MODEL": "x"}
    assert sa.clean_updates({}, ["LLM_API_KEY"]) == {"LLM_API_KEY": ""}


@pytest.mark.parametrize("values", [{"NOPE": "1"}, {"STT_PROVIDER": "bogus"}, {"COACH_MAX_PER_DAY": "-4"}, {"NATIVE_LANGUAGE": "a\nPORT=1"}])
def test_bad_input_is_refused(values):
    with pytest.raises(ValueError):
        sa.clean_updates(values, [])


def test_routes_save_to_the_file_report_the_changes_and_refuse_strangers(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LLM_API_KEY=old-key-0000\nNATIVE_LANGUAGE=French\nSTT_PROVIDER=mock\n")
    import app.main as main
    monkeypatch.setattr(main, "ROOT", tmp_path)
    s = Settings(data_dir=tmp_path / "data", stt_provider="mock", assemblyai_key="", assemblyai_models=(), max_upload_bytes=1024, host="127.0.0.1", port=0)
    with TestClient(create_app(s)) as c:
        got = c.get("/api/settings").json()
        assert "old-key-0000" not in str(got) and got["env_exists"] is True
        r = c.post("/api/settings", json={"values": {"NATIVE_LANGUAGE": "Ewe", "LLM_API_KEY": ""}})
        assert r.json() == {"changed": ["NATIVE_LANGUAGE"], "restart_needed": True}
        assert "NATIVE_LANGUAGE=Ewe" in env.read_text() and "old-key-0000" in env.read_text()
        assert c.post("/api/settings", json={"values": {"NATIVE_LANGUAGE": "Ewe"}}).json()["restart_needed"] is False  # nothing changed
        assert c.post("/api/settings", json={"values": {"STT_PROVIDER": "bogus"}}).status_code == 422
        assert c.post("/api/settings/test/stt").json()["ok"] is True  # mock provider: nothing to reach
        assert c.post("/api/settings/test/azure").json()["ok"] is False


def test_another_computer_cannot_read_change_test_or_restart(tmp_path):
    s = Settings(data_dir=tmp_path / "data", stt_provider="mock", assemblyai_key="", assemblyai_models=(), max_upload_bytes=1024, host="0.0.0.0", port=0)
    with TestClient(create_app(s), client=("203.0.113.9", 5555)) as c:
        assert c.get("/api/settings").status_code == 403
        assert c.post("/api/settings", json={"values": {}}).status_code == 403
        assert c.post("/api/settings/test/stt").status_code == 403
        assert c.post("/api/restart").status_code == 403
        assert c.get("/api/state").status_code == 200  # the rest of the app still answers


def test_a_restart_forgets_what_came_from_the_file_but_keeps_what_the_person_exported(tmp_path, monkeypatch):
    import os
    from app import config
    f = tmp_path / ".env"
    f.write_text("FROM_FILE_ONLY=1\nEXPORTED_TOO=file-value\n")
    monkeypatch.delenv("FROM_FILE_ONLY", raising=False)
    monkeypatch.setenv("EXPORTED_TOO", "shell-value")
    monkeypatch.setattr(config, "LOADED_FROM_FILE", set())
    config._load_env_file(f)
    assert os.environ["EXPORTED_TOO"] == "shell-value" and config.LOADED_FROM_FILE == {"FROM_FILE_ONLY"}
    for key in config.LOADED_FROM_FILE:
        os.environ.pop(key, None)
    assert "FROM_FILE_ONLY" not in os.environ and os.environ["EXPORTED_TOO"] == "shell-value"
    monkeypatch.delenv("FROM_FILE_ONLY", raising=False)
