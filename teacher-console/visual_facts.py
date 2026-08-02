import hashlib
import json
import copy

SCHEMA = "wuli.visual-facts.v1"
GATE_SCHEMA = "wuli.visual-facts-gate-result.v1"

VALID_KINDS = {"region", "object", "arrow", "label", "boundary", "geometry"}

REQUIRED_EVIDENCE_FIELDS = [
    "schema",
    "source_fingerprint",
    "reviewed_text",
    "printed_facts",
    "diagram_facts",
    "handwriting",
    "uncertainties",
    "model_identity",
]

ALLOWED_EVIDENCE_FIELDS = set(REQUIRED_EVIDENCE_FIELDS) | {"fingerprint"}


def _is_sha256_fingerprint(value):
    if not isinstance(value, str):
        return False
    if not value.startswith("sha256:"):
        return False
    hex_part = value[len("sha256:"):]
    if len(hex_part) != 64:
        return False
    return all(character in "0123456789abcdef" for character in hex_part)


def _validate_evidence_fields(raw):
    if not isinstance(raw, dict):
        raise ValueError("raw must be a dict")
    unknown = set(raw.keys()) - ALLOWED_EVIDENCE_FIELDS
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")
    missing = [f for f in REQUIRED_EVIDENCE_FIELDS if f not in raw]
    if missing:
        raise ValueError(f"missing required fields: {sorted(missing)}")


def _validate_source_fingerprint(value):
    if not _is_sha256_fingerprint(value):
        raise ValueError("source_fingerprint must be sha256:<64 lowercase hex>")


def _validate_reviewed_text(value):
    if not isinstance(value, str):
        raise ValueError("reviewed_text must be a string")


def _validate_string_list(value, field_name):
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    for item in value:
        if not isinstance(item, str) or item == "":
            raise ValueError(f"{field_name} must contain only non-empty strings")


