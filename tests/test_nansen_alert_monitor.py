from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/nansen_alert_monitor.py"
SPEC = importlib.util.spec_from_file_location("nansen_alert_monitor", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
alerts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(alerts)


def _manifest(tmp_path: Path) -> Path:
    root = tmp_path / "program"
    root.mkdir()
    path = root / "program.json"
    path.write_text(
        json.dumps({"program_id": alerts.PROGRAM_ID}), encoding="utf-8"
    )
    return path


def _systemctl_ok(*args: str):
    class Result:
        returncode = 0
        stdout = ""

    result = Result()
    if args[0] == "is-enabled":
        result.stdout = "enabled\n"
    elif args[0] == "is-active":
        result.stdout = "active\n"
    elif "--property=Result" in args:
        result.stdout = "success\n"
    elif "--property=InvocationID" in args:
        result.stdout = "abc123\n"
    return result


def test_milestones_are_queued_once_and_delivered_once(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)
    root = manifest.parent
    (root / "derived").mkdir()
    (root / "derived/discovery-family.json").write_text(
        '{"stage":"validation_family_frozen"}', encoding="utf-8"
    )
    (root / "derived/final-result.json").write_text(
        '{"stage":"validated_rule"}', encoding="utf-8"
    )
    state = tmp_path / "state"
    monkeypatch.setattr(alerts, "_systemctl", _systemctl_ok)

    alerts.detect_events(manifest, state)
    alerts.detect_events(manifest, state)
    events = sorted((state / "events").glob("*.json"))
    assert len(events) == 2
    seen = []
    assert alerts.deliver_pending(state, notifier=lambda event: seen.append(event) or True) == 2
    assert alerts.deliver_pending(state, notifier=lambda event: seen.append(event) or True) == 0
    assert {event["title"] for event in seen} == {
        "Nansen preliminary results are ready",
        "Nansen validated results are ready",
    }


def test_fatal_and_timer_failure_are_critical(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)
    root = manifest.parent
    (root / "intents").mkdir()
    (root / "intents/program-fatal.json").write_text(
        '{"reason":"provider balance discontinuity"}', encoding="utf-8"
    )
    state = tmp_path / "state"

    def unhealthy(*args: str):
        result = _systemctl_ok(*args)
        if args[0] in {"is-enabled", "is-active"}:
            result.stdout = "disabled\n" if args[0] == "is-enabled" else "inactive\n"
            result.returncode = 1
        return result

    monkeypatch.setattr(alerts, "_systemctl", unhealthy)
    alerts.detect_events(manifest, state)
    observed = []
    alerts.deliver_pending(state, notifier=lambda event: observed.append(event) or True)
    assert len(observed) == 2
    assert all(event["urgency"] == "critical" for event in observed)
    assert any("stopped safely" in event["title"] for event in observed)
    assert any("needs attention" in event["title"] for event in observed)


def test_failed_delivery_remains_pending(tmp_path):
    state = tmp_path / "state"
    alerts.queue_event(
        state,
        event_id="failure-one",
        title="Failure",
        body="Needs review",
        urgency="critical",
    )
    assert alerts.deliver_pending(state, notifier=lambda _event: False) == 0
    delivered = state / "delivered"
    assert not delivered.exists() or not list(delivered.glob("*.json"))
    assert alerts.deliver_pending(state, notifier=lambda _event: True) == 1


def test_alert_files_do_not_enter_frozen_runtime_globs():
    schema = (ROOT / "programs/nansen_rapid_research_v1/schema.py").read_text(
        encoding="utf-8"
    )
    assert 'scripts.glob("*rapid_research*.py")' in schema
    assert 'units.glob("nansen-signal-lab-rapid-research*")' in schema
    assert 'tests.glob("test_rapid_research*.py")' in schema
    assert "rapid_research" not in SCRIPT.name
    assert not (ROOT / "operations/nansen-signal-lab-research-alerts.service").name.startswith(
        "nansen-signal-lab-rapid-research"
    )
    assert not Path(__file__).name.startswith("test_rapid_research")
