# Browser action format guidance

Historical document. Reviewer-specific requirements and approval-context checks were superseded by reviewer retirement on 2026-10-29. Current behavior lives in owning `specs.json` and READMEs. Original committed reviewer remains in `archive/scout-safety-reviewer-2026-10-29`. Independent protection and observation requirements remain current where repeated in those contracts.

Planning brief from the 2026-10-28 saute-me discussion. Base commit `ace826f919d50002bc4c3b8cc9854af572b99a35`. This brief and BR_006 record desired behavior; implementation and new tests are not started.

## Outcome

Give Scout one complete browser-action reference at mission startup and again after a malformed request, together with a useful validation explanation. The next ordinary Hermes model call can propose a corrected action while retaining its mission context. Existing validation and safety review remain mandatory.

The recent short rice mission reached Google and rejected three click proposals before review. Its retained console does not reveal the submitted arguments or exact failing predicate. This work addresses missing guidance and feedback; it does not claim that compaction caused those proposals or guarantee a live model will self-correct.

## Confirmed behavior

- Use one Markdown reference covering all supported actions and their required arguments. Include full contents in the initial research context and after every request-format rejection.
- Keep existing conversation history. A format rejection causes no browser effect and no safety review. A later proposal passes the complete normal gate.
- Code loads and supplies guidance; the main model produces the corrected proposal through its normal next turn. No separate correction model or blind action replay.
- Preserve exact request arguments, permissions, safety denials, stale-context handling, review deadlines, browser/download/network protections and fatal teardown behavior.

## Engineering choices for the implementation slice

Use `plugins/signal-scout/browser_tools/action-reference.md` as the proposed canonical reference location. This is an ordinary packaged Markdown resource, not another model tool or an automatically discovered skill. The implementation agent may adjust the local name if existing packaging conventions warrant it.

Load and validate complete contents once before first research inference, alongside existing research-skill preload. Retain that trusted immutable copy for later corrective responses. Missing, unreadable or empty guidance fails before inference, following the existing mandatory startup-guidance pattern. Do not reread a mutable file or rebuild system/tool definitions mid-mission.

Describe exact outer request fields, all supported action argument sets, string types, `@s<number>` references, observed tab IDs, activity requirements and useful valid examples. Explain that reference examples are illustrative and actual refs/IDs must come from current observations. Include type replacement and key/chord semantics where needed to form a valid request. Keep examples and supported-action coverage checked against the real validator; do not freeze incidental enum counts or duplicate a new validation framework.

This first slice targets Scout's action-format validation, which produced the recent `Invalid browser request.` result. Syntactically invalid JSON rejected earlier by Hermes retains its native parser feedback; the startup reference still exists in ordinary context. Do not widen Hermes core merely to handle that separate upstream path.

Distinguish malformed requests from otherwise valid actions rejected by permission, reviewer or browser context. Return a machine-identifiable format classification and a specific static explanation of the failing field/rule. Do not derive categories by matching generic human-readable error strings. Explain expected syntax without copying raw submitted values, private review evidence or protocol exceptions into feedback.

Include full reference contents in the existing tool-result path only for request-format failures. Preserve the outer status/reason/execution_started/data contract and native browser-content warning boundary. Do not inject a synthetic user turn, change the fixed system prompt, swap toolsets or loosen treatment of webpage text. Include all fields through the normal native formatting path so tests verify the actual subsequent model request, not only a formatter stub.

The model may retry, observe something else or stop. Do not add an automatic repair loop, retry count or forced action. Existing mission runtime/tool-call policy is unchanged. Earlier guidance may be compacted; corrective feedback must obtain its full copy from the retained resource rather than depend on that history surviving.

## Owning code and contract

BR_006 lives in `browser_tools/specs.json` and requires unit and e2e proof. Coverage is honestly empty until adequate tests exist. Preserve BR_001 through BR_005, AG_001/AG_005 and MI_032 while extending their existing integration paths.

- `browser_tools/__init__.py` owns tool schema, native registration, binding and result formatting.
- `security/gate.py` owns exact request validation and rejection before effects.
- `missions/__main__.py` owns mandatory research-skill preload and the initial native conversation.
- Existing tests live in `tests/signal_scout/browser_tools`, `tests/signal_scout/security/test_gate.py` and `tests/signal_scout/missions/test_cli.py`.

Use one delegated implementation owner for these coupled paths, resource, tests and spec links through poteto-mode after an implementation request. The primary assistant remains manager. Routine internal names and layout are not additional contracts.

## Smallest verifiable slice

1. Add reference loading, startup delivery and deterministic format diagnostics with unit tests. Cover missing/extra/wrong-type fields, invalid refs/URLs and required activity. Confirm no effects or review on malformed input, and no format reminder for permission/safety/context failures.
2. Add repeatable native Hermes plus real owned Chromium coverage with offline model/reviewer transports. Propose a malformed click, inspect the next actual model request for the specific reason and complete reference, then propose a corrected observed ref. Assert one approved click effect, no effect/review for the original request, preserved mission history and byte-stable tool definitions/system prompt. Confirm required startup guidance reaches first inference and missing guidance prevents it.
3. Reconcile real coverage links and run proportional checks using `scripts/run_tests.sh`, explicit files/selections, one Docker worker and zero automatic file retries. Preserve existing successful checks unless changes invalidate them. Run the standalone checker against whole owning spec files and report planned gaps separately from actual test execution and assertion quality.

Do not change upstream Hermes core, add a terminal/file tool, create a new launcher or logging system, dump raw rejected payloads, address the separate cleanup warning, rebuild unrelated image targets, or start a live mission during this planning task. Offline wiring tests cannot prove live-model correction quality; a later authorized short mission can evaluate that separately.
