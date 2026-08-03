#!/usr/bin/env python3
"""Validate a complex-process-decomposition.v1 JSON artifact."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ALLOWED_STATUSES = {"completed", "provisional", "unsupported", "blocked"}
ALLOWED_PROCESS_KINDS = {
    "temporal",
    "stateful",
    "logical",
    "workflow",
    "hybrid",
    "none",
}
REQUIRED_TOP_LEVEL = {
    "schema",
    "status",
    "problem_profile",
    "targets",
    "process_model",
    "atomic_tasks",
    "state_transfers",
    "verification_obligations",
    "execution_plan",
    "control_policy",
    "aggregation",
    "unresolved",
}


def _list(value: Any, field: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{field} must be an array")
        return []
    return value


def _object(value: Any, field: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return {}
    return value


def _ids(items: list[Any], field: str, errors: list[str]) -> set[str]:
    result: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{field}[{index}] must be an object")
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            errors.append(f"{field}[{index}].id must be a non-empty string")
            continue
        if item_id in result:
            errors.append(f"{field} contains duplicate id {item_id!r}")
        result.add(item_id)
    return result


def _string_ids(
    value: Any,
    field: str,
    allowed: set[str],
    errors: list[str],
) -> set[str]:
    raw = _list(value, field, errors)
    result: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item:
            errors.append(f"{field}[{index}] must be a non-empty string")
            continue
        if item in result:
            errors.append(f"{field} contains duplicate id {item!r}")
        result.add(item)
        if item not in allowed:
            errors.append(f"{field} references unknown id {item!r}")
    return result


def _check_task_dag(
    tasks: list[Any],
    task_ids: set[str],
    errors: list[str],
) -> dict[str, set[str]]:
    dependencies: dict[str, set[str]] = {}
    for index, item in enumerate(tasks):
        if not isinstance(item, dict):
            continue
        task_id = item.get("id")
        if not isinstance(task_id, str) or task_id not in task_ids:
            continue
        verb = item.get("verb")
        if (
            not isinstance(verb, str)
            or not verb
            or verb.lower() != verb
            or any(character.isspace() for character in verb)
        ):
            errors.append(
                f"atomic_tasks[{index}].verb must be one lowercase action word"
            )
        maximum = item.get("max_attempts")
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
            errors.append(f"atomic_tasks[{index}].max_attempts must be a positive integer")
        dependencies[task_id] = _string_ids(
            item.get("depends_on"),
            f"atomic_tasks[{index}].depends_on",
            task_ids,
            errors,
        )
        if task_id in dependencies[task_id]:
            errors.append(f"task {task_id!r} cannot depend on itself")

    state: dict[str, int] = {}

    def visit(task_id: str, trail: list[str]) -> None:
        marker = state.get(task_id, 0)
        if marker == 1:
            cycle = " -> ".join([*trail, task_id])
            errors.append(f"atomic task dependency graph contains a cycle: {cycle}")
            return
        if marker == 2:
            return
        state[task_id] = 1
        for dependency in sorted(dependencies.get(task_id, set())):
            visit(dependency, [*trail, task_id])
        state[task_id] = 2

    for task_id in sorted(task_ids):
        visit(task_id, [])
    return dependencies


def _depends_transitively(
    task_id: str,
    dependency_id: str,
    dependencies: dict[str, set[str]],
) -> bool:
    frontier = list(dependencies.get(task_id, set()))
    visited: set[str] = set()
    while frontier:
        current = frontier.pop()
        if current == dependency_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        frontier.extend(dependencies.get(current, set()))
    return False


def validate(payload: Any) -> list[str]:
    errors: list[str] = []
    root = _object(payload, "root", errors)
    if not root:
        return errors

    missing = REQUIRED_TOP_LEVEL - set(root)
    extra = set(root) - REQUIRED_TOP_LEVEL
    if missing:
        errors.append(f"missing top-level fields: {sorted(missing)}")
    if extra:
        errors.append(f"unknown top-level fields: {sorted(extra)}")
    if root.get("schema") != "complex-process-decomposition.v1":
        errors.append("schema must equal 'complex-process-decomposition.v1'")
    status = root.get("status")
    if status not in ALLOWED_STATUSES:
        errors.append(f"status must be one of {sorted(ALLOWED_STATUSES)}")

    _object(root.get("problem_profile"), "problem_profile", errors)
    targets = _list(root.get("targets"), "targets", errors)
    tasks = _list(root.get("atomic_tasks"), "atomic_tasks", errors)
    transfers = _list(root.get("state_transfers"), "state_transfers", errors)
    obligations = _list(
        root.get("verification_obligations"),
        "verification_obligations",
        errors,
    )
    _list(root.get("unresolved"), "unresolved", errors)
    process = _object(root.get("process_model"), "process_model", errors)
    execution = _object(root.get("execution_plan"), "execution_plan", errors)
    control = _object(root.get("control_policy"), "control_policy", errors)
    aggregation = _object(root.get("aggregation"), "aggregation", errors)

    if process.get("kind") not in ALLOWED_PROCESS_KINDS:
        errors.append(f"process_model.kind must be one of {sorted(ALLOWED_PROCESS_KINDS)}")
    process_nodes = _list(process.get("nodes"), "process_model.nodes", errors)
    process_node_ids = _ids(process_nodes, "process_model.nodes", errors)
    transitions = _list(
        process.get("transitions"),
        "process_model.transitions",
        errors,
    )
    for index, transition in enumerate(transitions):
        if not isinstance(transition, dict):
            errors.append(f"process_model.transitions[{index}] must be an object")
            continue
        for endpoint in ("from", "to"):
            value = transition.get(endpoint)
            if value not in process_node_ids:
                errors.append(
                    f"process_model.transitions[{index}].{endpoint} "
                    f"references unknown process node {value!r}"
                )
        rollback_to = transition.get("rollback_to")
        if rollback_to is not None and rollback_to not in process_node_ids:
            errors.append(
                f"process_model.transitions[{index}].rollback_to "
                f"references unknown process node {rollback_to!r}"
            )

    target_ids = _ids(targets, "targets", errors)
    task_ids = _ids(tasks, "atomic_tasks", errors)
    obligation_ids = _ids(
        obligations,
        "verification_obligations",
        errors,
    )
    _ids(transfers, "state_transfers", errors)

    if status in {"completed", "provisional"} and not target_ids:
        errors.append(f"{status} decomposition must contain targets")
    if status in {"completed", "provisional"} and not task_ids:
        errors.append(f"{status} decomposition must contain atomic_tasks")

    covered_targets: set[str] = set()
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue
        covered_targets.update(
            _string_ids(
                task.get("target_ids"),
                f"atomic_tasks[{index}].target_ids",
                target_ids,
                errors,
            )
        )
        _string_ids(
            task.get("process_node_ids"),
            f"atomic_tasks[{index}].process_node_ids",
            process_node_ids,
            errors,
        )
    task_dependencies = _check_task_dag(tasks, task_ids, errors)
    if status in {"completed", "provisional"}:
        for target_id in sorted(target_ids - covered_targets):
            errors.append(f"target {target_id!r} is not covered by an atomic task")

    obligation_targets: set[str] = set()
    for index, obligation in enumerate(obligations):
        if not isinstance(obligation, dict):
            continue
        obligation_targets.update(
            _string_ids(
                obligation.get("target_ids"),
                f"verification_obligations[{index}].target_ids",
                target_ids,
                errors,
            )
        )
        _string_ids(
            obligation.get("task_ids"),
            f"verification_obligations[{index}].task_ids",
            task_ids,
            errors,
        )
    if status in {"completed", "provisional"}:
        for target_id in sorted(target_ids - obligation_targets):
            errors.append(
                f"target {target_id!r} has no verification obligation"
            )

    transfer_pairs: set[tuple[str, str]] = set()
    for index, transfer in enumerate(transfers):
        if not isinstance(transfer, dict):
            continue
        producer = transfer.get("producer_task_id")
        consumer = transfer.get("consumer_task_id")
        for field in ("producer_task_id", "consumer_task_id"):
            value = transfer.get(field)
            if value not in task_ids:
                errors.append(
                    f"state_transfers[{index}].{field} references "
                    f"unknown task {value!r}"
                )
        if (
            producer in task_ids
            and consumer in task_ids
            and not _depends_transitively(consumer, producer, task_dependencies)
        ):
            errors.append(
                f"state_transfers[{index}] producer {producer!r} is not a "
                f"dependency of consumer {consumer!r}"
            )
        if producer in task_ids and consumer in task_ids:
            transfer_pairs.add((producer, consumer))
    for consumer, dependencies in task_dependencies.items():
        for producer in dependencies:
            if (producer, consumer) not in transfer_pairs:
                errors.append(
                    f"task dependency {producer!r} -> {consumer!r} has no "
                    "state-transfer contract"
                )

    waves = _list(execution.get("waves"), "execution_plan.waves", errors)
    scheduled: list[str] = []
    wave_by_task: dict[str, int] = {}
    for index, wave in enumerate(waves):
        if not isinstance(wave, dict):
            errors.append(f"execution_plan.waves[{index}] must be an object")
            continue
        wave_tasks = _string_ids(
            wave.get("task_ids"),
            f"execution_plan.waves[{index}].task_ids",
            task_ids,
            errors,
        )
        scheduled.extend(wave_tasks)
        for task_id in wave_tasks:
            if task_id in wave_by_task:
                errors.append(f"task {task_id!r} appears in more than one execution wave")
            else:
                wave_by_task[task_id] = index
    if len(scheduled) != len(set(scheduled)):
        errors.append("a task appears in more than one execution wave")
    if status in {"completed", "provisional"} and set(scheduled) != task_ids:
        errors.append("execution waves must schedule every atomic task exactly once")
    for task_id, dependencies in task_dependencies.items():
        for dependency_id in dependencies:
            if (
                task_id in wave_by_task
                and dependency_id in wave_by_task
                and wave_by_task[dependency_id] >= wave_by_task[task_id]
            ):
                errors.append(
                    f"task {task_id!r} is not scheduled after dependency "
                    f"{dependency_id!r}"
                )

    for field in ("max_global_rounds", "max_attempts_per_task", "no_progress_limit"):
        value = control.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            errors.append(f"control_policy.{field} must be a positive integer")
    terminal_states = set(
        _list(control.get("terminal_states"), "control_policy.terminal_states", errors)
    )
    if not ALLOWED_STATUSES.issubset(terminal_states):
        errors.append(
            "control_policy.terminal_states must include completed, provisional, "
            "unsupported, and blocked"
        )

    required_targets = _string_ids(
        aggregation.get("required_target_ids"),
        "aggregation.required_target_ids",
        target_ids,
        errors,
    )
    required_obligations = _string_ids(
        aggregation.get("required_obligation_ids"),
        "aggregation.required_obligation_ids",
        obligation_ids,
        errors,
    )
    if status in {"completed", "provisional"} and required_targets != target_ids:
        errors.append("aggregation.required_target_ids must contain every target")
    if status in {"completed", "provisional"} and not required_obligations:
        errors.append("aggregation must require at least one verification obligation")
    critical_obligations = {
        item.get("id")
        for item in obligations
        if isinstance(item, dict) and item.get("risk") == "critical"
    }
    missing_critical = critical_obligations - required_obligations
    if missing_critical:
        errors.append(
            "aggregation omits critical verification obligations: "
            f"{sorted(missing_critical)}"
        )

    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_decomposition.py <decomposition.json>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    errors = validate(payload)
    if errors:
        print("INVALID")
        for error in errors:
            print(f"- {error}")
        return 1
    print("VALID complex-process-decomposition.v1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
