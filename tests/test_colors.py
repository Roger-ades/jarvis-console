"""An account's color or name changed later applies to its old discussions too."""
from .test_search import done


def test_old_discussions_take_the_current_color(engine):
    t = engine.create_task("bonjour", profile="work")
    done(engine, t["id"])
    old = engine.tasks[t["id"]]["color"]
    cfg = engine.cfg.model_copy(deep=True)
    prof = cfg.profile("work")
    prof.color, prof.name = "#12ab34", "Bureau"
    engine.cfg_store.save(cfg, "nouvelle couleur")
    row = next(x for x in engine.list_tasks() if x["id"] == t["id"])
    assert old != "#12ab34" and row["color"] == "#12ab34" and row["profile_name"] == "Bureau"
    assert engine.public(engine.tasks[t["id"]])["color"] == "#12ab34"
    hit = engine.search("bonjour")["tasks"][0]
    assert hit["color"] == "#12ab34" and hit["profile_name"] == "Bureau"
