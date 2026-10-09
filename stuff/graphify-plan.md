# Graphify Plan for Signal Scout

## Purpose

Graphify will be a persistent navigation index for the Hermes-derived Signal
Scout codebase. Its main purpose is to let Codex traverse architecture and
feature relationships without repeatedly rediscovering the whole repository.

Graphify is not a replacement for source code. For architecture, dependency,
and feature-flow questions, Codex should:

1. Query the graph first.
2. Follow the relevant nodes and paths.
3. Open only the selected source files.
4. Verify important claims against source before answering or editing.

For exact implementation details, bugs, and code changes, source remains the
authority.

## Graph target

Maintain one authoritative graph of the current Signal Scout working tree, not
a frozen graph of the original Hermes baseline. As Signal Scout changes, the
graph should describe the system actually being developed and run. Git history
preserves the upstream Hermes baseline.

### Include

- First-party product code
- Agent loop, model integration, and tool dispatch
- Browser, terminal, file, search, and media tools
- Plugins and skill-loading infrastructure
- Goals, scheduling, sessions, persistence, and budget/usage infrastructure
- CLI, server, web application, and desktop product code
- User-facing installation, update, and migration code
- Deterministically parsed runtime schemas and configuration
- Root product and architecture documentation
- `docs/**/*.md` and `docs/**/*.pdf`
- Plugin README files and `plugin.yaml` manifests
- Scout-relevant built-in skills: Hermes Agent, computer use, YouTube content,
  research, X/Twitter, and blocked-page recovery

### Exclude

- Tests; no separate test graph will be built
- Hermes marketing/documentation website code
- Vendored third-party code
- Generated files, build outputs, and caches
- Pure maintainer, build, packaging, and release tooling
- Examples, demos, benchmarks, and worked samples
- Unselected prose documentation, optional skills, and skill-index catalogs
- Translations, locales, sample media, and decorative media

Hermes agent tools are product code even if they live in a directory named
`tools`. They must not be confused with developer tooling.

## Extraction depth

Use two complementary extraction layers:

- Local AST parsing
- Codex semantic extraction for the exact approved documentation corpus
- Codex-generated community names
- No separate API key; the bridge uses the authenticated Codex subscription

This graph should capture functions, classes, calls, imports, inheritance, and
other statically extractable relationships. Semantic nodes add documented
concepts, product behavior, rationale, and skill knowledge. Dynamic Python
behavior may remain incomplete; source verification remains mandatory.

## Access and visualization

Use Graphify CLI first:

- `graphify query`
- `graphify path`
- `graphify explain`

Do not initially configure MCP, a background Graphify server, Git hooks, or the
Graphify Codex installer. Project instructions should require graph-first lookup
only for architecture, dependency, and feature-flow questions.

Use one graph for both machine traversal and visualization:

```text
Signal Scout source
    -> graph.json
        -> CLI traversal for Codex
        -> full graph.html for human inspection
        -> filtered views of features or communities
```

Filtered views are views of the same graph, not independently extracted or
maintained graphs. If the full HTML graph is too large, smaller presentation
views may later be derived from the same `graph.json`.

## Storage and reproducibility

Generated graph artifacts should remain local and Git-ignored. Do not commit
large graph output or caches.

Commit only the small inputs needed to reproduce the graph:

- Exact scope and ignore rules
- Graph-generation commands or script
- Pinned Graphify version

Do not automatically upgrade Graphify. Review its source or changelog before an
upgrade, approve the version explicitly, and rebuild the graph afterward.

Graphify is pinned to `graphifyy==0.9.53` in
`scripts/graphify-codebase.sh`. The Codex bridge pins `gpt-5.6-luna` with low
reasoning in `scripts/graphify-codex-shim/claude`.

The bridge uses Graphify's existing `claude-cli` subprocess contract so the
pinned Graphify package remains unmodified. The compatibility shim calls
`codex exec` with an ephemeral session, ignored user config, an empty temporary
working directory, a read-only sandbox, disabled tools/plugins, and local JSON
schema validation. Source content is explicitly treated as untrusted data.

## Safe staged build

Build in explicit stages:

1. Extract the scoped first-party AST graph with clustering disabled.
2. Run `semantic-smoke` on two small files before subscription-heavy work.
3. Run semantic extraction with clustering disabled and audit file coverage.
4. If dense batches omit files, run the cache-preserving `semantic-retry` pass.
5. Cluster locally.
6. Label communities through Codex and audit placeholders and duplicates.

