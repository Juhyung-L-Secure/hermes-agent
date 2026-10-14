# Runtime status

Operator can query installed Scout plugin version and declared readiness without starting a model conversation. Readiness distinguishes working status from unimplemented research surfaces.

## Sub-features

- `status-cli` returns plugin identity, manifest version, and readiness from the Docker operator command.
- `status-native` exposes and dispatches the native `signal_scout` status tool without exposing extra model tools.
- `status-isolation` runs with the configured restricted process/filesystem identity and retains auth/log volumes.

## How to get to it (user POV)

- Run `docker compose -f docker/signal-scout/compose.yaml run --rm --no-deps -T scout status`.
- In the native Hermes status-only surface, invoke `signal_scout` with `{"action":"status"}`. Scripted verification uses the existing container integration test, not a live model conversation.

## Driving it with verify-scout

Preconditions:

- Built Scout runtime image and successful doctor; no existing Scout/browser run.
- No credentials or seeded research data required.

- **Operator entry.** Run `.venv/bin/python .agents/skills/verify-scout/scripts/verify.py status`. Receipt must record exit zero and actual JSON with `plugin: signal-scout`, a nonempty version, `status: ready`, and both research fields `not_implemented`. Result is not a research-readiness claim.
- **Native tool and isolation entry.** Run `HERMES_TEST_FILE_TIMEOUT=600 HERMES_TEST_WORKERS=1 scripts/run_tests.sh tests/signal_scout/integration/test_container.py --include-integration -q -k test_BT_002_container_identity_mounts_resources_and_native_status --file-retries 0`. Require selected test passes; it observes container identity/mounts/resources and actual status dispatch. Other tests remain deselected.
- **Proof.** Retain helper receipt/stdout/stderr plus before/after inventory. For test entry, retain runner output and exit code. No new Scout/browser container may remain; original named volumes must still exist.

## Gotchas

- `scout chat` is a different, live status-only conversation; it is not needed to prove deterministic status and is not selected here.
- `--include-integration` is required for this test directory. A zero-test run is not success.
- Native test checks more than helper status; passing one does not prove the other entry's isolation assertions.
