from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _clock():
    return datetime(2026, 8, 17, 10, tzinfo=timezone.utc)


def _snapshot(path: Path) -> Path:
    # Deliberately non-canonical bytes: the protocol must hash these exact bytes.
    path.write_bytes(
        b'{\n  "candidate": {"identity": "candidate-1", "chain": "solana"},\n'
        b'  "smart_money": {"final_feature": {"holdings_change_1h_pct": 2.5}},\n'
        b'  "completeness": {"available_at": "2026-08-17T10:00:00Z"}\n}\n'
    )
    return path


def _pass1_value(**changes):
    value = {
        "action": "LONG",
        "confidence": 0.7,
        "expected_direction_4h": "UP",
        "evidence_for": ["smart_money.final_feature.holdings_change_1h_pct"],
        "evidence_against": [],
        "missing_evidence": ["exchange_counterflow"],
        "rationale": "The available point-in-time flow evidence is positive.",
        "risk_flags": ["INCOMPLETE_DATA"],
    }
    value.update(changes)
    return value


def _response(value, *, response_id="resp_1", model="gpt-5.6-sol", refusal=None, raw=None):
    from src.nansen_signal_lab.openai_client import OpenAIEvidenceResponse

    if raw is None:
        content = (
            [{"type": "refusal", "refusal": refusal}]
            if refusal is not None
            else [{"type": "output_text", "text": json.dumps(value)}]
        )
        raw = json.dumps({
            "id": response_id,
            "model": model,
            "status": "completed",
            "output": [{"type": "message", "content": content}],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }).encode()
    return OpenAIEvidenceResponse.from_raw(
        raw_body=raw,
        status_code=200,
        request_started_at="2026-08-17T10:00:00Z",
        response_retrieved_at="2026-08-17T10:00:01Z",
        response_headers={"x-request-id": "request-1"},
    )


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def create_structured(self, **kwargs):
        self.calls.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _writer(tmp_path):
    from src.nansen_signal_lab.gpt_protocol import GPTArtifactWriter

    return GPTArtifactWriter(tmp_path, now=_clock)


def test_pass1_archives_exact_bytes_and_binds_exact_snapshot_hash(tmp_path):
    from src.nansen_signal_lab.gpt_protocol import run_pass1

    snapshot = _snapshot(tmp_path / "snapshot.json")
    value = _pass1_value()
    noncanonical = (
        b'{ "id" : "resp_noncanonical", "model":"gpt-5.6-sol", '
        b'"status":"completed", "output":[{"type":"message","content":'
        b'[{"type":"output_text","text":'
        + json.dumps(json.dumps(value, indent=1)).encode()
        + b'}]}], "usage":{"total_tokens":15,"input_tokens":10,"output_tokens":5} }'
    )
    client = FakeClient(_response(value, response_id="resp_noncanonical", raw=noncanonical))
    result = run_pass1(client, snapshot, _writer(tmp_path))

    expected_snapshot_sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    assert result.value == value
    assert result.snapshot_sha256 == expected_snapshot_sha
    assert result.response_path.read_bytes() == noncanonical
    assert result.response_sha256 == hashlib.sha256(noncanonical).hexdigest()
    assert result.response_sha256 != hashlib.sha256(
        json.dumps(json.loads(noncanonical), sort_keys=True).encode()
    ).hexdigest()
    request = json.loads(result.request_path.read_text())
    assert request["snapshot_sha256"] == expected_snapshot_sha
    assert request["transmission_may_begin"] is True
    rendered = result.request_path.read_text().lower()
    for forbidden in ("token_address", "token_symbol", "forward_", "mfe", "mae", "previous_response_id", '"tools"'):
        assert forbidden not in rendered
    final = json.loads((tmp_path / "model/pass-1/final.json").read_text())
    assert final["attempt"] == 1
    assert final["snapshot_sha256"] == expected_snapshot_sha
    assert final["request_sha256"] == result.request_sha256
    assert final["response_sha256"] == result.response_sha256


