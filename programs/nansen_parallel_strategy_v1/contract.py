from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .design import (
    B2DesignError,
    PARENT_CANDIDATE_CONTRACT_SHA256,
    PARENT_PROGRAM_ID,
    PARENT_SOURCE_COMMIT,
    parallel_strategy_contract,
)


SOURCE_RELATIVE_PATH = (
    Path("research/experiments")
    / PARENT_PROGRAM_ID
    / "contracts/candidates.json"
)


def load_parallel_strategy_contract(repo_root: Path) -> dict[str, Any]:
    """Load only the pre-live candidate-definition artifact.

    The function intentionally has no path to Program-A/A2 manifests, rankings,
    panels, features, outcomes, or reports.
    """

    root = repo_root.resolve()
    path = root / SOURCE_RELATIVE_PATH
    if path.is_symlink() or not path.is_file() or path.resolve().parent != path.parent.resolve():
        raise B2DesignError("pre-live candidate contract is not a regular confined file")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PARENT_CANDIDATE_CONTRACT_SHA256:
        raise B2DesignError("pre-live candidate contract SHA-256 differs")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise B2DesignError("pre-live candidate contract is malformed JSON") from exc
    if not isinstance(value, dict):
        raise B2DesignError("pre-live candidate contract is not an object")
    contract = parallel_strategy_contract(value)
    return {
        **contract,
        "source": {
            "repository_relative_path": SOURCE_RELATIVE_PATH.as_posix(),
            "source_commit": PARENT_SOURCE_COMMIT,
            "sha256": digest,
            "scope": "candidate definitions only",
        },
    }
