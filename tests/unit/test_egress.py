from cutlery import egress
from cutlery.db import Database


def test_domains_from_addresses_and_bare_names():
    text = ("Map from https://tile.opentopomap.org/{z}/{x}/{y}.png, music on www.jamendo.com, see news.bbc.co.uk; "
            "not briefing.md, edit.json, cut1.mp4, a@b.de, 192.168.0.1, http://10.0.0.1/x, http://localhost:8080")
    assert egress.domains(text) == {"opentopomap.org", "jamendo.com", "bbc.co.uk"}
    assert egress.rules({"opentopomap.org"}) == ["opentopomap.org", "*.opentopomap.org"]
    assert egress.registrable("co.uk") is None and egress.registrable("x.co.uk") == "x.co.uk"


def test_recorded_per_project_and_for_all(cfg):
    db = Database(cfg.database)
    egress.record(db, "p1", "briefing", "https://opentopomap.org", replace=True)
    egress.record(db, "p1", "note", "example.org")
    egress.record(db, egress.ALL, "preferences", "freesound.org", replace=True)
    assert egress.for_project(db, "p1") == ["example.org", "freesound.org", "opentopomap.org"]
    assert egress.for_project(db, "p2") == ["freesound.org"]
    egress.record(db, "p1", "briefing", "nothing", replace=True)            # the briefing changed
    assert egress.for_project(db, "p1") == ["example.org", "freesound.org"]