@pytest.mark.parametrize(
    "invalid",
    [
        _pass1_value(extra="unknown"),
        _pass1_value(action="SHORT"),
        _pass1_value(confidence=1.5),
        _pass1_value(evidence_for=["missing.path"]),
        _pass1_value(evidence_for=["candidate.chain", "candidate.chain"]),
    ],
)
def test_pass1_local_validation_repairs_once_and_preserves_both_attempts(tmp_path, invalid):
    from src.nansen_signal_lab.gpt_protocol import run_pass1

    client = FakeClient(_response(invalid, response_id="bad"), _response(_pass1_value(), response_id="good"))
    result = run_pass1(client, _snapshot(tmp_path / "snapshot.json"), _writer(tmp_path))
    assert result.value == _pass1_value()
    assert len(client.calls) == 2
    assert (tmp_path / "model/pass-1/attempt-1-response.json").is_file()
    assert (tmp_path / "model/pass-1/attempt-2-response.json").is_file()
    repair = client.calls[1]["input_json"]
    assert set(repair) == {"repair", "original_input", "original_schema"}
    assert repair["repair"]["validation_errors"]
    assert json.loads((tmp_path / "model/pass-1/final.json").read_text())["attempt"] == 2


def test_pass1_second_invalid_response_is_terminal_and_never_calls_again(tmp_path):
    from src.nansen_signal_lab.gpt_protocol import GPTProtocolError, run_pass1

    client = FakeClient(
        _response(_pass1_value(action="SHORT"), response_id="bad1"),
        _response(_pass1_value(action="SHORT"), response_id="bad2"),
    )
    with pytest.raises(GPTProtocolError, match="validation"):
        run_pass1(client, _snapshot(tmp_path / "snapshot.json"), _writer(tmp_path))
    assert len(client.calls) == 2
    assert not (tmp_path / "model/pass-1/final.json").exists()


def test_malformed_nonfinite_output_is_terminal_without_repair(tmp_path):
    from src.nansen_signal_lab.gpt_protocol import GPTProtocolError, run_pass1

    invalid = _pass1_value(confidence=float("nan"))
    client = FakeClient(_response(invalid, response_id="nonfinite"))
    with pytest.raises(GPTProtocolError, match="malformed"):
        run_pass1(client, _snapshot(tmp_path / "snapshot.json"), _writer(tmp_path))
    assert len(client.calls) == 1
    assert not (tmp_path / "model/pass-1/attempt-2-request.json").exists()


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (
            b'{"id":"wrong-model","model":"gpt-5.6-terra","status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"{}"}]}],"usage":{}}',
            "model mismatch",
        ),
        (
            b'{"id":"absent","model":"gpt-5.6-sol","status":"completed","output":[],"usage":{}}',
            "absent",
        ),
        (
            b'{"id":"malformed","model":"gpt-5.6-sol","status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"{"}]}],"usage":{}}',
            "malformed",
        ),
        (
            b'{"id":"incomplete","model":"gpt-5.6-sol","status":"incomplete","output":[{"type":"message","content":[{"type":"output_text","text":"{}"}]}],"usage":{}}',
            "not completed",
        ),
    ],
)
def test_terminal_provider_outputs_are_archived_without_repair(tmp_path, raw, message):
    from src.nansen_signal_lab.gpt_protocol import GPTProtocolError, run_pass1

    client = FakeClient(_response({}, raw=raw))
    with pytest.raises(GPTProtocolError, match=message):
        run_pass1(client, _snapshot(tmp_path / "snapshot.json"), _writer(tmp_path))
    assert len(client.calls) == 1
    assert (tmp_path / "model/pass-1/attempt-1-response.json").read_bytes() == raw
    assert not (tmp_path / "model/pass-1/attempt-2-request.json").exists()


def test_http_failure_and_timeout_are_request_first_and_never_repaired(tmp_path):
    from src.nansen_signal_lab.openai_client import OpenAIEvidenceResponse, OpenAIError
    from src.nansen_signal_lab.gpt_protocol import GPTProtocolError, run_pass1

    failure = OpenAIEvidenceResponse.from_raw(
        raw_body=b'{"error":{"message":"unavailable"}}',
        status_code=503,
        request_started_at="2026-08-17T10:00:00Z",
        response_retrieved_at="2026-08-17T10:00:01Z",
        response_headers={},
    )
    with pytest.raises(GPTProtocolError, match="HTTP 503"):
        run_pass1(
            FakeClient(OpenAIError("OpenAI HTTP 503", transmitted=True, response=failure)),
            _snapshot(tmp_path / "snapshot.json"),
            _writer(tmp_path),
        )
    assert (tmp_path / "model/pass-1/attempt-1-request.json").is_file()
    assert (tmp_path / "model/pass-1/attempt-1-response.json").read_bytes() == failure.raw_body
    assert not (tmp_path / "model/pass-1/attempt-2-request.json").exists()

    other = tmp_path / "timeout"
    other.mkdir()
    snapshot = _snapshot(other / "snapshot.json")
    timeout = OpenAIError("read timeout", transmitted=True)
    with pytest.raises(GPTProtocolError, match="after transmission"):
        run_pass1(FakeClient(timeout), snapshot, _writer(other))
    assert (other / "model/pass-1/attempt-1-request.json").is_file()
    assert not (other / "model/pass-1/attempt-1-response.json").exists()
    no_calls = FakeClient()
    with pytest.raises(GPTProtocolError, match="ambiguous"):
        run_pass1(no_calls, snapshot, _writer(other))
    assert no_calls.calls == []


