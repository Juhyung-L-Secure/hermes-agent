# Scout browser tools and action contracts

Native Hermes tool `browser_scout` forwards proposals to trusted run-owned `BrowserSession`. Separate `signal_scout_browser` toolset leaves bootstrap status-only. This owner holds BR routing contracts and ordinary AG/AE action/inspection contracts in [specs.json](specs.json). [Browser runtime mechanics](../scout_runtime/browser/README.md) explain implementation. [Security contracts](../security/README.md) retain confinement and network guarantees.

| File | Owns |
| --- | --- |
| `__init__.py` | Fixed schema, runtime binding, native registration, and result packaging |
| `action-reference.md` | Complete request syntax and examples, supplied at startup and after format rejection |
| `specs.json` | BR_001 through BR_006, AG_001 through AG_009, AE_001, AE_002, and AE_004 through AE_007 |
| `../scout_runtime/actions.py` | Ordered action vocabulary shared by schema, settings, and parser |
| `../scout_runtime/browser/session.py` | Complete action dispatch, selected page, original refs, private CDP, observations, and terminal closure |
| `../../../tests/signal_scout/browser_tools/` | Native discovery, dispatch, main-model loop, framing, exact effects, and hard stop |
| `../../../tests/signal_scout/security/` | Action/inspection unit and real-browser checks, alongside retained security checks |
| `../../../tests/signal_scout/support/` | Reusable external model transports, browser fixtures, stack, diagnostics, and mission support |
| `../../../docker/signal-scout/browser_run.py` | Trusted host container ownership and exact normal teardown |

## Native binding

Trusted owner starts isolated `Browser`, obtains `browser.session(target_id=...)`, and calls `bind_browser(session, on_fatal=..., action_reference=...)` on native-loaded plugin's `browser_tools` module. Explicitly selected `signal_scout_browser` then dispatches unchanged proposal to captured session. Unbound calls cannot attach or create browser, choose provider, or execute an effect.

One binding is supported. Trusted caller loads complete nonempty action reference through same registered module's `load_action_reference()` before provider resolution and inference. Mandatory `action_reference` must be nonblank string. Startup includes full contents beside preloaded research skill and mission. Missing, unreadable, invalid UTF-8, or empty resource stops startup. Adapter retains exact immutable string for run and never rereads it after rejection.

Mandatory callable `on_fatal` invokes enclosing `AIAgent.interrupt(hard_cancel=True)`. Fatal dispatch calls callback before return. Later calls on same binding stay fatal. Native registry catches handler exceptions, so throwing or asking model to stop cannot replace callback.

Scope exit marks binding inactive, closes exact session, then releases binding. Session terminal state revokes later calls before browser transport cleanup. Enclosing host `BrowserRun` owns exact container removal. Delayed calls retain captured session, stop callback, and action reference. They never consult newer run.

Binding must use module loaded by native plugin discovery. Importing another namespace creates another Python module and cannot bind registered handler. No binding-dependent `check_fn` changes tool discovery. Handler checks binding per call. Existing native unknown-name repair may map similar name to `browser_scout`; raw executor arguments still reject and no other executor becomes exposed.

Only `browser_scout` is selected for foreground research. Native browser, terminal, code, file, MCP, messaging, and delegation tools remain absent. Discovery schema and prompt prefixes remain stable through conversation.

## Requests and outcomes

Model supplies `action`, exact action-specific `args`, and optional progress `activity`. [Packaged action reference](action-reference.md) owns complete syntax and examples. Browser identity, permissions, stop callback, and executor come only from trusted caller. Request parser validates closed grammar and sanitized format rules. Session owns selected page and current observations.

`browser.action_permissions` accepts `allowed` or `blocked`. Canonical configured actions start allowed. Missing entries deny. Unknown actions, extra fields, malformed refs, keys, or URLs reject without proposed effect. `switch_tab` accepts exact existing tab ID. Popups never auto-select. Scroll distance comes from `browser.scroll_pixels`.

