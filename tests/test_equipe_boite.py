"""L'équipe dans la boîte de réception : invitations, Fil, mentions, partages, retraits."""
from console.equipe_boite import TeamFeed, _mentions


class Store:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


ROGER = {"id": "roger", "name": "Roger Martin"}


class Hub:
    def __init__(self):
        self.projects = [
            {"id": "offre", "name": "Offres", "color": "#123456", "added_by": "paul", "joined": 10, "member_count": 3},
            {"id": "etude", "name": "Études", "added_by": "roger", "joined": 5},
        ]
        self.messages = {"etude": [{"id": 1, "author": "paul", "text": "ancien", "ts": 100}]}
        self.conversations = {"etude": []}

    def __call__(self, method, path):
        if path == "/api/v1/projects":
            return {"projects": self.projects}
        if path == "/api/v1/users":
            return {"users": [{"id": "paul", "name": "Paul Durand"}, {"id": "roger", "name": "Roger Martin"}]}
        team, what = path.split("/")[-2:]
        return {what: getattr(self, what)[team]}


def _feed():
    changes = []
    feed = TeamFeed(Store(), lambda: changes.append(1))
    return feed, Hub(), changes


LINKED = {"etude": {"folder": "C:/Etudes", "name": "Études SSI", "color": "#abcdef"}}


def test_an_invitation_names_who_added_me_until_it_is_ignored():
    feed, hub, changes = _feed()
    feed.poll(hub, ROGER, LINKED)
    invite, = feed.entries()
    assert invite["kind"] == "team_invite" and invite["section"] == "todo" and invite["team_id"] == "offre"
    assert invite["summary"].startswith("Paul Durand t'a ajouté") and invite["members"] == 3
    assert feed.pending_invitations() == 1 and changes
    assert feed.mark([invite], dismiss=False) == 0          # "Lu" ne range pas une invitation
    assert feed.mark([invite], dismiss=True) == 1
    assert feed.entries() == [] and feed.pending_invitations() == 0
    feed.poll(hub, ROGER, LINKED)
    assert feed.entries() == []                             # elle ne revient pas


def test_history_is_not_news_but_what_comes_after_is():
    feed, hub, _ = _feed()
    hub.projects = hub.projects[1:]
    feed.poll(hub, ROGER, LINKED)
    assert feed.entries() == []
    hub.messages["etude"] += [{"id": 2, "author": "roger", "text": "moi", "ts": 150},
                              {"id": 3, "author": "paul", "text": "Bonjour", "ts": 200},
                              {"id": 4, "author": "paul", "text": "effacé", "ts": 210, "deleted": 211}]
    hub.conversations["etude"] = [{"id": "c1", "author": "paul", "title": "Point", "rev": 2, "updated": 300},
                                  {"id": "c2", "author": "roger", "title": "Moi", "rev": 1, "updated": 400}]
    feed.poll(hub, ROGER, LINKED)
    fil, partage = feed.entries()
    assert fil["kind"] == "team_message" and fil["section"] == "read" and fil["count"] == 1
    assert fil["title"] == "Études SSI" and fil["folder"] == "C:/Etudes" and fil["upto"] == 200
    assert fil["excerpt"] == "Paul Durand : Bonjour" and fil["authors"] == ["Paul Durand"]
    assert partage["kind"] == "team_share" and partage["upto"] == 300
    assert partage["excerpt"] == "Paul Durand : « Point » (révision 2)"
    assert feed.entries(light=True)[0]["excerpt"] == ""
    assert feed.mark([fil], dismiss=False) == 1
    assert [e["kind"] for e in feed.entries()] == ["team_share"]


def test_a_mention_goes_to_do():
    feed, hub, _ = _feed()
    hub.projects = hub.projects[1:]
    feed.poll(hub, ROGER, LINKED)
    hub.messages["etude"].append({"id": 2, "author": "paul", "text": "@roger tu relis ?", "ts": 200})
    feed.poll(hub, ROGER, LINKED)
    entry, = feed.entries()
    assert entry["kind"] == "team_mention" and entry["section"] == "todo"


