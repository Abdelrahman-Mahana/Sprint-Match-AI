# Agent workflow (LangGraph)

The recommendation agent is one compiled `StateGraph` (`app/agents/builder.py`, LangGraph 1.2.19) with 16 nodes,
a `ToolNode` for 20 read-only tools, conditional routing, a node retry policy and a PostgreSQL checkpointer.
Full diagram: [`agent_graph.mmd`](agent_graph.mmd) (generated with `uv run python -m scripts.export_graph`).

```text
START -> validate_request -> load_task_context -> task_analysis -> workforce_research_agent
             |                    |                   |                |  ^
             v                    v                   |  tool calls?   v  |  results always return
        failure_node <------------+-------------------+-------- tool_execution (ToolNode)
             |                                        | NEEDS_INPUT / PROVIDER_UNAVAILABLE
             |                                        v
             |        load_authoritative_data -> build_employee_context -> calculate_availability
             |        -> calculate_workload -> eligibility_filter -> [performance_analysis]
             |        -> recommendation_engine -> explanation_agent -> final_validation
             |                                                              | invalid -> failure_node
             +--------------------------> save_recommendation <-------------+ valid
                                                  |
                                                 END
```

## Nodes (`app/agents/nodes/`)

| # | Node | Responsibility | Output (state keys) |
|---|---|---|---|
| 1 | `validate_request` | request schema, single task reference, `as_of` offset, tenant, window shape, DB ping | errors / status on failure |
| 2 | `load_task_context` | task + project/sprint + dependencies (or ad-hoc spec), execution window | `task_record`, `task_input`, `execution_window` |
| 3 | `task_analysis` | manager values > validated LLM suggestions > defaults; injection filtering | `task_analysis`, `requirements` or `NEEDS_INPUT`/`PROVIDER_UNAVAILABLE` |
| 4 | `workforce_research_agent` | LLM with the 20 tools bound; bounded loop; grounded research summary | `messages`, `research`, `research_rounds` |
| 5 | `tool_execution` | official `ToolNode` + execution policy (`app/agents/tools/policy.py`) | `messages` (ToolMessages), `agent_tool_results` |
| 6 | `load_authoritative_data` | candidate set and workforce records from PostgreSQL (independent of the LLM) | `employee_ids` (+ workspace) |
| 7 | `build_employee_context` | normalized Employee 360 summaries (effective levels of required skills) | `employee_profiles` |
| 8 | `calculate_availability` | deterministic capacity per employee over the whole window | `availability_results` |
| 9 | `calculate_workload` | remaining commitments, unknown estimates, overcommitment | `workload_data` |
| 10 | `eligibility_filter` | hard constraints incl. certifications; explicit exclusion reasons | `eligibility_results` |
| 11 | `performance_analysis` | quality / reliability / similar-task evidence with sample counts | `performance_results` |
| 12 | `recommendation_engine` | official weighted ranking (deterministic, versioned) | `engine_result`, `ranked_candidates`, `data_quality` |
| 13 | `explanation_agent` | LLM explanation of validated results, grounding-checked; deterministic fallback | `final_explanation` |
| 14 | `final_validation` | invariants + re-checks: employee exists, eligible, score re-computed equals stored, evidence, availability, mandatory constraints, exactly one primary, explanation grounded | `validation`, `final_recommendation` |
| 15 | `failure_node` | standardized FAILED response with categorized errors, never a fabricated result | `final_recommendation` |
| 16 | `save_recommendation` | upsert run, candidates, score components, node logs, tool executions, errors, audit | `persisted` |

Every node except the ToolNode is wrapped by `traced` (`nodes/common.py`): start/end time, duration, attempt
number, LLM tokens/calls (from the per-run usage meter), sanitized output summary. The ToolNode round is traced
by the research node from the tool records.

## Conditional routing (`app/agents/routing.py`)