Commands:

```bash
./scripts/graphify-codebase.sh extract
./scripts/graphify-codebase.sh semantic-smoke
./scripts/graphify-codebase.sh semantic
./scripts/graphify-codebase.sh semantic-retry  # only when coverage audit reports misses
./scripts/graphify-codebase.sh cluster
./scripts/graphify-codebase.sh label
```

Trial limits:

- Four AST workers
- Stop if the Graphify process exceeds 4 GB RAM
- Stop if a stage runs for 15 minutes without progress
- Never start a semantic, retry, or labeling pass implicitly
- Semantic calls are serial and adaptive retries are disabled, bounding calls
- Completed semantic chunks are cached per source file

AST work is cached per file. An interrupted extraction can reuse completed file
caches. Clustering does not have an exact mid-stage checkpoint, but a completed
raw graph lets clustering restart without reparsing all source files.

## Adoption benchmark

The graph must help answer these real Hermes/Signal Scout questions:

1. How does a browser tool request travel from the agent loop to the browser
   implementation?
2. How are plugins and skills discovered, loaded, and exposed?
3. How does video input become model-usable context?
4. How do goals, cron jobs, and sessions persist and resume?
5. Where can Signal Scout enforce its Codex usage budget?

The trial passes only if:

- Queries identify correct files and relationship paths.
- Source verification confirms the graph results.
- Vendor, generated, website, example, and maintainer code does not dominate
  results.
- Codex can answer by opening only graph-selected source files.
- The visual graph loads and can isolate relevant features or communities.
- Incremental refresh works after relevant code changes.

If these conditions fail, Graphify is not adopted merely because it produced a
large or visually impressive graph.

## Freshness policy

After every upstream Hermes pull, compare changed files against graph scope:

- If only excluded files changed, do not update the graph.
- If included product code, schemas, or configuration changed, propose an
  incremental Graphify update.
- If scope rules or major directory structure changed, propose a full rebuild.

Always ask the user before running an incremental update or full rebuild. Do not
install automatic Git hooks.

## First aborted trial

The first command was run before scope consensus:

```bash
uvx --from graphifyy graphify extract . --code-only --max-workers 6
```

It scanned 7,905 code files, including unwanted repository surfaces. AST parsing
finished with four partial-parser warnings, then clustering reached about 3.3 GB
RAM and 81% CPU without producing a final graph. The process was stopped at the
user's request. Its incomplete 211 MB `graphify-out/` cache was moved to desktop
trash and is recoverable until trash is emptied.

Do not repeat this unscoped command.

## Official references

- Documentation: https://graphify.com/docs
- Tutorial: https://graphify.com/docs/tutorial
- CLI reference: https://graphify.com/docs/cli
- Security and local data flow: https://graphify.com/security
- Source: https://github.com/Graphify-Labs/graphify

Official documentation says Graphify's structural parser runs locally without
an account or API key. Package execution still creates software supply-chain
risk, which is why the tested version must be pinned and upgrades reviewed.

## Structural trial result

The approved scoped trial used Graphify 0.9.53. Extraction indexed 2,638 code
files and produced 63,219 nodes and 172,121 raw edges without an LLM or API
tokens. Graph loading and clustering normalized this to 160,307 edges and
created 1,121 communities. Extraction stayed below the 4 GB ceiling; clustering
completed in about 2 minutes 22 seconds and stayed below 500 MB observed RAM.

Scope filtering worked. Tests, website code, documentation, skills, examples,
vendored code, generated code, and maintainer tooling did not dominate or leak
into sampled results. Two files produced partial-parser warnings but still
yielded symbols: `native/fts5_cjk/fts5_cjk.c` and
`web/src/pages/ModelsPage.tsx`.

The graph is useful as a navigation index when queries use exact terms and then
switch to `graphify explain` or community membership. Sample feature nodes
clustered coherently:

- Browser implementation: community 11
- Plugin registration: community 161
- Session persistence: community 21
- Video analysis: community 213
- Speech transcription: community 74
- Account usage: community 268
- Iteration budget: community 51

Source checks confirmed the graph-selected files for all five benchmark areas.
Broad natural-language queries remain noisy, and static relationships do not
fully represent dynamic Python registry dispatch. In particular, Graphify did
not find a directed path from agent tool invocation to `browser_navigate()` even
though source inspection confirms the runtime connection. The graph therefore
cannot replace source verification or reliably answer every end-to-end flow by
itself.

