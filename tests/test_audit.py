from __future__ import annotations

import json

from draino.audit import AuditLogger


def test_audit_logger_writes_jsonl_entry(tmp_path):
    log_path = tmp_path / "audit.log"
    logger = AuditLogger(path=log_path)

    logger.log("evacuation", "node-1", "started")
    logger.log("reboot", "node-1", "completed", "downtime=42s")

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    second = json.loads(lines[1])

    assert first["action"] == "evacuation"
    assert first["node"] == "node-1"
    assert first["event"] == "started"
    assert "detail" not in first
    assert second["detail"] == "downtime=42s"
    assert first["session_id"] == second["session_id"]