def _validate_diagram_facts(value):
    if not isinstance(value, list):
        raise ValueError("diagram_facts must be a list")
    seen_ids = set()
    for fact in value:
        if not isinstance(fact, dict):
            raise ValueError("each diagram fact must be an object")
        if set(fact.keys()) != {"id", "kind", "statement", "confidence"}:
            raise ValueError("diagram fact must have exactly id, kind, statement, confidence")
        fact_id = fact["id"]
        if not isinstance(fact_id, str) or fact_id == "":
            raise ValueError("diagram fact id must be a non-empty string")
        if fact_id in seen_ids:
            raise ValueError(f"duplicate diagram fact id: {fact_id}")
        seen_ids.add(fact_id)
        kind = fact["kind"]
        if kind not in VALID_KINDS:
            raise ValueError(f"invalid diagram fact kind: {kind}")
        statement = fact["statement"]
        if not isinstance(statement, str) or statement == "":
            raise ValueError("diagram fact statement must be a non-empty string")
        confidence = fact["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("diagram fact confidence must be numeric 0..1")
        if not (0 <= confidence <= 1):
            raise ValueError("diagram fact confidence must be in [0, 1]")


def _validate_model_identity(value):
    if not isinstance(value, dict):
        raise ValueError("model_identity must be an object")
    if set(value.keys()) != {"model_id", "provider"}:
        raise ValueError("model_identity must have exactly model_id and provider")
    if not isinstance(value["model_id"], str) or value["model_id"] == "":
        raise ValueError("model_identity.model_id must be a non-empty string")
    if not isinstance(value["provider"], str) or value["provider"] == "":
        raise ValueError("model_identity.provider must be a non-empty string")


def _canonical_json(payload):
    # payload must be a dict with exactly the evidence fields (no fingerprint)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _compute_fingerprint(payload):
    return "sha256:" + hashlib.sha256(_canonical_json(payload)).hexdigest()


def normalize_payload(raw: dict, expected_source_fingerprint: str) -> dict:
    if not isinstance(expected_source_fingerprint, str) or not _is_sha256_fingerprint(expected_source_fingerprint):
        raise ValueError("expected_source_fingerprint must be sha256:<64 lowercase hex>")
    _validate_evidence_fields(raw)
    if raw["schema"] != SCHEMA:
        raise ValueError(f"schema must be {SCHEMA}")
    _validate_source_fingerprint(raw["source_fingerprint"])
    if raw["source_fingerprint"] != expected_source_fingerprint:
        raise ValueError("source_fingerprint mismatch")
    _validate_reviewed_text(raw["reviewed_text"])
    _validate_string_list(raw["printed_facts"], "printed_facts")
    _validate_diagram_facts(raw["diagram_facts"])
    _validate_string_list(raw["handwriting"], "handwriting")
    _validate_string_list(raw["uncertainties"], "uncertainties")
    _validate_model_identity(raw["model_identity"])

    # Deep copy all values to avoid aliasing.
    evidence = {
        "schema": copy.deepcopy(raw["schema"]),
        "source_fingerprint": copy.deepcopy(raw["source_fingerprint"]),
        "reviewed_text": copy.deepcopy(raw["reviewed_text"]),
        "printed_facts": copy.deepcopy(raw["printed_facts"]),
        "diagram_facts": copy.deepcopy(raw["diagram_facts"]),
        "handwriting": copy.deepcopy(raw["handwriting"]),
        "uncertainties": copy.deepcopy(raw["uncertainties"]),
        "model_identity": copy.deepcopy(raw["model_identity"]),
    }

    if "fingerprint" in raw:
        supplied = raw["fingerprint"]
        if not _is_sha256_fingerprint(supplied):
            raise ValueError("fingerprint must be sha256:<64 lowercase hex>")
        computed = _compute_fingerprint(evidence)
        if supplied != computed:
            raise ValueError("fingerprint mismatch")

    fingerprint = _compute_fingerprint(evidence)
    normalized = dict(evidence)
    normalized["fingerprint"] = fingerprint
    return normalized


def _validate_canonical_artifact(payload):
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")
    if set(payload.keys()) != ALLOWED_EVIDENCE_FIELDS:
        raise ValueError("canonical artifact must contain exactly evidence fields plus fingerprint")
    if "fingerprint" not in payload:
        raise ValueError("canonical artifact missing fingerprint")
    _validate_evidence_fields(payload)
    if payload["schema"] != SCHEMA:
        raise ValueError(f"schema must be {SCHEMA}")
    _validate_source_fingerprint(payload["source_fingerprint"])
    _validate_reviewed_text(payload["reviewed_text"])
    _validate_string_list(payload["printed_facts"], "printed_facts")
    _validate_diagram_facts(payload["diagram_facts"])
    _validate_string_list(payload["handwriting"], "handwriting")
    _validate_string_list(payload["uncertainties"], "uncertainties")
    _validate_model_identity(payload["model_identity"])
    if not _is_sha256_fingerprint(payload["fingerprint"]):
        raise ValueError("fingerprint must be sha256:<64 lowercase hex>")
    # Verify fingerprint matches the evidence fields.
    evidence = {k: v for k, v in payload.items() if k != "fingerprint"}
    computed = _compute_fingerprint(evidence)
    if payload["fingerprint"] != computed:
        raise ValueError("fingerprint mismatch")


def _validate_expected_fingerprint(expected_source_fingerprint):
    if not isinstance(expected_source_fingerprint, str) or not _is_sha256_fingerprint(expected_source_fingerprint):
        raise ValueError("expected_source_fingerprint must be sha256:<64 lowercase hex>")


def _validate_runtime_identity(identity):
    if not isinstance(identity, dict):
        raise ValueError("runtime identity must be a dict")
    if set(identity.keys()) != {"model_id", "provider"}:
        raise ValueError("runtime identity must have exactly model_id and provider")
    if not isinstance(identity["model_id"], str) or identity["model_id"] == "":
        raise ValueError("runtime identity model_id must be non-empty string")
    if not isinstance(identity["provider"], str) or identity["provider"] == "":
        raise ValueError("runtime identity provider must be non-empty string")


def _validate_threshold(minimum_fact_confidence):
    if isinstance(minimum_fact_confidence, bool) or not isinstance(minimum_fact_confidence, (int, float)):
        raise ValueError("minimum_fact_confidence must be numeric 0..1")
    if not (0 <= minimum_fact_confidence <= 1):
        raise ValueError("minimum_fact_confidence must be in [0, 1]")


def evaluate_gate(
    payload: dict,
    expected_source_fingerprint: str,
    runtime_model_identity: dict,
    expected_runtime_model_identity: dict,
    minimum_fact_confidence: float = 0.6,
) -> dict:
    _validate_expected_fingerprint(expected_source_fingerprint)
    _validate_runtime_identity(runtime_model_identity)
    _validate_runtime_identity(expected_runtime_model_identity)
    _validate_threshold(minimum_fact_confidence)
    _validate_canonical_artifact(payload)

    reasons = []

    if payload["source_fingerprint"] != expected_source_fingerprint:
        reasons.append({
            "code": "source-mismatch",
            "fact_id": None,
            "message": "source fingerprint does not match expected"
        })

    if payload["reviewed_text"] == "":
        reasons.append({
            "code": "empty-reviewed-text",
            "fact_id": None,
            "message": "reviewed_text is empty"
        })

    if len(payload["uncertainties"]) > 0:
        reasons.append({
            "code": "uncertainty-present",
            "fact_id": None,
            "message": "uncertainties list is non-empty"
        })

    for fact in payload["diagram_facts"]:
        if fact["confidence"] < minimum_fact_confidence:
            reasons.append({
                "code": "low-confidence",
                "fact_id": fact["id"],
                "message": f"confidence {fact['confidence']} below threshold {minimum_fact_confidence}"
            })

    if runtime_model_identity != expected_runtime_model_identity:
        reasons.append({
            "code": "route-mismatch",
            "fact_id": None,
            "message": "runtime model identity does not match expected"
        })

    reasons.sort(key=lambda r: (r["code"], r["fact_id"] or ""))

    return {
        "schema": GATE_SCHEMA,
        "visual_facts_fingerprint": payload["fingerprint"],
        "status": "passed" if len(reasons) == 0 else "needs-source-review",
        "reasons": reasons,
        "thresholds": {"minimum_fact_confidence": minimum_fact_confidence},
    }
