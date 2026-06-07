from pathlib import Path
from ansible_query.inventory.bulk import BulkBuffer


def test_stage_file_and_inspect():
    buf = BulkBuffer()
    p = Path("/inv/hostvars/node1/node1.yaml")
    buf.stage_file(p, {"env": "prod"}, affected_hosts=["node1"])

    assert p in buf.pending_files
    assert buf.pending_files[p] == {"env": "prod"}
    assert buf.affected_hosts == frozenset({"node1"})


def test_stage_file_overwrites():
    buf = BulkBuffer()
    p = Path("/inv/hostvars/node1/node1.yaml")
    buf.stage_file(p, {"env": "staging"})
    buf.stage_file(p, {"env": "production"})
    assert buf.pending_files[p] == {"env": "production"}


def test_stage_dir_removal():
    buf = BulkBuffer()
    d = Path("/inv/hostvars/node1")
    buf.stage_dir_removal(d)
    assert d in buf.pending_dir_removals


def test_affected_hosts_accumulate():
    buf = BulkBuffer()
    buf.stage_file(Path("/a"), {}, affected_hosts=["node1", "node2"])
    buf.stage_file(Path("/b"), {}, affected_hosts=["node2", "node3"])
    assert buf.affected_hosts == frozenset({"node1", "node2", "node3"})


def test_is_empty():
    buf = BulkBuffer()
    assert buf.is_empty()
    buf.stage_file(Path("/a"), {})
    assert not buf.is_empty()


def test_clear():
    buf = BulkBuffer()
    buf.stage_file(Path("/a"), {"x": 1}, affected_hosts=["node1"])
    buf.stage_dir_removal(Path("/b"))
    buf.clear()

    assert buf.is_empty()
    assert buf.pending_files == {}
    assert buf.pending_dir_removals == []
    assert buf.affected_hosts == frozenset()


def test_pending_files_returns_copy():
    buf = BulkBuffer()
    p = Path("/a")
    buf.stage_file(p, {"x": 1})
    snapshot = buf.pending_files
    buf.clear()
    assert p in snapshot  # snapshot is not affected by clear
