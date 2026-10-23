"""Le jeton d'équipe ne passe ni dans la configuration, ni dans un mauvais dossier."""
import json

from console.config import default_config, dump
from console.hub_client import HubClient, normalize_fingerprint


def test_the_token_stays_out_of_the_config(tmp_path):
    cfg = default_config()
    cfg.hub.url = "https://10.20.10.249:8443"
    cfg.hub.fingerprint = "ab" * 32
    text = json.dumps(dump(cfg))
    HubClient(tmp_path).set_token("SECRET-TOKEN-VALUE-123456")
    assert "SECRET-TOKEN-VALUE-123456" not in text
    assert "hub-token" not in text
    assert HubClient(tmp_path).token() == "SECRET-TOKEN-VALUE-123456"
    assert normalize_fingerprint("AB:CD") == "abcd"


def test_the_poste_device_id_uses_the_syncthing_2_subcommand(tmp_path, monkeypatch):
    import subprocess

    import pytest

    from console import syncbox

    exe = syncbox.binary_of(tmp_path)
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    device = "KBMT5Z4-TRKLGY2-SP6MNVQ-CUTE2A2-7ZMQQWO-FFFBB37-GGSNFDW-SSF2JQE"
    calls = []

    def answer(cmd, **kwargs):
        calls.append(cmd)
        return device + "\n"

    monkeypatch.setattr(syncbox.subprocess, "check_output", answer)
    assert syncbox.device_id(tmp_path) == device
    assert calls[0][1:] == ["--home", str(syncbox.home_of(tmp_path)), "device-id"]

    def refuse(cmd, **kwargs):
        raise subprocess.CalledProcessError(80, cmd)

    monkeypatch.setattr(syncbox.subprocess, "check_output", refuse)
    with pytest.raises(ValueError):
        syncbox.device_id(tmp_path)
