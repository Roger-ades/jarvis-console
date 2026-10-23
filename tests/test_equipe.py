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
