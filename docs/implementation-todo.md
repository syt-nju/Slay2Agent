# slay2agent Implementation Todo

本文档是执行进度 tracker,不是需求来源。需求以 `docs/feature-requirements.md` 为准,架构边界以 `docs/framework-design.md` 为准。

## Current Mode

Execution Mode.

已确认从 F-001 开始按 feature 顺序推进。F-003 起需要 STS2MCP 实例或样本,目前用户侧暂未提供,届时会先暂停等待。

## Feature Order

建议执行顺序:

1. F-001 Runtime Boundary and Configuration
2. F-002 LLM Adapter
3. F-003 Game Communication Path
4. F-004 State and Action Domain Model
5. F-005 Minimal Runnable Agent Loop
6. F-006 Skill Routing and Tool Bridge
7. F-007 Trace, Metrics, and Baseline Evaluation
8. F-008 Memory and Reflect Improvement Loop
9. F-009 Deferred Extensions

## Progress Summary

- [x] F-002 OpenRouter baseline implemented.
- [x] F-002 retry, usage, error classification, smoke entry implemented.
- [x] F-001 unified CLI + Config + STS2MCP env placeholder + README prerequisites.
- [ ] F-003 STS2MCP mod reachability smoke.
- [ ] F-003 Game client and action wrappers.
- [ ] F-004 State schema and compact prompt.
- [ ] F-005 Minimal Agent loop.
- [ ] F-006 Skill router and tool bridge.
- [ ] F-007 Trace and baseline metrics.
- [ ] F-008 Memory and Reflect loop.

## Phase 0 - Requirements and Harness Docs

- [x] Confirm final goal: runnable baseline first, memory-driven improvement second.
- [x] Confirm scope: STS2MCP JSON/REST only, cloud LLM only.
- [x] Re-split feature list using hybrid feature + milestone structure.
- [x] Create `docs/feature-requirements.md`.
- [x] Create `docs/framework-design.md`.
- [x] Create `docs/implementation-todo.md`.

## Phase 1 - Runtime Boundary and Existing LLM Baseline

Linked features: F-001, F-002

- [x] Review `.env.example`, README, and CLI entrypoint against F-001.
- [x] Add `src/slay2agent/config.py` (LLMConfig + GameConfig, env-driven, no GPU).
- [x] Add `src/slay2agent/cli.py` with `config` / `smoke` / `inspect` / `run` subcommands.
- [x] Register `slay2agent` console script via pyproject; wire `main.py` to it.
- [x] Document STS2MCP / LLM prerequisites in README; drop dangling `plan.md` reference.
- [x] Add `STS2MCP_BASE_URL` / `STS2MCP_TIMEOUT` placeholders in `.env.example`.
- [x] Verify no secrets or local-only files are staged for commit.
- [x] Run offline tests (53 passed).
- [ ] Run OpenRouter smoke only when API credentials are available (manual, deferred to user).

Verification:

- Offline tests for LLM adapter and config/CLI all pass.
- `slay2agent --help` and `slay2agent config` work end-to-end with masked secrets.
- Documentation makes runtime boundary clear (LLM cloud only, STS2MCP REST only, no GPU).

## Phase 2 - Game Communication Path

Linked feature: F-003

- [ ] Confirm STS2MCP version and available REST endpoints.
- [ ] Add `src/slay2agent/game/client.py`.
- [ ] Add `src/slay2agent/game/actions.py`.
- [ ] Add action response/request fixtures.
- [ ] Add tests for at least 5 representative actions.
- [ ] Add manual inspect command.
- [ ] Add action settle helper or minimal settle integration.
- [ ] Run fixture tests.
- [ ] Manually run mod reachability smoke when local game/mod is available.

Expected verification:

- Fixture tests pass.
- `inspect` can print current state when STS2MCP is running.

## Phase 3 - State and Action Domain Model

Linked feature: F-004

- [ ] Add pydantic dependency if not already present.
- [ ] Add `src/slay2agent/game/schema.py`.
- [ ] Model known `state_type` variants from STS2MCP samples.
- [ ] Add domain objects for cards, enemies, relics, potions, and map nodes as needed.
- [ ] Implement `to_compact_prompt()`.
- [ ] Add fixture parse tests for known state types.
- [ ] Keep `diff(prev)` deferred unless needed by compact prompt.

Expected verification:

- Fixture parse tests pass.
- Strategy-facing code can consume typed state objects without raw dict access.

## Phase 4 - Minimal Runnable Agent

Linked features: F-005, F-006

- [ ] Add agent DTOs.
- [ ] Add Perceive -> Execute -> Finalize orchestrator.
- [ ] Add skill base class and state_type router.
- [ ] Add combat/map/event/rewards/fallback skills.
- [ ] Add tool bridge gate.
- [ ] Add pre_execute validation for common invalid actions.
- [ ] Add loop_detector for repeated `(action, args)`.
- [ ] Add minimal JSONL trace writer.
- [ ] Run unit tests for router, bridge, and loop detector.
- [ ] Run a manual complete game session when STS2MCP and game are available.

Expected verification:

- Agent can run from current game state to terminal run state.
- Trace file is non-empty and can be manually reviewed.

## Phase 5 - Trace, Metrics, and Baseline

Linked feature: F-007

- [ ] Define baseline run metadata.
- [ ] Add run-level metrics.
- [ ] Add token aggregation per run and per combat turn.
- [ ] Add simple report command or script.
- [ ] Capture baseline before memory changes.
- [ ] Decide minimum number of baseline runs.

Expected verification:

- Baseline report exists before F-008 starts.
- Metrics are sufficient to compare memory-enabled runs against baseline.

## Phase 6 - Memory and Reflect Improvement Loop

Linked feature: F-008

- [ ] Decide memory storage format.
- [ ] Add memory read/write interface.
- [ ] Add skill-local playbook format.
- [ ] Add Reflect stage to produce experience records.
- [ ] Add Plan stage to consume memory/playbook.
- [ ] Add tests for memory retrieval and update behavior.
- [ ] Run memory-enabled sessions.
- [ ] Compare against F-007 baseline.
- [ ] Record whether improvement is observed.

Expected verification:

- Memory-enabled Agent shows measurable improvement in win rate or token efficiency, or preserves enough evidence to explain why not.

## Phase 7 - Deferred Extensions

Linked feature: F-009

- [ ] Evaluate automatic new run support.
- [ ] Evaluate replay feasibility.
- [ ] Evaluate provider-native adapters.
- [ ] Evaluate more roles/difficulties.
- [ ] Evaluate prompt or skill template auto-improvement.

## Open Blocks

- F-003 needs STS2MCP REST samples (running mod / saved JSON / interface docs). User confirmed none available yet — F-003 work blocked until provided.
- Need STS2MCP runtime availability for manual smoke and full run checks (F-005 onward).
- Need baseline run count and success threshold before F-008 verification.
