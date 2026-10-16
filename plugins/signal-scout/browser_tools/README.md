# Scout browser tools

One native Hermes tool, `browser_scout`, forwards proposed actions to a trusted
run-owned `BrowserActionGate`. The separate `signal_scout_browser` toolset leaves
the existing status-only bootstrap unchanged. No mission launcher is added.

| File | Owns |
| --- | --- |
| `__init__.py` | Fixed schema, runtime binding, native registration and result packaging |
| `specs.json` | Routing contracts BR_001–BR_005 and validated test links |
| `../../../tests/signal_scout/browser_tools/test_adapter.py` | Native discovery/dispatch with contract-shaped gates and fake main transport |
| `../../../tests/signal_scout/browser_tools/test_native_integration.py` | Real gate, owned Chromium, native model/tool loop and exact host cleanup |
| `../../../tests/signal_scout/browser_tools/model_fixture.py` | In-memory fake main transport; no provider calls |
| `../security/gate.py` | Request/permission validation, exact evidence/review/recheck, execution and fresh feedback |
| `../../../docker/signal-scout/browser_run.py` | Trusted host container ownership and normal teardown |

## Flow and binding

Already-started owned Browser → trusted gate with initial explicit page ID and
reviewer → `bind_browser(gate, on_fatal=...)` on the native-loaded plugin's
`browser_tools` module → explicitly selected `signal_scout_browser` → native
`browser_scout` dispatch → captured gate → text or native image observation.

Only one binding is supported. Trusted caller supplies a mandatory callable
`on_fatal` that invokes its own `AIAgent.interrupt(hard_cancel=True)`. A fatal
gate result calls that callback before returning; later calls on that binding
remain fatal. Native registry catches handler exceptions, so raising or asking
the model to stop cannot replace this callback.

Scope exit first marks the binding inactive, then closes that exact gate,
then releases the binding. The enclosing host `BrowserRun` still owns removal
of the exact browser container. Delayed calls keep their captured gate and stop
callback; they never consult a newer run after dispatch. Closing a gate revokes
waiting approvals before Browser transport cleanup.

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
Activity is mandatory when image-owned operating policy requires review. The
handler forwards these values unchanged; browser identity, reviewer, callback
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
Incomplete navigation includes fixed warning, current AX, selected target ID, and truthful `execution_started`. It permits another main-model turn to choose another URL. The gate owns bounded navigation retries; the adapter forwards this result without retry or fatal callback.
Execution_started is not proof that a server-side effect completed.
Adapter returns raw JSON text, not a custom warning wrapper. Hermes's native
`make_tool_result_message` recognizes the actual `browser_scout` name and frames
external result text with that source, neutralizing embedded boundary tokens.
The same native path frames multimodal text while preserving image parts. No
`scout_browser` alias, alternate source label or core change is added.

Screenshot feedback alone packages in-memory PNG bytes into native `_multimodal`
text/image content. Text summary retains MIME and other public result metadata,
with bytes carried only in the image part. No automatic main screenshot,
temporary screenshot file, auxiliary vision model or private review packet is
added. Normal independently observed AX field values remain, including Scout's
typed text. Request/action packets are not copied into results; reviewer receives
raw viewport PNG without masking. Observation reaches main context; this is not a new
conversation-retention system. Native/provider image support is not proven by
offline verification.

## Verification and pairings

Schema/handler ↔ BR_001–BR_005 ↔ mirrored tests. Gate request/result/close API
must stay paired with `security/gate.py`, while host cleanup stays paired with
the existing lifecycle. Coverage rows name only tests that passed; collection
checks prove links, not assertion quality or executed integration.

Use canonical `scripts/run_tests.sh` with explicit files for development, then the full Scout suite documented in the [plugin guide](../README.md). Use its [standalone checker workflow](../README.md#validate-spec-links) for setup and spec-link validation.
Real integration uses the same serialized fixture and owned stack as security
tests. Fake-only unit checks cannot establish real-gate completion.

Native integration covers click/key effects, retained typed AX values, PNG,
external delimiter defanging, deny/stale/late and fatal/exact host cleanup.
No user mission launcher, Discord, live activation, login, account provisioning,
outer tool-timeout recovery, selected-tab closure policy, evidence caps,
supervisor recovery or independent host-loss cleanup is introduced.
