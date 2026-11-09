"""The activity history: what happened to a download, and why - including after a restart."""
import json
import os
import stat

import pytest


def test_records_are_ordered_and_filterable(client):
    import eventlog
    ev = eventlog.events
    ev.clear()
    ev.record("added", "aa" * 20, "One.Show.S01E01", "tv")
    ev.record("rd_refused", "aa" * 20, "One.Show.S01E01", "infringing_file")
    ev.record("completed", "bb" * 20, "Another.Film.2023", "2048 MB")

    items = ev.tail()
    assert [e["kind"] for e in items] == ["added", "rd_refused", "completed"]
    assert [e["id"] for e in items] == sorted(e["id"] for e in items)
    assert [e["kind"] for e in ev.tail(hash="AA" * 20)] == ["added", "rd_refused"]
    assert [e["kind"] for e in ev.tail(after=items[0]["id"])] == ["rd_refused", "completed"]


def test_history_survives_a_restart(client):
    import config
    import eventlog
    eventlog.events.clear()
    eventlog.events.record("rd_removed", "cc" * 20, "Gone.Show.S02E03", "404")

    with open(os.path.join(config.CONFIG_PATH, "events.json")) as f:
        saved = json.load(f)
    assert saved["events"][-1]["kind"] == "rd_removed"

    again = eventlog.EventLog()          # a fresh process reading the same config dir
    (e,) = again.tail()
    assert e["name"] == "Gone.Show.S02E03" and e["detail"] == "404"
    assert again.seq == e["id"]          # ids keep increasing across restarts


def test_the_history_file_is_not_world_readable(client):
    import config
    import eventlog
    eventlog.events.record("added", "dd" * 20, "Private.Title", "")
    mode = stat.S_IMODE(os.stat(os.path.join(config.CONFIG_PATH, "events.json")).st_mode)
    assert mode & 0o077 == 0


def test_the_log_is_bounded(client, monkeypatch):
    import eventlog
    monkeypatch.setattr(eventlog, "MAX_EVENTS", 20)
    ev = eventlog.EventLog()
    for i in range(25):
        ev.record("added", "ee" * 20, f"T{i}", "")
    assert len(ev.items) == 20
    assert ev.tail()[-1]["name"] == "T24"


def test_a_refused_add_is_recorded(client):
    import eventlog
    import settings
    settings.store.update({"RD_API_TOKEN": "t"})
    mgr = client.main.mgr
    eventlog.events.clear()

    import rd

    def reject():
        raise rd.RDError('POST /torrents/addMagnet -> 451 {"error": "infringing_file"}', 451)

    mgr.add("ff" * 20, reject, "tv", "Refused.Release.2023")
    (e,) = eventlog.events.tail(hash="ff" * 20)
    assert e["kind"] == "rd_refused" and "infringing_file" in e["detail"]


def test_a_removal_is_recorded(client):
    import eventlog
    mgr = client.main.mgr

    class RD:
        def delete(self, rid): pass

    mgr.rd = RD()
    mgr.torrents["ab" * 20] = dict(hash="ab" * 20, rd_id="", state="completed", name="Done.Show", links=[], files=[])
    eventlog.events.clear()
    mgr.delete("ab" * 20, False)
    (e,) = eventlog.events.tail()
    assert e["kind"] == "removed" and e["name"] == "Done.Show"


def test_an_archive_only_download_is_flagged(client):
    from manager import is_packed

    assert is_packed([{"name": "Release.rar"}, {"name": "Release.r00"}]) is True
    assert is_packed([{"name": "Release.mkv"}]) is False
    assert is_packed([{"name": "Release.mkv"}, {"name": "notes.nfo"}]) is False
    assert is_packed([]) is False


def test_a_dead_token_is_visible_even_with_an_empty_queue(client):
    """The old failure mode: nothing queued, so the poll loop never touched RD and rd_error stayed empty."""
    import settings
    settings.store.update({"RD_API_TOKEN": "t"})
    mgr = client.main.mgr
    assert mgr.torrents == {}

    class RD:
        def user(self):
            import rd
            raise rd.RDError("GET /user -> 401 bad_token", 401)

    mgr.rd = RD()
    mgr.poll()
    assert "rejected the API token" in mgr.rd_error