The aggregated `graph.html` loads successfully. It can search nodes, inspect
communities, and toggle communities. Feature-specific views may be derived later
from the same graph if useful.

Generated artifacts remain under ignored `graphify-out/`. The incremental
refresh criterion remains untested because refreshes require user approval.
Adoption remained pending until the semantic graph was tested against real
Hermes feature-flow questions.

## Semantic enrichment result

The approved corpus contains 184 files: 181 documents and 3 papers. It includes
root/product architecture docs, `docs/`, plugin README/manifest files, and the
six selected built-in skill areas. Tests, website code, optional skills,
translations, catalogs, decorative media, and unrelated documentation remain
excluded.

The Codex smoke pass succeeded. The first 40,000-token-batch pass used 242,470
input and 15,669 output tokens but omitted 97 mostly small plugin manifests.
A 5,000-token cache-preserving retry used 104,515 input and 13,097 output tokens
and reduced misses to 36. Graphify's prompt required attribution but not
per-file coverage, so the bridge added an extraction-only coverage invariant.
The final one-call retry used 16,178 input and 2,651 output tokens and covered
all remaining files. Graphify rejected every model-attributed path outside the
dispatched scope.

Community labeling used 121,957 input and 7,768 output tokens across five
Codex calls. Including the smoke call, the complete semantic-and-labeling trial
used 495,641 input and 39,808 output tokens. Codex exposes token usage for these
calls but does not expose an authoritative conversion to a percentage of the
user's daily subscription allowance, so this run cannot prove a “50% of daily
usage” bound.

Final graph: 63,345 nodes, 160,323 edges, and 1,249 communities. The semantic
layer contributes 225 nodes and represents all 184 approved source files.
Codex named all 1,249 communities: zero `Community N` placeholders, 1,216 unique
names. Sample queries now return `Browserbase Plugin`, `YouTube Content Skill`,
`Research Skills Description`, and `Security & Privacy Toggles` alongside
relevant implementation nodes.

The semantic layer improves discovery and human readability, but it remains an
LLM-produced index. Duplicate labels and imperfect inferred relationships are
expected; source files remain authoritative.

## Adoption validation

Validation on 2026-09-01 used three Hermes subsystems:

1. Browser request dispatch. Exact-node lookup found `AIAgent._invoke_tool`,
   `agent_runtime_helpers.invoke_tool`, `model_tools.handle_function_call`, the
   tool registry, and `browser_navigate`. Source confirmed this runtime chain:
   the agent forwarder applies middleware and hooks, calls
   `handle_function_call`, dispatches through the registry, and reaches the
   registered browser handler. Graphify did not produce a directed end-to-end
   path because registration and handler selection are dynamic.
2. Plugin and skill loading. Exact-node lookup found `discover_plugins`,
   `PluginContext.register_tool`, `_build_skills_system_prompt_inner`, and
   `iter_skill_index_files`. Source confirmed manifest scanning, config gates,
   `register(ctx)` execution, registry exposure, skill directory filtering, and
   prompt-index construction. Direct static call paths worked for skill
   discovery; plugin registration required source verification.
3. Video analysis. Graphify returned the correct two-hop directed path:
   `_handle_video_analyze` -> `video_analyze_tool` -> `async_call_llm`. Source
   confirmed safe local/remote materialization, whole-video base64 encoding,
   the 50 MB payload cap, dispatch to a configured video-capable auxiliary
   model, and text analysis returned to the agent.

Broad natural-language queries returned 675-1,225 nodes with many irrelevant
starts. Exact symbols plus `explain` or `path` were materially better. The
aggregated HTML loaded headlessly with 1,249 searchable community nodes and
6,610 community edges; the unfiltered view is too dense to navigate by sight,
but search and community toggles make it usable.

Verdict: adopt Graphify as a navigation index, not as source of truth. Project
instructions now require graph-first lookup for architecture, dependency, and
feature-flow work, followed by targeted source verification. No hooks, MCP,
automatic refresh, or separate filtered graph was added. Incremental refresh
remains untested; after every upstream pull, assess scope changes and ask before
updating.

## Next step

Return to Signal Scout milestones. Use the graph-first workflow to identify the
smallest standalone plugin and research-skill integration points for milestone
1 before changing runtime code.
