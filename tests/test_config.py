import json

import pytest
from pydantic import ValidationError

from console.config import Config, ConfigStore, default_config, dump


def test_defaults_are_valid_and_complete():
    cfg = default_config()
    assert [p.id for p in cfg.profiles] == ["work", "personal"]
    assert {p.id for p in cfg.presets} == {"lecture", "web", "brouillons", "edition", "assiste", "complet"}
    complet = cfg.preset("complet")
    assert not complet.enabled and complet.require_confirm and complet.require_dedicated_workdir
    Config.model_validate(dump(cfg))


@pytest.mark.parametrize("mutate", [
    lambda c: c["profiles"][0].update(color="bleu"),
    lambda c: c["profiles"][0].update(id="Work Pro"),
    lambda c: c["profiles"].append(dict(c["profiles"][0])),
    lambda c: c["general"].update(default_profile="inconnu"),
    lambda c: c["profiles"][0].update(default_preset="inexistant"),
    lambda c: c["presets"][0]["allow"].append("Bash(a,b)"),
    lambda c: c["presets"][0].update(mode="yolo"),
    lambda c: c["general"].update(max_concurrent=0),
    lambda c: c["constraints"].append({"tool": "x", "path": "y"}),
    lambda c: c["profiles"][0]["mcp"]["extra_servers"].update({"bad name": {"command": "x"}}),
    lambda c: c["profiles"][0]["env"].update({"1BAD": "x"}),
])
def test_invalid_configs_are_rejected(mutate):
    data = dump(default_config())
    mutate(data)
    with pytest.raises(ValidationError):
        Config.model_validate(data)


def test_save_keeps_history_and_rollback(tmp_path):
    store = ConfigStore(tmp_path)
    cfg = store.config
    assert cfg.general.max_concurrent == 3
    cfg2 = Config.model_validate({**dump(cfg), "general": {**dump(cfg)["general"], "max_concurrent": 5}})
    store.save(cfg2, "cinq tâches")
    assert ConfigStore(tmp_path).config.general.max_concurrent == 5
    versions = store.history()
    # the archived version is the one that was replaced, labelled with what replaced it
    assert versions[0]["reason"] == "cinq taches"
    store.rollback(versions[0]["id"])
    assert ConfigStore(tmp_path).config.general.max_concurrent == 3


def test_import_validates_and_keeps_current_on_error(tmp_path):
    store = ConfigStore(tmp_path)
    before = store.config.general.max_concurrent
    bad = dump(store.config)
    bad["general"]["max_concurrent"] = 999
    with pytest.raises(ValidationError):
        store.import_json(bad)
    assert ConfigStore(tmp_path).config.general.max_concurrent == before


def test_corrupted_file_falls_back(tmp_path):
    store = ConfigStore(tmp_path)
    store.config  # writes defaults
    (tmp_path / "config.json").write_text("{ pas du json", encoding="utf-8")
    cfg = ConfigStore(tmp_path).load()
    assert cfg.profiles
    assert list(tmp_path.glob("config.invalid-*.json"))


def test_schema_is_exportable():
    schema = ConfigStore.schema()
    assert "properties" in schema and "profiles" in schema["properties"]
    json.dumps(schema)
