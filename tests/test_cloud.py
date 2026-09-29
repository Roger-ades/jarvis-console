import json

import pytest

from console import cloud
from console.engine import TaskError


def test_list_goes_through_the_relay_and_is_filtered(engine):
    res = engine.cloud_routines("work", refresh=True)
    r = res["routines"][0]
    assert r["name"] == "Rapport hebdo" and r["enabled"] and r["device"] == "LAPTOP"
    assert r["folders"] == ["C:\\Tarifs"] and r["model"] == "claude-sonnet-5"
    blob = json.dumps(res)
    assert "SECRET_HINT" not in blob and "SECRET_JOB" not in blob
    # served from the cache afterwards
    assert engine.cloud_routines("work")["fetched"] == res["fetched"]


def test_actions(engine):
    assert engine.cloud_action("work", "trig_1", "run") == {"ok": True}
    assert engine.cloud_action("work", "trig_1", "toggle", enabled=False) == {"ok": True}
    runs = engine.cloud_action("work", "trig_1", "runs")["runs"]
    assert runs[0]["title"] == "Rapport du lundi" and runs[0]["status"] == "succeeded"
    with pytest.raises(TaskError):
        engine.cloud_action("work", "../x", "run")
    with pytest.raises(TaskError):
        engine.cloud_action("work", "trig_1", "delete")


def test_relay_refuses_other_actions():
    with pytest.raises(cloud.CloudError):
        cloud.relay(["x"], {}, ".", {"action": "create", "body": {}})


def test_http_parsing_and_cron_labels():
    assert cloud.parse_http('HTTP 200\n{"data": []}') == (200, {"data": []})
    assert cloud.parse_http("HTTP 404\nnot found")[0] == 404
    assert cloud.cron_label("0 8 * * 1").startswith("chaque lundi à ")
    assert cloud.cron_label("30 7 * * 1-5").startswith("en semaine à ")
    assert cloud.cron_label("15 * * * *") == "toutes les heures à :15"
    assert "cron" in cloud.cron_label("*/5 8-18 * * *")