| From | Condition | To |
|---|---|---|
| any node | `execution_status == FAILED` | `failure_node` |
| `task_analysis` | NEEDS_INPUT / PROVIDER_UNAVAILABLE | `final_validation` (valid outcome without candidates) |
| `workforce_research_agent` | last AIMessage has tool calls and research still in progress | `tool_execution` |
| `workforce_research_agent` | final answer, round/call limit reached, LLM failure, agent disabled | `load_authoritative_data` |
| `tool_execution` | always | `workforce_research_agent` |
| `load_authoritative_data` | no employee records | `final_validation` (NO_SUITABLE_CANDIDATE) |
| `eligibility_filter` | nobody eligible | `recommendation_engine` (skips performance, explicit NO_SUITABLE_CANDIDATE) |
| `final_validation` | invalid | `failure_node` |

## Research agent and tool loop

* Mode (`app/agents/configuration.py`): `native` when the model supports tool calling (probed once), `json_plan`
  for models with structured output only (validated JSON tool plans, allowlisted, converted to ordinary tool calls
  so the same ToolNode executes them), `disabled` without a suitable model. Override: `AGENT_TOOL_MODE`.
* Model: `ProviderChatModel` (`app/infrastructure/llm/chat_model.py`), a LangChain `BaseChatModel` over the
  project's provider layer (retries, Retry-After, redaction, usage accounting); `bind_tools(ALL_TOOLS)`.
* Limits: `AGENT_MAX_TOOL_ROUNDS` (5), `AGENT_MAX_TOOL_CALLS` (20), `AGENT_TOOL_TIMEOUT_SECONDS` (10, plus a
  PostgreSQL `statement_timeout`), `AGENT_MAX_TOOL_RESULT_ITEMS/CHARS`, `AGENT_MAX_PROFILE_EXPANSIONS` (10), graph
  `recursion_limit`.
* Policy per call: unknown tool -> rejected; malformed/extra/invalid arguments -> rejected; role check; identical
  call -> `duplicate` (cached result); budget / expansion limits -> rejected; timeout -> `timeout`. The model always
  gets a ToolMessage (result or structured error).
* The research summary is grounded (employee codes must appear in tool results) and **advisory**: ranking, scores
  and eligibility come only from nodes 6-12. LLM failure -> `research.status = failed`, deterministic run continues.

## State and checkpoints

`SprintMatchAgentState` (`app/agents/state.py`) is JSON-only (ids, validated models dumped with `mode="json"`,
summaries) plus LangChain messages; reducers `add_messages` and `operator.add` for warnings, errors, trace and tool
records. Full employee records live in the non-checkpointed `RunWorkspace` and are reloaded from PostgreSQL after a
resume. Checkpointers (`app/agents/checkpointer.py`): `PostgresSaver` / `AsyncPostgresSaver` in schema
`agent_checkpoints` (tables via the official `setup()`), `InMemorySaver` for tests. Thread id = recommendation id.

## Failure handling and recovery

| Failure | Behaviour |
|---|---|
| invalid request / unknown task | `failure_node` -> FAILED (VALIDATION_ERROR) persisted |
| database unavailable in a node | LangGraph `RetryPolicy` (bounded), then the run stops **at that node**: FAILED, `resumable: true`; `POST /agent/runs/{id}/retry` continues from the checkpoint, the same run id is updated (no duplicate) |
| tool failure | structured ToolMessage error, research continues |
| LLM failure | research `failed` / deterministic explanation / PROVIDER_UNAVAILABLE only when the LLM was required for requirements |
| invalid result | `final_validation` -> `failure_node` |
| step limit | GRAPH_RECURSION_LIMIT |
| completed FAILED run retried | new run linked by `retry_of_id` |

## Invocation

`RecommendationService` (`app/application/recommendations.py`): `create` (`invoke`), `acreate` (`ainvoke`, used by
FastAPI), `stream` / `astream` (node and tool events, SSE endpoint), `resume` / `aresume`, idempotency keys.
