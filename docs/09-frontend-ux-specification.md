# Frontend and UX Specification

## 1. Experience goals

- Make a complex home-buying decision feel progressive and reversible.
- Explain real-estate terminology and avoid false precision.
- Preserve buyer context across guided and expert editing modes.
- Make missing, stale, marketing, authoritative, and derived data visually distinct.
- Keep the application safe when displaying developer-provided content.

## 2. Information architecture

### Buyer navigation

- Discover
- My requirements
- Recommendations
- Shortlist
- Comparisons
- Alerts (P1)
- Privacy/settings

### Developer navigation

- Organization
- Projects
- Documents
- Extraction/discrepancies
- Submission status

### Reviewer/admin navigation

- Review queue
- Projects/data quality
- Integrations
- Taxonomies
- Feature flags
- Audit/operations

Role-specific navigation hides unavailable actions for usability, but APIs independently enforce authorization.

## 3. Buyer onboarding layouts

### Guided layout

```text
+-------------------------------------------------------------+
| Logo       Find a new home in Mumbai           Save status  |
+-------------------------------------------------------------+
| 1 Places  2 Home  3 Space  4 Amenities ... 9 Review        |
+--------------------------+----------------------------------+
| Context/help             | Active interactive panel         |
| - Why this is asked      | Inputs                           |
| - Privacy note           | Examples and validation          |
| - Current preferences    |                                  |
+--------------------------+----------------------------------+
| Back                Save and exit                   Continue |
+-------------------------------------------------------------+
```

On mobile, help collapses below the title and one panel fills the viewport. Back/continue remain visible without covering focused inputs.

### Editable summary layout

```text
+-------------------------------------------------------------+
| Your home requirements                      Guided mode     |
+-------------------------------------------------------------+
| Places                         Complete             Edit     |
| BKC 5/week, Powai 2/week                                  |
+-------------------------------------------------------------+
| Home and space                 Needs attention      Edit     |
| 2 BHK, preferred 800 sq ft                                 |
+-------------------------------------------------------------+
| Amenities                      Complete             Edit     |
+-------------------------------------------------------------+
| Budget                         Complete             Edit     |
+-------------------------------------------------------------+
| [Review privacy choices]                  [Find my matches]  |
+-------------------------------------------------------------+
```

Inline expansion or section navigation is acceptable, but it must use the same draft store and validation schema as guided mode.

## 4. Field interaction details

### Places

- Search uses provider-normalized suggestions and a map/list confirmation.
- Frequency supports common presets plus numeric trips/week.
- Transport mode is multi-select; arrival window and tolerance are optional but recommended.
- Privacy control allows locality-level rather than exact-point precision.
- Never display exact saved destinations in developer/admin demand analytics.

### Configuration and space

- Configuration chips: 1 RK, 1 BHK, 2 BHK, 2.5 BHK, 3 BHK, 4+ BHK, other.
- Area controls always label `carpet area`; unit toggle preserves canonical value.
- Room expectations use optional advanced controls so novices are not blocked.
- Each preference can be marked `Must have` or `Flexible` where meaningful.

### Amenities

Amenity cards provide:
- importance: required/preferred/not needed;
- substitution: society-only or public/private equivalent acceptable;
- maximum substitute distance/time;
- explanatory text that alternatives are not identical.

### Budget

- Indian digit grouping in display; canonical integer INR in API.
- Separate base/purchase and all-in maximum with explanation of additional charges.
- Avoid sliders as the only input; provide accessible numeric fields.
- Sensitive-data notice appears before save.

## 5. Recommendation page

Each card includes:
- project name/locality and current publication status;
- normalized price and carpet-area range;
- MahaRERA verification state and timestamp;
- overall match with 3 strongest reasons;
- hard-constraint result;
- confidence/freshness indicator;
- compare and shortlist actions.

The score is not a universal project quality rating. Label it “Match for your requirements.” A details drawer/page shows component score, weight, inputs, unavailable data, and method version.

Do not use color alone for good/bad/unknown states. Missing data is `Unknown`, never zero unless the metric definition says so.

## 6. Project detail

Recommended sections:
1. Summary and verification
2. Why it matches
3. Configuration and floor plans
4. Price and known charges
5. Your commute
6. Society amenities and nearby alternatives
7. Micro-market indicators
8. Local registered sales (P1)
9. Developer history
10. Evidence, freshness, methodology, and disclaimers

