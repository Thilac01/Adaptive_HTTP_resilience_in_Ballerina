"""Shared action admission for the non-idempotent payment prototype."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Literal


Action = Literal["stop", "wait", "reconcile", "retry", "failover"]
Endpoint = Literal["primary", "backup"]


class ExecutionEvidence(Enum):
    AMBIGUOUS = "ambiguous"
    CONFIRMED = "confirmed"
    NO_PRIOR_ATTEMPT_CAN_COMMIT = "no_prior_attempt_can_commit"


@dataclass(frozen=True)
class VerifiedDeduplication:
    """A verified guarantee for this request across all listed destinations."""

    request_id: str
    endpoints: frozenset[Endpoint]


def admissible_actions(
    *,
    request_id: str,
    evidence: ExecutionEvidence,
    deduplication: VerifiedDeduplication | None,
    remaining_ms: int,
    remaining_calls: int,
    duration_bounds_ms: Mapping[Action, int],
) -> tuple[Action, ...]:
    """Return eligible actions using evidence scoped to this logical request.

    Duration bounds include the time reserved for executing each action.
    Missing bounds leave the corresponding action unavailable.
    """
    if evidence is ExecutionEvidence.CONFIRMED or remaining_ms <= 0:
        return ("stop",)

    actions: list[Action] = ["stop"]
    if 0 < duration_bounds_ms.get("wait", 0) <= remaining_ms:
        actions.append("wait")
    if remaining_calls <= 0:
        return tuple(actions)

    if 0 < duration_bounds_ms.get("reconcile", 0) <= remaining_ms:
        actions.append("reconcile")
    repeat_endpoints: tuple[tuple[Action, Endpoint], ...] = (
        ("retry", "primary"),
        ("failover", "backup"),
    )
    verified_endpoints: frozenset[Endpoint] = frozenset()
    if request_id and deduplication is not None and deduplication.request_id == request_id:
        verified_endpoints = deduplication.endpoints

    for action, endpoint in repeat_endpoints:
        safe_to_repeat = (
            evidence is ExecutionEvidence.NO_PRIOR_ATTEMPT_CAN_COMMIT
            or endpoint in verified_endpoints
        )
        if safe_to_repeat and 0 < duration_bounds_ms.get(action, 0) <= remaining_ms:
            actions.append(action)
    return tuple(actions)
