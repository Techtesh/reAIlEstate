# Effective feature flags — infrastructure only

No operation gates, workers, templates, model changes, settings changes or migrations
are included. Imports and evaluation do **not** create rows. Existing unknown rows
are retained, never interpreted as registered features, and cannot be mutated by
these adapters. Direct ORM writes are not a supported management interface.

## Defaults and dependencies

| Key | Missing-row default | Dependencies |
| --- | --- | --- |
| `media.crop_drafts` | ON (compatibility) | — |
| `media.crop_attach` | ON (compatibility) | `media.crop_drafts` |
| `media.crop_update` | ON (compatibility) | — |
| `media.layout_names` | ON (compatibility) | — |
| `media.layout_removal` | ON (compatibility) | — |
| `media.source_pages` | ON (compatibility) | — |
| `media.auto_segment` | ON (compatibility) | `media.extraction_queue` |
| `media.extraction_queue` | ON (compatibility) | — |
| `operations.extraction_execute` | ON (compatibility) | — |
| `operations.legacy_destructive` | **OFF (deliberate safety)** | — |

Registry entries are frozen `FeatureDefinition` dataclasses; `REGISTRY` and
`UI_FLAG_KEYS` are read-only mappings. Execution is deliberately independent of
enqueue: an emergency execution stop must not erase or silently reclassify queued
work. Gate execution separately, with a fresh snapshot when work actually starts.

Only the exact `settings.ENVIRONMENT` is queried or written. There is no fallback
to development, default/global rows, lowercase aliases, another environment or an
environment supplied by a request. Missing/invalid settings deny. Supported
environment names are 1–32 ASCII alphanumeric/dot/underscore/hyphen characters,
starting with an alphanumeric character, with case preserved.

A missing **registered** row uses its explicit registry default. An unknown key,
DB failure, malformed record, invalid context or failed dependency denies.
`operations.extraction_execute` permits only empty targeting rules `{}`. Other
keys allow `project_ids`, `organization_ids`, and `user_ids` lists: AND between
specified dimensions; OR between values in each list; an empty list denies.
An absent dimension is unrestricted; an absent context ID cannot match a list.
No percentages, wildcards, arbitrary expressions, nested groups or user attributes
are supported. UUIDs and integer IDs have canonical string representations; other
opaque string IDs are case-sensitive. Invalid ID values are rejected, not ignored.

## Public evaluation interfaces

Import from `platform_api.feature_flags`:

- `FeatureContext(actor=None, project=None)`: frozen context; normalizes actor PK,
  project PK and the project's already-loaded `organization_id`. A project ID alone
  supports project targeting, not organization targeting. Anonymous users have no
  user ID. Deferred organization IDs are treated as absent, without a DB lookup.
- `is_enabled(key, actor=None, project=None) -> bool`.
- `require_enabled(key, actor=None, project=None) -> Decision`: raises
  `FeatureDisabled`, a `ValueError` subclass with `code == "FEATURE_DISABLED"`
  and a safe `.decision`, on denial. The exception message contains no input data.
- `FeatureDecisions(context=None, *, actor=None, project=None)`: one bulk query for
  all registered keys in the exact active environment, including dependencies.
- `snapshot(actor=None, project=None) -> FeatureDecisions`: convenience loader.
- Snapshot methods: `.decision(key)`, `.is_enabled(key)`, `.require_enabled(key)`,
  `.snapshot() -> dict[str, Decision]`.
- `.ui_flags -> dict[str, bool]`: same effective decisions, using full key names
  with dots replaced by underscores, e.g. `media_crop_attach`,
  `operations_extraction_execute`. `UI_FLAG_KEYS` maps these names back to keys.

`Decision` is frozen, with `key`, `enabled`, `reason`, `environment`, `source`,
`version`, `versions` and `dependency`. `version=None` denotes no stored row;
`versions` is an immutable tuple of `(key, version)` pairs consulted for the
decision. `.as_dict()` produces a safe diagnostic dictionary. Reasons are fixed
codes: `enabled`, `disabled`, `default_enabled`, `default_disabled`, `target_match`,
`target_mismatch`, `unknown_key`, `invalid_context`, `invalid_environment`,
`malformed_record`, `storage_unavailable`, `dependency_disabled`, `dependency_cycle`.
Unknown input keys are returned as an empty diagnostic key rather than reflected.
Decisions contain no targeting rules, context IDs, exception text or credentials.

Snapshots are request/job-scoped and are **not** refreshed in place. Reuse one
snapshot for UI flags and all decisions within that boundary. Create a new snapshot
at later mutation/execution boundaries; do not keep a process-global snapshot.
Flags are **not authorization**: retain authentication, permissions, ownership,
input checks and atomic operation preconditions independently of effective flags.

## Shared management interfaces

Import from `platform_api.feature_flags.management`:

- `create_flag(*, key, actor, enabled=<registry default>, targeting_rules={},
  environment=<active>, correlation_id=None)` creates version 1.
- `update_flag(flag_id, *, actor, expected_version, changes, correlation_id=None)`
  permits `enabled` and `targeting_rules` changes. Supplying unchanged `key` and
  `environment` is tolerated; changing either is rejected. All other fields reject.
