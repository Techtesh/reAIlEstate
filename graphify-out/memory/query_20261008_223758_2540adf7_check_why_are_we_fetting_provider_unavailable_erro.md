---
type: "query"
date: "2026-10-08T22:37:58.017045+00:00"
question: "check why are we fetting PROVIDER_UNAVAILABLE errors , ollama is working"
contributor: "graphify"
outcome: "useful"
source_nodes: ["_profile_for_selection()", "queue_layout_evaluation()"]
---

# Q: check why are we fetting PROVIDER_UNAVAILABLE errors , ollama is working

## Answer

Expanded via vocabulary: provider unavailable vision profile selection timeout. Local Ollama tags responds and gemma3:4b exists. All 34 noncompleted queue rows with PROVIDER_UNAVAILABLE have stale or missing vision pins and are dead-lettered; another 3 completed rows retain historical queue errors. Recent Adityaraj Greens A Wing Units 01-04 and Empire jobs pin hash 99f25c1b, exactly reproduced with current profile response_format=json_object, while active json_schema profile hash is 93abc4a4. _profile_for_selection rejects them before any network/model request. queue_layout_evaluation retry and queue-monitor retry preserve the original selection, so requeue cannot repair drift. layout_evaluation_error flattens ProviderUnavailable into a misleading generic code. Diagnosis only: no app code, job pins, model requests or queues changed. Resolution requires an explicitly authorized new evaluation attempt using current settings, preserving old attempts and exposing configuration drift clearly.

## Outcome

- Signal: useful

## Source Nodes

- _profile_for_selection()
- queue_layout_evaluation()