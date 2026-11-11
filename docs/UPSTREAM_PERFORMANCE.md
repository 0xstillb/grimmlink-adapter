# Upstream request performance

The Adapter still verifies each request's identity against Grimmory. Verified
identity is reused only within that request, never as a cross-request auth cache.

## Connections and authentication

The application lifespan owns HTTP transport pools, with a default limit of 20
connections per server/TLS configuration and 30 seconds of keepalive. Each client
keeps its own credentials, headers and cookie jar. Closing a short-lived client
does not close the shared sockets; shutdown closes the transports. Standalone
clients outside the application lifespan retain their own transport ownership.
Explicitly injected HTTP clients retain caller ownership.

Valid Reader session and linked-account profile checks run without the refresh
lock. Refresh falls back to a lock keyed by normalized server and user ID, then
reloads stored credentials before rotating tokens. Metadata workers use the same
account key, so they cannot rotate the same linked refresh chain concurrently.
Locks are removed when the last holder/waiter leaves, including cancellation.
Metadata's credential read/verify/write remains under its account lock.

## Time budgets

`OFFICIAL_CLIENT_TOTAL_TIMEOUT` defaults to 25 seconds. Incoming HTTP requests
share a monotonic upstream deadline across auth, service calls, retry backoff,
refresh and account-lock waits. Background/standalone upstream calls use this
limit per operation. No new upstream operation starts after the deadline.

This bounds upstream waiting, not all local processing or an entire background
job. `OFFICIAL_CLIENT_TIMEOUT` still controls HTTP idle/read timeouts. Streamed
downloads are bounded while waiting for headers; transferring the file body is
not cut off by the total budget. Increase the total budget if intentional large
synchronous API batches need more time, keeping the Reader's own timeout in mind.

Only GET/HEAD/OPTIONS retain automatic transport retries. Mutations are not
automatically replayed. A timeout after a write is not proof that Grimmory rejected
it; the existing outbox/reconciliation logic remains responsible for recovery.

## Bookmark reads

Concurrent bookmark and annotation pulls may share one upstream bookmark GET
only when server, TLS configuration, book ID and bearer-token digest match.
Each caller receives a deep copy. There is no TTL or completed-result cache;
subsequent pulls fetch fresh data. API mutation attempts invalidate in-flight
sharing before and after the upstream request, even on failure. An existing read
may finish with its old snapshot, but a new post-write read cannot join it.
Changes made directly in Grimmory become visible on the next fresh pull.

Cancelling a follower does not cancel the leader. If the leader is cancelled,
remaining readers start a replacement read subject to their own remaining budget.

## Diagnostics

Request summaries include total, auth, auth-lock wait, upstream and SQLite
connection-scope time in milliseconds. At `LOG_LEVEL=INFO`, only requests taking
at least `SLOW_REQUEST_MS` (default 1000) emit these summaries; DEBUG includes all.
Metrics use route templates and omit credentials, query strings, book identifiers
and response bodies. Timings can overlap, so they must not be added together.
Streamed request total time includes the download body.

Regression tests in `tests/unit/test_upstream_performance.py` cover concurrency,
account isolation, refresh, cancellation, deadlines, mutation invalidation,
transport ownership and diagnostic redaction. Mock/local tests do not establish
latency improvements or end-to-end Reader sync on the Pi. After deployment,
compare slow-route timing summaries and test two readers syncing concurrently,
plus PDF/EPUB bookmark create/edit/delete and a large streamed download.
