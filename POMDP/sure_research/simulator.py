"""Deterministic timelines for the initial payment attempt."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ClientView:
    """Information available to the recovery policy after the first attempt."""

    observation: str
    remaining_ms: int


@dataclass(frozen=True)
class PaymentTimeline:
    """Ground-truth event times in milliseconds since the request was sent."""

    start_ms: int | None
    commit_ms: int | None
    reply_ms: int | None

    def state_at(self, now_ms: int) -> str:
        if self.commit_ms is not None and now_ms >= self.commit_ms:
            return "completed"
        if self.start_ms is not None and now_ms >= self.start_ms:
            return "processing"
        return "unstarted"

    def observe_initial(self, timeout_ms: int, deadline_ms: int) -> ClientView:
        cutoff_ms = min(timeout_ms, deadline_ms)
        if self.reply_ms is not None and self.reply_ms <= cutoff_ms:
            return ClientView("confirmed", deadline_ms - self.reply_ms)
        return ClientView("timeout", deadline_ms - cutoff_ms)


SCENARIOS = {
    "request_dropped": PaymentTimeline(None, None, None),
    "slow_commit": PaymentTimeline(50, 550, 570),
    "response_lost": PaymentTimeline(50, 250, None),
    "normal": PaymentTimeline(50, 250, 270),
}