`@s` references bind exact original tab/frame/document/loader/node. Click/type check current original node, visibility, iframe owners, projected hit point, and applicable editability/focus. Keypress retains actual focus through cross-origin frames and closed author shadow roots. Page-level key invents no element. No semantic reacquisition, replacement target, action ticket, approval baseline, or staged prepare/commit API exists.

Known unrelated iframe loss returns explicit partial-inspection notice while available branches stay usable. Missing required original target or focus rejects before execution. Required inspection or control loss after execution begins stays fatal. Unknown protocol and requested capture failures remain fatal. Session checks owned control and same private CDP connection before effects. Fatal closes session and Browser, then adapter hard-interrupts native agent. Host removes exact owned container and preserves helper state and volumes.

Results retain `status`, sanitized `reason`, `execution_started`, and `data`. Success includes applicable current observation. Rejection permits healthy continuation and no proposed effect. Fatal ends enclosing run. Started means dispatch entered execution, not proof of completed server-side effect. Click, navigate, type, keypress, back, scroll, and switch return fresh compact AX and selected target. Ordinary observed AX values can include typed text. Proposal packets are not copied into results.

Request-format rejection adds `data.error = {kind: "request_format", code, field}` and full retained reference in `data.action_reference`. Exact expected keys derive from known action grammar. Submitted values and unknown key names never enter diagnostic feedback. Permission, stale-reference, and fatal outcomes keep existing data. Invalid JSON intercepted by native Hermes remains outside adapter validation.

Nested `data.error` lets native failure detector recognize format rejection before repeated-result stubbing. Native next-request tests cover repeated identical malformed calls, representative two-error batch, and history without earlier guidance. Existing native warnings, guardrails, and context-scaled result budgets remain active. Large batches can exceed inherited budgets. Main model chooses next proposal. No correction model, automatic rewriting, replay, or new retry cap is added.

## Observations and native feedback

Navigation-aware click, keypress, navigate, and back return compact AX, selected target, actual URL, native readiness, and sanitized deadline facts under `data.observation`. Explicit navigate retains separate `data.requested_url`. Matching initial-document DOMContentLoaded permits early success while media remains pending. Healthy configured observation expiry returns `incomplete` without replay or fatal callback. Ordinary interactions use shorter configured interval. Future delayed navigation remains possible. Each command has independent response deadline. Only explicit-navigation loading errors retain bounded original-URL recovery. [Runtime observation mechanics](../scout_runtime/browser/README.md#observations-and-deadlines) describe matching and retry checks.

Adapter returns raw JSON text. Native `make_tool_result_message` recognizes actual `browser_scout` name, marks external text untrusted, and neutralizes embedded boundary tokens. Same native path frames multimodal text while retaining image parts. No alias, alternate source label, custom warning wrapper, or core change exists.

Explicit screenshot alone packages in-memory full-viewport PNG into native `_multimodal` text/image content. Text summary retains MIME and public metadata, with bytes only in image part. No automatic screenshot, temporary screenshot file, or auxiliary vision model appears. Observation reaches main conversation, without new retention system. Offline verification does not prove native/provider image support.

## Verification and remaining scope

Schema/handler pair with BR_001 through BR_006. Request, effects, observations, and closure pair with AG/AE records in same owner. [Security specs](../security/specs.json) retain SR guarantees. [Browser action recipe](../../../.agents/skills/verify-scout/features/browser-actions.md) selects maintained checks through canonical `scripts/run_tests.sh`, one worker, and zero file retries. [Standalone checker workflow](../README.md#validate-spec-links) checks whole documents independently of execution and assertion review.

Real native integration covers click/key effects, retained typed AX values, explicit PNG, delimiter defanging, blocked/stale continuation, partial frames, fatal hard stop, and exact host cleanup. BR_006 covers actual research startup to first inference and reference failure before provider resolution. External model doubles prove controlled wiring and effects, not live model correction quality or research reliability.

AG_009 retains reviewer-free execution. Former implementation remains in [Git history](../history.md#retired-reviewer). Main-agent safety guidance does not independently classify website effects. No Discord, account provisioning, outer tool-timeout recovery, selected-tab closure policy, evidence cap, supervisor recovery, or independent host-loss cleanup is introduced.
