## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).


## Deployment rules

- Do not add user-specific details to deployment files, scripts, workflows, examples,
  or documentation: resource IDs, public/service IPs, tenant IDs, subscription IDs,
  account IDs, personal paths, hostnames, or similar deployment-specific values.
  Use GitHub environment secrets/variables, ignored runtime configuration, and
  clearly marked placeholders instead.
- Do not run `aws` CLI or `az` CLI commands without explicit permission from the
  user for those commands. Before requesting permission, explain each command,
  its target, whether it reads or changes state, and its expected effect. General
  task/deployment authorization does not substitute for this command permission.
- Deploy application releases through the reviewed GitHub Actions workflow.
  Do not substitute ad hoc local or SSH deployment commands. Local/offline checks
  may validate the workflow without authenticating to or changing a cloud account.