def test_refusal_and_ambiguous_request_never_repair_or_reroll(tmp_path):
    from src.nansen_signal_lab.gpt_protocol import GPTArtifactWriter, GPTProtocolError, run_pass1

    snapshot = _snapshot(tmp_path / "snapshot.json")
    client = FakeClient(_response({}, refusal="cannot"))
    with pytest.raises(GPTProtocolError, match="refusal"):
        run_pass1(client, snapshot, _writer(tmp_path))
    assert len(client.calls) == 1
    assert (tmp_path / "model/pass-1/attempt-1-response.json").exists()
    assert not (tmp_path / "model/pass-1/attempt-2-request.json").exists()

    other = tmp_path / "ambiguous"
    other.mkdir()
    snapshot2 = _snapshot(other / "snapshot.json")
    writer = GPTArtifactWriter(other, now=_clock)
    request_dir = other / "model/pass-1"
    request_dir.mkdir(parents=True)
    (request_dir / "attempt-1-request.json").write_text("{}\n")
    no_calls = FakeClient(_response(_pass1_value()))
    with pytest.raises(GPTProtocolError, match="ambiguous"):
        run_pass1(no_calls, snapshot2, writer)
    assert no_calls.calls == []


def test_crash_after_response_before_final_adopts_without_reroll(tmp_path, monkeypatch):
    from src.nansen_signal_lab.gpt_protocol import (
        GPTArtifactWriter,
        run_pass1,
    )

    snapshot = _snapshot(tmp_path / "snapshot.json")
    first_client = FakeClient(_response(_pass1_value(), response_id="sealed"))
    first_writer = GPTArtifactWriter(tmp_path, now=_clock)
    def crash_before_final(scope, value):
        raise OSError("injected after response install")

    monkeypatch.setattr(first_writer, "install_final", crash_before_final)
    with pytest.raises(OSError, match="injected"):
        run_pass1(first_client, snapshot, first_writer)
    assert len(first_client.calls) == 1
    assert (tmp_path / "model/pass-1/attempt-1-response.json").is_file()
    assert not (tmp_path / "model/pass-1/final.json").exists()

    later = lambda: datetime(2026, 8, 17, 11, tzinfo=timezone.utc)
    no_reroll = FakeClient()
    result = run_pass1(no_reroll, snapshot, GPTArtifactWriter(tmp_path, now=later))
    assert result.response_id == "sealed"
    assert no_reroll.calls == []
    assert (tmp_path / "model/pass-1/final.json").is_file()


def _theories():
    return tuple({"id": f"theory-{index}", "role": "entry", "all": []} for index in range(6))


def _pass2_value(snapshot_sha, response_sha, theories=None, **changes):
    theories = _theories() if theories is None else theories
    value = {
        "snapshot_sha256": snapshot_sha,
        "pass1": {"response_sha256": response_sha},
        "pass1_assessment": "UPHOLD",
        "final_action": "LONG",
        "theory_assessments": [{
            "theory_id": theory["id"],
            "applicability": "APPLICABLE",
            "predicate_alignment": "SUPPORTS_LONG",
            "rationale": "The frozen predicate is directionally aligned.",
        } for theory in theories],
        "conflicts": [],
        "evidence_for": ["smart_money.final_feature.holdings_change_1h_pct"],
        "evidence_against": [],
        "missing_evidence": [],
        "rationale": "The frozen records do not overturn Pass 1.",
    }
    value.update(changes)
    return value


def test_pass2_receives_literal_pass1_response_hash_and_same_snapshot(tmp_path):
    from src.nansen_signal_lab.gpt_protocol import run_pass1, run_pass2

    snapshot = _snapshot(tmp_path / "snapshot.json")
    client1 = FakeClient(_response(_pass1_value(), response_id="p1"))
    pass1 = run_pass1(client1, snapshot, _writer(tmp_path))
    expected = _pass2_value(pass1.snapshot_sha256, pass1.response_sha256)
    client2 = FakeClient(_response(expected, response_id="p2"))
    result = run_pass2(client2, snapshot, pass1, _theories(), _writer(tmp_path))
    assert result.value == expected
    sent = client2.calls[0]["input_json"]
    assert sent["snapshot_sha256"] == pass1.snapshot_sha256
    assert sent["pass1"]["response_sha256"] == pass1.response_sha256
    assert sent["pass1"]["value"] == pass1.value
    assert len(sent["theory_records"]) == 6


