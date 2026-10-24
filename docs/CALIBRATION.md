# Native grading policy v2

This pass keeps Google's native managed metrics and raw results. No custom LLM judge,
post-hoc rescoring, lower thresholds or score-based retries are introduced.

Policy v1 remains preserved with its failed verdict. Policy v2 assigns native
multi_turn_tool_use_quality and multi_turn_trajectory_quality an advisory role for
full conversations across BOTH example agents, including successful operations.
Their threshold remains 0.8 and every below-threshold result is reported as a warning.
They remain mandatory: missing scores, service errors and incomplete coverage fail verification.
All other selected native metrics remain blocking at the original thresholds.
In particular, multi_turn_task_success blocks below 0.8. Deterministic authorization,
state, tool-result and format checks always block. Native safety alone is not an authorization test.

Rationale: the prior nine real finance conversations passed deterministic checks;
three tool-use grades and one trajectory grade rejected correct conditional behavior.
The saved reasoning requires prohibited actions or scopes one turn's restriction over
another. The authored-rubric override probe did not replace the generated multi-turn rubrics.
This is a policy mitigation, not a fix to Google's service and not a claim that task
success or other native metrics are universally reliable.

Validation plan, fixed before new live results: rerun all prior finance cases plus two
new prospective cases; run a separate support agent's two single-turn, four development
and two prospective cases; execute four finance faults and two support conversation faults.
Both fault agents are scripted ADK agents calling the actual offline tools. Their outputs
are deliberately incorrect; they are not edited Gemini traces. Grade them with native
metrics and require each fault case to fail the composed gate for behavior, never infrastructure.
Report strict native-policy and v2 decisions side by side, preserving every score.

The old four-case holdout is now regression data. New holdout inputs, policy and metric
configs are frozen together before live execution; no subsequent tuning on these cases.
Labels are authored acceptance expectations, not an independent human-labeled benchmark.
The calibration script identifies disagreements requiring trace/rubric review; it does not
prove that every positive execution is correct merely because assertions pass.
This small exercise is engineering calibration, not a measured production false-positive rate.
Changing metric roles requires a new policy version, rationale and verification evidence.


## Platform review and capacity fixtures

The load pass does not recalibrate the semantic gate or tune either example agent.
Local load scores are explicitly synthetic and are used only to exercise processing
capacity and failure detection; they are not included in native quality evidence.
Real load grading uses the same Google metric family and exact-score threshold.
The initial repeated-placeholder workload failed and remains published. A revised
ordinary-text workload adds an explicit user task and lower dispatch rate. These
changes are reported jointly; they do not isolate a causal fix for judge errors.
Published/inspected holdout cases, including the fresh support rerun, are now regression
coverage. New claims of generalization require new independently reviewed holdouts.
