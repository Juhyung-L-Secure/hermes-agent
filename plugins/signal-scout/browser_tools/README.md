# Scout browser tools

One native Hermes tool, `browser_scout`, forwards proposed actions to a trusted
run-owned `BrowserActionGate`. The separate `signal_scout_browser` toolset leaves
the existing status-only bootstrap unchanged. No mission launcher is added.

| File | Owns |
| --- | --- |
| `__init__.py` | Fixed schema, runtime binding, native registration and result packaging |
| `action-reference.md` | Complete request syntax and examples, supplied at startup and after format rejection |
| `specs.json` | Routing contracts BR_001–BR_006 and test links |
| `../../../tests/signal_scout/browser_tools/test_adapter.py` | Native discovery/dispatch with contract-shaped gates and fake main transport |
| `../../../tests/signal_scout/browser_tools/test_native_integration.py` | Real gate, owned Chromium, native model/tool loop and exact host cleanup |
| `../../../tests/signal_scout/browser_tools/model_fixture.py` | In-memory fake main transport; no provider calls |
| `../security/gate.py` | Request/permission validation, current target resolution, execution and fresh feedback |
| `../../../docker/signal-scout/browser_run.py` | Trusted host container ownership and normal teardown |

## Flow and binding

Already-started owned Browser → trusted gate with initial explicit page ID → `bind_browser(gate, on_fatal=..., action_reference=...)` on the native-loaded plugin's
`browser_tools` module → explicitly selected `signal_scout_browser` → native
`browser_scout` dispatch → captured gate → text or native image observation.

Only one binding is supported. Trusted caller loads the complete nonempty action reference through that same registered module's `load_action_reference()` before provider resolution and inference. It supplies that exact immutable string as mandatory `action_reference`; the binding rejects missing, non-string or blank references. Research startup includes the full contents beside the preloaded research skill and mission. Missing, unreadable, invalid UTF-8 or empty resources stop startup. The adapter retains the original string for the run and never rereads it on rejection.

Trusted caller supplies a mandatory callable `on_fatal` that invokes its own `AIAgent.interrupt(hard_cancel=True)`. A fatal
gate result calls that callback before returning; later calls on that binding
remain fatal. Native registry catches handler exceptions, so raising or asking
the model to stop cannot replace this callback.

Scope exit first marks the binding inactive, then closes that exact gate,
then releases the binding. The enclosing host `BrowserRun` still owns removal
of the exact browser container. Delayed calls keep their captured gate and stop
callback and action reference; they never consult a newer run after dispatch. Closing a gate revokes later calls before Browser transport cleanup.

Use the module loaded by native plugin discovery: importing another namespace
creates another Python module and does not bind the registered handler. No
binding-dependent `check_fn` is registered. Tool discovery remains stable;
the handler enforces actual binding on every call. Unbound dispatch rejects
without creating/attaching a Browser or resolving a provider.
Existing native unknown-name repair can map a similar name to `browser_scout`.
It still reaches only the bound gate; raw executor arguments are invalid. No
repair exposes the original native browser or another execution handler.

## Request and feedback

Model supplies `action`, exact action-specific `args`, and optional `activity`.
Activity describes progress. The handler forwards these values unchanged; browser identity, callback
and operating policy never come from model arguments. The gate validates every
field and owns the selected page ID and fresh compact post-action snapshot.

| Action | Exact args |
| --- | --- |
| `click` | `{ref}` |
| `navigate` | `{url}` |
| `type` | `{ref,text}`; clear then insert |
| `keypress` | `{key}`; separate browser-only keys |
| `scroll` | `{direction}`; `up` or `down` |
| `switch_tab` | `{target_id}`; existing tab only |
| `back`, `snapshot`, `screenshot`, `get_images`, `list_tabs`, `close` | `{}` |

Ordinary feedback serializes gate status, reason, execution_started and data.
Success includes the applicable observation; rejection confirms no proposed
effect and permits healthy continuation. Fatal ends the enclosing run.

Request-format rejection adds `data.error = {kind: "request_format", code, field}` and the full retained reference in `data.action_reference`. Rule codes, fields and reasons come from gate-owned rules. Exact expected keys derive only from the known action's existing grammar; submitted values and unknown key names never enter diagnostic feedback. Permission, stale-reference and fatal outcomes keep their existing data. Invalid JSON intercepted by native Hermes remains outside this adapter's validation path.

Nested `data.error` also lets the native failure detector recognize format rejection before repeated-result stubbing. The ordinary wrapper still marks all tool contents as untrusted data; system prompt and tool definitions remain stable. Native next-request tests cover repeated identical malformed calls, a representative two-error batch and history without earlier guidance. Existing native warnings, guardrails and context-scaled result budgets remain active. Arbitrarily large batches can still exceed inherited budgets. The main model chooses its next proposal; no correction model, automatic rewriting, replay or new retry cap is added.
Navigation-aware click/key/navigate/back feedback includes current compact AX, selected target, actual URL, native readiness and sanitized deadline facts under `data.observation`; explicit navigate keeps `data.requested_url` separate. Matching initial-document DOMContentLoaded permits early success while media remains pending. A healthy5second observation expiration returns `incomplete` with its configured duration after effect acknowledgment, with no automatic repeat or fatal callback. Ordinary dropdowns use a bounded0.25second interaction interval; delayed future navigation remains possible. Each command retains its independent60second response deadline, and missing acknowledgment/control/inspection failure remains fatal. The main model chooses later observations or another action. Only explicit navigation loading errors retain bounded original-URL recovery.
Execution_started is not proof that a server-side effect completed.
Adapter returns raw JSON text, not a custom warning wrapper. Hermes's native
`make_tool_result_message` recognizes the actual `browser_scout` name and frames
external result text with that source, neutralizing embedded boundary tokens.
The same native path frames multimodal text while preserving image parts. No
`scout_browser` alias, alternate source label or core change is added.

Screenshot feedback alone packages in-memory PNG bytes into native `_multimodal`
text/image content. Text summary retains MIME and other public result metadata,
with bytes carried only in the image part. No automatic main screenshot,
temporary screenshot file or auxiliary vision model is
added. Normal independently observed AX field values remain, including Scout's
typed text. Request/action packets are not copied into results. Observation reaches main context; this is not a new
conversation-retention system. Native/provider image support is not proven by
offline verification.

## Verification and pairings

Schema/handler ↔ BR_001–BR_006 ↔ mirrored tests. Gate request/result/close API
must stay paired with `security/gate.py`, while host cleanup stays paired with
the existing lifecycle. Coverage rows name only tests that passed; collection
checks prove links, not assertion quality or executed integration.

Use canonical `scripts/run_tests.sh` with explicit affected files and selections, one worker and zero automatic file retries. Use the [standalone checker workflow](../README.md#validate-spec-links) for setup and spec-link validation.
Real integration uses the same serialized fixture and owned stack as security
tests. Fake-only unit checks cannot establish real-gate completion.

Native integration covers click/key effects, retained typed AX values, PNG,
external delimiter defanging, blocked/stale requests and fatal/exact host cleanup.
BR_006 also covers actual `research()` startup into the first native inference and failure before provider resolution when the reference is unavailable. Offline transports prove wiring and effects on controlled pages, not live model correction quality.
Foreground missions use the existing host launcher. No Discord, account provisioning,
outer tool-timeout recovery, selected-tab closure policy, evidence caps,
supervisor recovery or independent host-loss cleanup is introduced.