Facts show a compact source badge: `MahaRERA`, `Reviewed brochure`, `Partner record`, or `Derived`. Selecting a badge reveals source date and approved evidence details without exposing storage credentials.

## 7. Comparison

- Compare 2–4 projects.
- Sticky row labels and project headers.
- Normalize money, carpet area, commute assumptions, and dates.
- Toggle `All facts` vs `Differences only`.
- Pin buyer must-haves first and show failures prominently.
- On mobile, use one reference project plus one compared project rather than an unusable wide table.

## 8. Developer upload experience

- Drag/drop and file picker with accepted types and limits displayed before upload.
- Per-file progress separates `Uploading`, `Scanning`, `Extracting`, `Needs review`, `Ready`, and `Rejected`.
- Closing the page does not cancel server processing.
- Safe error text includes correlation ID and remediation; never parser stack traces.
- Extracted fields show source page thumbnails, confidence, conflicts, and authoritative comparisons.
- Developer corrections are new candidate facts and require evidence; they are not direct publication.

## 9. Reviewer console

- Prioritized queue with type, age, project, risk, and assignment.
- Split view: evidence on one side, candidates/history on the other.
- Keyboard shortcuts must not conflict with browser/screen-reader commands.
- Resolution requires reason; destructive/reversal actions require confirmation.
- Presence/version conflict prevents two reviewers from silently overwriting decisions.

## 10. State model

- **Server state:** query cache with explicit stale times and invalidation.
- **Form state:** canonical draft context/store plus schema resolver.
- **Navigation state:** URL routes/search parameters.
- **Session state:** minimal authenticated user/capabilities, not raw tokens.
- **Feature state:** server-provided typed flags.

Avoid copying API entities into a global store unless there is a demonstrated cross-route editing need. Never store uploaded bytes, raw HTML, exact partner responses, signed storage URLs, or credentials globally.

## 11. Safe content presentation

The supplied notebook HTML design establishes the relevant rule: a complete interactive document must not be returned as a string and assigned to `srcDoc` under the main application policy.

For this product:
- render project facts with typed React components;
- render user/developer text as text by default;
- sanitize only a minimal rich-text subset when required;
- display images/PDF previews via authorized same-origin resources;
- keep raw originals private and avoid direct storage URLs;
- if interactive HTML is introduced, use iframe `src` to a separate authorized document, a dedicated CSP, and `sandbox="allow-scripts allow-popups"` without `allow-same-origin`.

Do not weaken the main app CSP or add broad CDN origins as a quick fix.

## 12. Authentication expiry

- API calls receiving 401 enter one coordinated reauthentication flow.
- Preserve only safe unsaved draft data while authenticating.
- Embedded resources must return 401/403, not redirect an iframe/viewer to an identity-provider page.
- Retry idempotent reads automatically once after restored authentication; mutations require idempotency/version safeguards.

## 13. Accessibility

- WCAG 2.2 AA target.
- Semantic headings, landmarks, labels, descriptions, and error associations.
- Visible keyboard focus and logical order.
- `aria-live` for save status, processing updates, and validation summary.
- Focus moves to new panel heading and first invalid field when appropriate.
- Maps/charts always have equivalent textual/tabular information.
- Touch targets at least 24×24 CSS px, preferably 44×44 for primary mobile controls.
- Respect reduced motion, zoom/reflow, contrast, and forced-color modes.

## 14. Localization and formatting

- Initial locale: English for MMR; design strings for future Marathi/Hindi localization.
- Use `Asia/Kolkata`, Indian number grouping, INR, and unambiguous dates.
- Externalize copy; do not concatenate translated fragments.
- Retain canonical units and convert only for display.

## 15. Analytics

Allowed events use opaque IDs and categories: onboarding started, section completed, mode switched, validation failed by code, submitted, result viewed, explanation opened, compare, shortlist, upload state, review completed.

Do not send field values, exact destinations, budgets, notes, document names/content, search text, signed URLs, or partner payloads. Consent and environment policy control analytics activation.

## 16. Frontend acceptance checklist

- Dual modes preserve identical values.
- URLs are stable, encoded, and share only authorized state.
- No secrets or sensitive payloads appear in DOM/global state/logs.
- All screens implement loading/empty/error/unauthorized/stale states.
- Scores have explanations and missing data is explicit.
- Keyboard-only and screen-reader core flows pass.
- Responsive behavior passes at agreed breakpoints and 200%/400% zoom.
- Strict CSP remains effective; untrusted content cannot execute in the app origin.
