"""Le hub ne se relance que lorsqu'un push a vraiment changé la copie."""
import subprocess

from hub import refresh


def test_apply_reloads_when_the_commit_changes(tmp_path, monkeypatch):
    repo = tmp_path / "hub"
    repo.mkdir()
    (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    heads = iter(["aaa", "bbb"])
    monkeypatch.setattr(refresh, "_git", lambda *args, **kwargs: next(heads))
    monkeypatch.setattr(refresh.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0))
    assert refresh.apply(repo) is True


def test_apply_stays_when_nothing_moved(tmp_path, monkeypatch):
    repo = tmp_path / "hub"
    repo.mkdir()
    (repo / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    monkeypatch.setattr(refresh, "_git", lambda *args, **kwargs: "aaa")
    monkeypatch.setattr(refresh.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0))
    assert refresh.apply(repo) is False


def test_a_failed_pull_is_not_a_reload(tmp_path, monkeypatch):
    repo = tmp_path / "hub"
    repo.mkdir()
    monkeypatch.setattr(refresh, "_git", lambda *args, **kwargs: "aaa")

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args, "", "refusé")

    monkeypatch.setattr(refresh.subprocess, "run", fail)
    try:
        refresh.apply(repo)
    except subprocess.CalledProcessError:
        return
    raise AssertionError("le pull raté aurait dû s'arrêter")
