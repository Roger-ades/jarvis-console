"""Claude accounts: two folders, or two plans on one address."""
import json
from types import SimpleNamespace

from console.claude_cli import describe_login, profile_accounts, read_local_account


def _profile(pid, name, folder):
    return SimpleNamespace(id=pid, name=name, config_dir=str(folder))


def _write(folder, email, plan, org="", token="SECRET-TOKEN"):
    folder.mkdir(parents=True)
    oauth = {"emailAddress": email, "subscriptionType": plan}
    if org:
        oauth["organizationName"] = org
    (folder / ".claude.json").write_text(json.dumps({"oauthAccount": oauth}), encoding="utf-8")
    (folder / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {"accessToken": token, "refreshToken": token, "subscriptionType": plan},
    }), encoding="utf-8")


def test_two_folders_keep_two_email_addresses(tmp_path):
    personal = tmp_path / "claude"
    work = tmp_path / "claude-work"
    _write(personal, "perso@example.com", "pro")
    _write(work, "travail@example.com", "team", org="Atelier")
    found = profile_accounts([
        _profile("work", "Travail", work),
        _profile("personal", "Perso", personal),
    ])
    assert found["personal"]["account"]["label"] == "perso@example.com · forfait Pro"
    assert found["work"]["account"]["label"] == "travail@example.com · forfait Team · Atelier"
    assert found["personal"]["shared_with"] == [] and found["work"]["also"] == []
    assert "SECRET-TOKEN" not in json.dumps(found)


def test_same_address_shows_each_plans_own_folder(tmp_path):
    pro = tmp_path / "pro"
    team = tmp_path / "team"
    _write(pro, "ada@example.com", "pro", org="Ada")
    _write(team, "ada@example.com", "team", org="Équipe")
    found = profile_accounts([
        _profile("personal", "Perso", pro),
        _profile("work", "Travail", team),
    ])
    assert found["personal"]["account"]["plan"] == "pro"
    assert found["work"]["account"]["plan"] == "team"
    assert found["personal"]["also"] == [{"id": "work", "name": "Travail", "plan": "team", "plan_label": "Team"}]
    assert found["work"]["also"][0]["plan_label"] == "Pro"


def test_one_folder_shared_by_two_profiles_has_one_active_plan(tmp_path):
    folder = tmp_path / "claude"
    _write(folder, "ada@example.com", "team", org="Équipe")
    found = profile_accounts([
        _profile("a", "Pro", folder),
        _profile("b", "Team", folder),
    ])
    assert found["a"]["account"]["label"] == found["b"]["account"]["label"]
    assert found["a"]["account"]["plan"] == "team"
    assert found["a"]["shared_with"] == ["Team"]
    assert found["b"]["shared_with"] == ["Pro"]


def test_a_plan_without_an_email_is_still_logged_in(tmp_path):
    folder = tmp_path / "pro"
    folder.mkdir()
    (folder / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {"accessToken": "SECRET-TOKEN", "subscriptionType": "pro"},
    }), encoding="utf-8")
    ident = read_local_account(str(folder))
    assert ident["logged_in"] is True
    assert ident["email"] == "" and ident["label"] == "forfait Pro"
    assert "SECRET-TOKEN" not in json.dumps(ident)


def test_logout_is_not_overruled_by_a_saved_plan(tmp_path):
    folder = tmp_path / "claude"
    _write(folder, "ada@example.com", "pro")
    account, ident = describe_login({"tokenSource": "none", "email": "ada@example.com"}, str(folder))
    assert ident["logged_in"] is False and ident["label"] == ""
    assert "SECRET-TOKEN" not in json.dumps({"account": account, "identity": ident})


def test_the_cli_plan_wins_and_a_different_saved_address_is_ignored(tmp_path):
    folder = tmp_path / "claude"
    _write(folder, "ancien@example.com", "pro")
    account, ident = describe_login(
        {"tokenSource": "claude.ai", "email": "ada@example.com", "subscriptionType": "max",
         "organizationName": "Studio", "accessToken": "SECRET-TOKEN"},
        str(folder))
    assert ident["label"] == "ada@example.com · forfait Max · Studio"
    assert account["email"] == "ada@example.com"
    assert "SECRET-TOKEN" not in json.dumps({"account": account, "identity": ident})
    assert "ancien@example.com" not in ident["label"]
