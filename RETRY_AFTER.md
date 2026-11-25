# Retry-After (experimental v0.2.0)

Fixed mode honors integer delta-seconds by waiting at least the server delay and the existing fixed backoff. The delay plus retry timeout and reserve must fit the remaining budget. Learned mode retains the frozen candidate action space: if its recommendation waits less than the server minimum, recovery is stopped rather than modifying the learned action. HTTP-date, malformed, duplicate and excessive values conservatively suppress retries. This is partial Retry-After support, not full RFC compliance. Mutation retries remain disabled. No hard deadline guarantee; prepare the sequential client before business operations.

Run `bal test` in aegis_next, repack and push locally, rebuild next_demo, then run `python3 tests/integration_retry_after.py` and `python3 tests/integration_next.py` from the project root. Cold test remains a separate known limitation.