def test_pass2_rejects_snapshot_or_pass1_hash_mutation_and_record_coverage(tmp_path):
    from src.nansen_signal_lab.gpt_protocol import GPTProtocolError, run_pass1, run_pass2

    snapshot = _snapshot(tmp_path / "snapshot.json")
    pass1 = run_pass1(FakeClient(_response(_pass1_value())), snapshot, _writer(tmp_path))
    snapshot.write_bytes(snapshot.read_bytes() + b" ")
    client = FakeClient(_response({}))
    with pytest.raises(GPTProtocolError, match="snapshot"):
        run_pass2(client, snapshot, pass1, _theories(), _writer(tmp_path))
    assert client.calls == []

    snapshot.write_bytes(snapshot.read_bytes()[:-1])
    wrong = _pass2_value(pass1.snapshot_sha256, "0" * 64)
    with pytest.raises(GPTProtocolError, match="validation"):
        run_pass2(FakeClient(_response(wrong), _response(wrong)), snapshot, pass1, _theories(), _writer(tmp_path))


def test_pass2_rejects_mutated_pass1_value_before_model_call(tmp_path):
    from src.nansen_signal_lab.gpt_protocol import GPTProtocolError, run_pass1, run_pass2

    snapshot = _snapshot(tmp_path / "snapshot.json")
    pass1 = run_pass1(FakeClient(_response(_pass1_value())), snapshot, _writer(tmp_path))
    mutated = replace(pass1, value=_pass1_value(action="ABSTAIN"))
    no_calls = FakeClient()
    with pytest.raises(GPTProtocolError, match="Pass 1 value"):
        run_pass2(no_calls, snapshot, mutated, _theories(), _writer(tmp_path))
    assert no_calls.calls == []


def test_pass2_rejects_prior_feasibility_results_before_model_call(tmp_path):
    from src.nansen_signal_lab.gpt_protocol import GPTProtocolError, run_pass1, run_pass2

    snapshot = _snapshot(tmp_path / "snapshot.json")
    pass1 = run_pass1(FakeClient(_response(_pass1_value())), snapshot, _writer(tmp_path))
    records = list(_theories())
    records[0] = {**records[0], "historical_return": 0.42}
    no_calls = FakeClient()
    with pytest.raises(GPTProtocolError, match="prior feasibility"):
        run_pass2(no_calls, snapshot, pass1, tuple(records), _writer(tmp_path))
    assert no_calls.calls == []


def test_model_preflight_is_archived_request_first_and_adopted(tmp_path):
    from src.nansen_signal_lab.gpt_protocol import GPTArtifactWriter, archive_model_preflight

    class PreflightClient:
        def __init__(self):
            self.calls = 0

        def preflight_model(self, model_id):
            self.calls += 1
            return _response({}, response_id="gpt-5.6-sol", raw=b'{"id":"gpt-5.6-sol"}')

    client = PreflightClient()
    writer = GPTArtifactWriter(tmp_path, now=_clock)
    first = archive_model_preflight(client, writer)
    assert client.calls == 1
    request = tmp_path / "model/preflight/attempt-1-request.json"
    response = tmp_path / "model/preflight/attempt-1-response.json"
    assert request.is_file() and response.read_bytes() == b'{"id":"gpt-5.6-sol"}'
    assert json.loads(request.read_text())["transmission_may_begin"] is True
    second = archive_model_preflight(client, writer)
    assert client.calls == 1
    assert second.raw_body == first.raw_body


def test_model_preflight_request_without_response_is_ambiguous(tmp_path):
    from src.nansen_signal_lab.gpt_protocol import (
        GPTArtifactWriter,
        GPTProtocolError,
        archive_model_preflight,
    )

    writer = GPTArtifactWriter(tmp_path, now=_clock)
    writer.install_request("preflight", 1, {
        "schema_version": 1,
        "scope": "preflight",
        "attempt": 1,
        "method": "GET",
        "path": "/v1/models/gpt-5.6-sol",
        "requested_model_id": "gpt-5.6-sol",
        "transmission_may_begin": True,
        "request_started_at": "2026-08-17T10:00:00Z",
    })
    client = type("NeverCall", (), {"preflight_model": lambda *args: pytest.fail("rerolled")})()
    with pytest.raises(GPTProtocolError, match="ambiguous"):
        archive_model_preflight(client, writer)