def test_mentions_name_the_account_or_the_person():
    assert _mentions("@roger tu relis ?", ROGER)
    assert _mentions("Vu avec @Roger Martin hier", ROGER)
    assert _mentions("(@roger)", ROGER)
    assert not _mentions("@rogerio", ROGER)
    assert not _mentions("roger@ades.fr", ROGER)
    assert not _mentions("@paul", ROGER)


def test_reading_the_page_marks_only_what_it_showed():
    feed, hub, changes = _feed()
    hub.projects = hub.projects[1:]
    feed.poll(hub, ROGER, LINKED)
    hub.messages["etude"] += [{"id": 2, "author": "paul", "text": "a", "ts": 200},
                              {"id": 3, "author": "paul", "text": "b", "ts": 300}]
    feed.poll(hub, ROGER, LINKED)
    feed.read_team("etude", msg=200)
    entry, = feed.entries()
    assert entry["count"] == 1 and entry["upto"] == 300
    feed.read_team("etude", msg=150)                        # une page en retard ne recule pas la marque
    assert feed.seen["etude"]["msg"] == 200
    feed.read_team("etude")
    assert feed.entries() == []


def test_a_lost_project_offers_to_detach_its_folder():
    feed, hub, _ = _feed()
    feed.poll(hub, ROGER, LINKED)
    hub.projects = [p for p in hub.projects if p["id"] != "etude"]
    feed.poll(hub, ROGER, LINKED)
    gone = next(e for e in feed.entries() if e["kind"] == "team_removed")
    assert gone["folder"] == "C:/Etudes" and gone["section"] == "todo" and gone["title"] == "Études SSI"
    feed.mark([gone], dismiss=True)
    assert all(e["kind"] != "team_removed" for e in feed.entries())


def test_the_marks_survive_a_restart():
    feed, hub, _ = _feed()
    feed.poll(hub, ROGER, LINKED)
    feed.mark(feed.entries(), dismiss=True)
    again = TeamFeed(feed.store)
    again.poll(hub, ROGER, LINKED)
    assert again.entries() == [] and again.seen["etude"]["msg"] == 100


def test_an_unreachable_hub_keeps_the_last_state():
    feed, hub, _ = _feed()
    feed.poll(hub, ROGER, LINKED)

    def down(method, path):
        raise OSError("hub injoignable")

    try:
        feed.poll(down, ROGER, LINKED)
    except OSError:
        pass
    assert [e["kind"] for e in feed.entries()] == ["team_invite"]


def test_the_administrator_sees_who_registered_since_the_first_look():
    feed, hub, changes = _feed()
    admin = {**ROGER, "admin": True}
    registered = [{"id": 2, "ts": 50, "user": "paul", "kind": "user.register", "detail": {"name": "Paul Durand"}}]

    def call(method, path):
        if path == "/api/v1/audit":
            return {"entries": [*registered, {"id": 1, "ts": 1, "user": "roger", "kind": "user.add", "detail": {}}]}
        return hub(method, path)

    feed.poll(call, admin, LINKED)
    assert [e for e in feed.entries() if e["kind"] == "team_signup"] == []     # déjà là avant : pas une nouvelle
    registered.insert(0, {"id": 3, "ts": 60, "user": "marie", "kind": "user.register", "detail": {"name": "Marie"}})
    hub_users = hub.__call__

    def with_marie(method, path):
        if path == "/api/v1/users":
            return {"users": [*hub_users(method, path)["users"], {"id": "marie", "name": "Marie Leroy"}]}
        return call(method, path)

    feed.poll(with_marie, admin, LINKED)
    signup, = [e for e in feed.entries() if e["kind"] == "team_signup"]
    assert signup["user"] == "marie" and signup["title"] == "Marie Leroy" and "revoke" in signup["actions"]
    assert feed.mark([signup], dismiss=False) == 1
    assert [e for e in feed.entries() if e["kind"] == "team_signup"] == []
    again = TeamFeed(feed.store)
    again.poll(with_marie, admin, LINKED)
    assert [e for e in again.entries() if e["kind"] == "team_signup"] == []    # la marque survit au redémarrage
    feed.poll(with_marie, ROGER, LINKED)                                          # plus administrateur : plus rien
    assert feed.signups == []