- `disable_flag(flag_id, *, actor, expected_version, correlation_id=None)` persists
  `enabled=False`; it never deletes. Repair invalid targeting via a validated update
  with `targeting_rules={}` if a malformed historical row cannot be disabled.
- `sync_defaults(*, apply=False)` previews missing keys; explicit `apply=True`
  creates them. This is a system/CLI interface, not exposed through HTTP.

HTTP/admin mutations require authenticated, active staff and the action-specific
`platform_api.add_featureflag` or `platform_api.change_featureflag` permission.
Version increments are server-controlled. `expected_version` is mandatory for
updates and disable, never inferred from a freshly loaded row. A row lock plus
conditional version update protects against stale writes. No-op accepted updates
still increment the version and audit the management request.

Validation raises `FlagValidationError` with a fixed `.code`; stale writes raise
`FeatureFlagConflict` (`VERSION_CONFLICT`); storage failures raise
`FeatureFlagUnavailable` (`FEATURE_FLAG_STORAGE_UNAVAILABLE`). Every accepted
write and its `AuditEvent` are atomic. Failed audit writes roll back flag changes.
Audit metadata records key/environment, enabled state, old/new versions and rule
dimension names, **not** rule values or arbitrary request data. Creation/updates/
disable use `feature_flag.created`, `feature_flag.updated`, `feature_flag.disabled`.
System seeds use a null audit actor and `registry_sync` reason.

## API adapter (compatibility export already installed)

The existing router still imports `FeatureFlagAdminViewSet` from the main views
module, which now re-exports the implementation here. The main serializers module
likewise re-exports the validated `FeatureFlagSerializer`. Neither adapter imports
operation services/workers. Direct serializer writes also use shared management.

- Existing `/api/v1/admin/feature-flags/` list/detail/create/update endpoint is
  preserved. Reads require active staff + `view_featureflag`. Writes require the
  corresponding add/change model permission, not merely staff status.
- List/retrieve are active-environment only. Unknown rows are visible but have
  `registered=false` and `editable=false`; they cannot be edited. Their unvalidated
  JSON (and malformed known JSON) is redacted as `{}` in API output.
- POST defaults omitted environment to the exact active environment, enabled to
  the registry default, rules to `{}`; version starts at 1. Explicit nulls reject.
- PUT/PATCH and POST `/{id}/disable/` require `If-Match`. Missing header: 428;
  malformed header: 400; stale version: 409; storage failure: 503. Strong `"3"`,
  existing weak `W/"3"`, and numeric `3` forms are accepted, not `*` or lists.
- Responses to create/retrieve/update/disable carry `ETag: W/"<version>"`.
  All responses use `Cache-Control: no-store`.
- Client-supplied versions, timestamps, IDs and unknown fields reject. DELETE is
  HTTP 405; use disable or PATCH `enabled=false`. Disable accepts no body fields.
- Historical snake_case request fields and the current renderer's camelCase
  `targetingRules` / `projectIds` / `organizationIds` / `userIds` aliases are accepted;
  ambiguous duplicate aliases reject. Stored rules always use snake_case.

## Main-admin integration handoff (intentionally not performed)

The integrator must import `FeatureFlagAdmin` from
`platform_api.feature_flags.admin`, remove `FeatureFlag` from the generic model
registration loop, and register `FeatureFlag` with `FeatureFlagAdmin` once. Do not
leave the generic registration active: it bypasses these mutation protections.
The new module deliberately has no auto-register decorator or unregister side
effects, so it can be safely imported before that handoff.

The ModelAdmin displays version, makes key/environment immutable after create,
submits a hidden `expected_version`, and uses the shared audited mutations.
Unknown and inactive-environment rows are view-only. No deletion, bulk mutation
actions, list editing, or save-as cloning is offered. Disable by unchecking enabled
and saving. Add forms expose the enabled checkbox explicitly, rather than silently
changing a user's selection when a registry key is chosen.

## Opt-in seed and gates handoff

The `sync_feature_flags` management command previews by default; only `--apply`
writes. It accepts no environment override and only creates missing registered rows
in `settings.ENVIRONMENT`. Existing rows/versions/rules and other environments stay
untouched. The seed batch and audit writes are atomic; concurrent conflicts safely
fail rather than overwrite (retry the command after reloading). Seeding is not
required for compatibility defaults to work, and has **not** been run here.

Future gates/adapters should load one contextual snapshot for UI, check the same
key at mutation entry after normal authorization, and load a fresh execution
snapshot for the global execution switch when a queued job actually executes.
Handle `FeatureDisabled` separately by its code; denial must not start work, claim
it completed or remove queued evidence. Legacy destructive operations require
explicit opt-in. No gates are wired by this change.

## Verification status

Regression tests were added for deterministic context, one-query snapshots,
targeting, defaults, environment isolation, fail-closed decisions, dependencies,
shared permissions, version conflicts/CAS, atomic audit rollback, API adapters,
admin forms and opt-in seeding. **Tests, Python, lint, commands, DB access and
installation were not run during this implementation**, per request. Only editor
diagnostics were requested; runtime behavior remains unverified until tests run.