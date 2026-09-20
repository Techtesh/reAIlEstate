# MMR Homefinder Buyer Frontend

React 19, TypeScript and Vite buyer experience backed by the existing Django API.
It provides guided and summary requirements, ranked map/feed results, an explainable
score sheet, published project details, comparisons and account-owned shortlists.

## Run Locally

Use Node.js 22.12+ and npm. From the workspace root:

```powershell
npm --prefix reailEstate/reailEstate_common/frontend ci
npm --prefix reailEstate/reailEstate_common/frontend run dev
```

Open http://127.0.0.1:5173/. Django must already be running at
http://127.0.0.1:8002/, using this workspace's database. See the
[backend startup instructions](../backend/README.md#terminal-setup).
Do not recreate the database, reingest samples or publish projects for frontend setup.

The Vite proxy forwards `/api` to Django while preserving the incoming Host header
for same-origin session cookies, CSRF checks and pagination links. Use the loopback
address above consistently. No API credentials belong in frontend environment files.
If port 5173 is occupied, use `npm --prefix reailEstate/reailEstate_common/frontend run dev -- --port 5176`.

Sign in using an existing workspace account. Account creation is administered in
Django; the buyer UI does not offer public registration. Guest requirements can be
entered before signing in. Discover and Browse, published listing details, filters,
and station layers are public. Saved requirements, matches, shortlists and comparisons
require sign-in. Guest detail responses contain listing fields only, not internal evidence.

## Builder workspace

The homepage **Sign in** dialog asks the user to select **Buyer** or **Builder**
before entering credentials. Buyer uses the normal account login; Builder uses the
membership-aware builder login and routes directly to the builder workspace.
Choosing Builder does not grant access: the backend still requires an active
owner/editor organization membership. Signed-in users can click their username
to choose again; the builder header also offers **Switch workspace**.

Open `/builder/login` (also linked from the public footer) for the dedicated builder
sign-in experience. `/builder` restores the current session on reload without loading
the buyer workspace. A platform administrator provisions accounts and owner/editor
memberships in active organizations; there is no public sign-up or membership management.
Buyer or staff status alone does not grant builder access.
Builder sign-in distinguishes incorrect credentials from a correct password with
missing organization access. Administrators can add memberships with organization,
role and status dropdowns on the saved user's admin edit page.

Select an organization to create or edit projects, including MahaRERA registration
(validated and normalized by the backend), address and processing attestation. New
projects begin in verification. Project organization
and publication status are server-managed. Layout names and optional positive total
INR prices can be edited; changing published data requires independent review.
Conflicting edits retain local entries and block saving until an explicit reload.
Archiving requires confirmation, retires the project and does not delete evidence.

In **Project details**, use **Project location entry method** to supply a Google
Maps pin/share link or a latitude/longitude pair. A short Maps link is resolved
through the existing restricted Google resolver; links without a unique pin need
coordinates instead. Saving creates a private location proposal for independent
review, not a published map pin. Later metadata edits keep the saved location by
default. Incomplete, out-of-range or unresolved entries are not saved.

The **Project media workspace** has tabs in this order: **Project images**,
**Amenities**, **Floor plans**, **Layouts**. It provides private previews, a
carousel, a layout review table, and the existing alias/total-INR-price editors.
The top-right **Open image in new tab** icon opens the selected preview or crop
draft at full size without leaving the workspace or discarding edits. The crop
editor also links to the original source page in a new tab. These links retain
the existing same-origin, authenticated media access; opening them does not
save changes or start extraction.
Carpet area in square feet, configuration and review state remain read-only.
The **Layouts** tab uses an admin-style searchable, sortable list on the left and
the selected layout's image, controls, room measurements, name and price on the
right. On narrow screens the list stacks above the details. Switching layouts
keeps unsaved price edits, which are marked in the directory.
Room length/width (feet), calculated area (square feet), configuration, room counts,
features and extraction provenance are shown from saved evaluation results.
Room rectangle totals are not carpet area. Unknown measurements are not shown as zero.
Per-layout evaluation state uses the same checksum-bound status logic as admin:
queued, extracting, failed, completed without dimensions, or completed awaiting
review. Failed crops explain their error and offer a confirmed retry; queued or
running jobs cannot be queued again from the button. **Refresh media and jobs**
loads new results without starting extraction or changing the selected layout.
There are no admin links or publish/approve controls.
Click the title or pencil beneath a floor-plan or layout preview to edit its name
in place. Enter or the checkmark saves immediately by default. Administrators can
enable **Confirm name changes** on an organization to require the extra confirmation
for its projects. This organization setting defaults to false and affects only
name updates, not destructive actions or AI cost confirmations. Escape or
the cancel icon discards the name edit. Blank names are rejected. Price editing
remains separate, without a duplicate name textbox. Unsaved inline names prompt
before changing previews or categories; failed saves and refreshes retain the draft.
Project pictures can be chosen from available previews, uploaded as a validated
PNG/JPEG/WebP (up to 10 MB), or reset to the automatic first-page cover. Picture
changes are confirmed, version-checked and require independent review.

**Crop source page** lets a builder choose a source document and page; **Crop this
image** preselects the displayed item's source. The crop editor opens directly
below the image gallery, before layout tables or draft management. Draw a rectangle or enter X/Y/width/
height percentages and a label. **Save crop draft** sends normalized fractional
bounds, category, source SHA-256, selected quarter-turn rotation and a UUID request ID; it stages a draft
without running AI. The crop request ID survives explicit retries and media refreshes
until a crop field changes. Missing/corrupt sources expose the server's error and
cannot be selected for cropping; a zero page count also disables cropping.
Amenities crops retain their category instead of becoming layouts. Generated crop
labels fit the 64-character limit. Saving an untouched whole page gives actionable
feedback rather than sending an invalid crop request.
Under **Floor plans**, choose **Manually crop layouts** to outline multiple layouts
on the selected floor plan's original source page. Draw rectangles consecutively:
starting a new rectangle automatically retains the previous one, without requiring
**Add layout crop** or a label between gestures (up to 20 rectangles per save).
Default labels can be edited in the retained-crop list or the current **Crop label**
field. **Add layout crop** remains available when entering percentage bounds.
Numbered outlines and a removable list keep track of unsaved selections. **Save layout drafts**
saves all valid added rectangles plus the valid current labeled rectangle in one request,
always in the layouts category and without modifying the floor plan or running AI.
Manual rectangles may overlap to include shared walls and adjacent homes; the
automatic extraction overlap check is not applied to user-selected crops.
Each new layout rectangle covers 2-70% of the source image area (width times height).
Smaller added rectangles are dropped with a warning; a smaller unfinished/current
rectangle is silently ignored when saving so stray gestures do not create extra
layouts. Exactly 2% is accepted. The minimum does not change existing crop updates
or non-layout media crops, and backend validation also rejects undersized new layouts.
Validation and server failures appear beside the Save button;
failed saves keep all local rectangles and do not create drafts.
Select the saved drafts below and use **Attach selected drafts** to confirm
evaluation through the standard pipeline. **Extract layouts** remains the
separate automatic extraction action.
The source editor also offers **Add full page as floor plan** and **Add full page
as amenities**. These use the entire verified page, not the crop rectangle, and
queue the standard processing pipeline only after explicit cost confirmation.
Select saved drafts and an optional evaluation profile, then explicitly confirm
model costs to attach them in their selected category and queue normal evaluation. Discarding
selected drafts also requires confirmation and does not remove source evidence.
**Re-evaluate layout** queues one version-checked layout evaluation only after
explicit cost confirmation. Source-page bulk additions are not exposed by this UI.
**Reset / update crop** stages a change to the existing floor plan or layout;
committing retains its identity and alias rather than adding a duplicate.
The plan-management controls support activation/deactivation, reversible removal,
restoration, and explicitly replacing selected layouts with selected crop drafts.
Each checkbox shows the saved enabled state: checked means enabled. Changing it
requires confirmation. Disabled plans show a trash icon on the right for confirmed,
reversible deletion; original evidence is retained. Failed changes keep the saved
checkbox state. When drafts are selected, a separate **Layouts to replace with drafts**
selector chooses replacement targets without changing their enabled state.
Deactivating a floor plan also deactivates its derived layouts. Activating the floor
plan later does not automatically reactivate layouts.
Layout rotation/normalization, floor-plan layout extraction, saved-amenity
evaluation, and all/failed-document reprocessing use the existing backend pipelines.
No browser action launches a worker or approves/publishes its results.

Media uses organization-authorized `/api/v1/builder/projects/{id}/media` APIs.
Source-page PNGs and draft previews are same-origin private resources. Writes
refresh media; the jobs panel shows five entries at a time with keyboard-accessible
scrolling for the remaining jobs. **Refresh media and jobs** checks progress without polling or
retrying mutations. Attach/discard/evaluation confirmations carry a UUID request ID.
Failed action retries retain that ID across refresh/reconfirmation only for the same
endpoint and payload; changed profiles, versions, selections or action kinds receive
a new ID. Successful actions clear the retry ID.
Access failures clear the private session through the shared API helper. Conflicts
preserve local entries and require a refresh and a new action selection.
Unsaved project or layout edits block media mutations, and tab switches and media
refreshes preserve layout/crop entries. Switching projects/organizations prompts
before discarding local edits; reloading the project explicitly discards its local
project, layout and crop entries together. Retired/merged projects and a false
media `canEdit` are read-only.

Upload PDF, PNG, JPEG or WEBP evidence from a saved project's document section.
The browser computes SHA-256, requests a same-origin upload intent, transfers the
binary with the supplied media type, and completes it with a stable idempotency key.
Failed transfer/completion retries resume the same document rather than automatically
creating duplicates. If the initial intent response was lost, refresh documents before
explicitly retrying the intent. Canceling a local upload does not delete server evidence.
No document contents, passwords, memberships or project drafts are put in browser storage.
Logging out or losing session access clears the private workspace and cancels requests.

**Submit for review** requires saved attestation, evidence and a registry verification
attempt. The portal queues processing/review only: it never starts workers or
direct model calls. Explicitly confirmed media actions queue evaluation for backend
workers; builders cannot publish their own submissions.

Focused browser regressions use mocked APIs and do not mutate backend data:

```powershell
npm --prefix reailEstate/reailEstate_common/frontend run build
npm --prefix reailEstate/reailEstate_common/frontend run test:e2e -- tests/builder.spec.ts
```

Coverage includes login/access checks, organization isolation, create/edit conflicts,
layout pricing, upload retry and origin validation, archive/review validation, media
tabs/previews, normalized crop staging, draft attachment/discard and evaluation
confirmation, read-only scope, stale requests, dirty-form preservation, session expiry,
logout and 320/390px layouts. Run the existing buyer suite separately to verify
public browsing and gallery behavior remains unchanged.

## Layout pricing

Project cards, details, comparisons and shortlists display the API's
layout-derived `priceMin` to `priceMax` range in INR. A single known price is shown
once; no available layout prices produces **contact to know**. Price sorting uses
the derived minimum and places unknown prices last. Legacy configuration prices
are not used as a fallback. Each layout in the gallery shows its own total price.
Project names are the card headings; prices are secondary. Browse's compact results
bar keeps the count, processing toggle and sorting without a large title banner.

Organization owners/editors can enter prices under **Builder workspace -> Project ->
Layout names and prices**. Platform administrators can also use **Django admin ->
Project media -> Layouts -> Total price (INR)**.
Prices are optional positive amounts with up to two decimal places. The range
uses eligible active layouts and follows the existing review/public-preview
policy. Prices may exclude taxes and other charges.

**Available layouts** lists every eligible layout by its alias, including distinct
layouts with the same BHK type. Carpet area, price, and the backend-calculated room
area total are separate columns. The calculated total sums available room rectangles;
it may include decks/overlaps and must not be interpreted as carpet area. Missing
carpet area remains unknown even when room dimensions are available.
The comparison table follows the media-type selector: **All media** and **Project
images** show every eligible layout; **Floor plans** shows only layouts linked to
the currently selected floor-plan image through saved source-image ancestry.
Switching floor-plan images updates the rows. **Unit layouts** and **Amenities**
hide this table; individual layout measurements remain available in the gallery.

## Matching Worker

Submitting requirements creates a queued recommendation request. In a separate
terminal, follow the backend terminal setup, then explicitly start the worker when
you intend to process the workspace's pending recommendation jobs:

```powershell
& $Python manage.py process_outbox --event-type recommendations
```

This consumes recommendation work across this workspace, not just the current tab.
It does not process extraction jobs. The frontend never starts a worker or runs
layout evaluation. Without a worker, the results screen retains the queued state
and offers a status check. See [queue commands](../backend/README.md#ingestion-and-evaluation-queues).

## Routes and Data

| Route | View |
| --- | --- |
| `/` | Published catalogue, locality/configuration filters, map and feed |
| `/browse` | Public listing browser with metro and railway map layers |
| `/requirements` | One canonical draft in guided or summary mode |
| `/profiles` | Saved drafts and submitted-profile copies |
| `/matches/{snapshotId}` | Ranked results, score sheet and snapshot metadata |
| `/shortlist` | Saved projects and private daily-commute destinations in map/feed format |
| `/privacy` | Consent, local storage and map-provider disclosure |

- Discover, Browse, matches and shortlists use one Leaflet/OpenStreetMap map with
  published property pins and local station layers. There is no Google iframe, title
  banner or map-source switch. **On map** selects the property in the same map and
  opens map view on mobile, preserving the current property filters.
- Stations and schematic connections have matching line colours and marker labels
  such as M1 and M2A. The Transit line selector isolates one line without filtering
  projects, and the Line colours legend identifies the palette. It follows the supplied
  Mumbai colour mapping: 1 blue; 2/2A/2B yellow; 3 aqua; 4/4A green; 5 orange; 6 pink;
  7/7A/9 red; 8/10/11 black (colour unannounced); 12 mandarin. Hex shades are display
  choices, not an independently verified operator style guide. Unknown-line stations
  remain unconnected, and colours alone do not establish connections between branches.
- Solid, opaque 7px segments have an 11px white outline for visibility on map tiles.
  Below zoom 12, station badges shrink to 10px dots so they do not obscure the lines;
  zooming in restores numbered 32px badges. Station titles and popups remain available.
  Lines follow visible station layers without a separate schematic-lines toggle.
  Schematic segments use adjacent same-line, same-kind station sequence records (or configured CSV rows),
  not alphabetical station order or nearest-neighbour routing. Invalid/conflicting
  records, line changes and repeated stops break segments; gaps are not bridged.
  Hiding a station removes adjacent segments, and hiding unverified records also hides
  these unverified schematics. Solid styling does not establish verified geometry or service.
- Three explicit schematic connections are supported separately from same-line
  sequences: Miragaon to Dahisar East (Line 9), Gundavali to Airport Colony (7/7A),
  and Kasarvadavali (Kasarwadavli) to Gowniwada (4/4A). They use existing unambiguous
  candidate coordinates, never fabricated stations or verified track claims.
  Selecting either branch retains the connecting terminal; hiding its layer or
  unverified stations removes the connection. Existing adjacent edges are not duplicated.
- CSV order, actual tracks, operating status and transfers remain unverified. Crossings
  or shared coordinates do not establish interchange or through-service connections.
  Accurate route geometry would need an authorized dataset; nothing is imported from
  My Maps, and these display segments are not used for journey calculations.
- Browse's **Show processing listings** toggle defaults off. Opting in adds labelled
  `ingestion` and `needs_review` summaries via `includeProcessing=true`, preserving
  filters in the URL. The choice is not stored as an account or browser preference.
  Drafts, withdrawn and merged records remain hidden. Processing cards have no private
  evidence, prices or configuration values; comparison and shortlist controls are disabled.
  Project titles, image areas and map previews open a media/detail dialog. It includes
  category filtering, thumbnails, previous/next images, full-size image links, layout
  and room measurements, amenities and project evidence, with loading/retry/empty states.
  The gallery API decides access, not the frontend sign-in state.
  `PROJECT_PUBLIC_PREVIEW_ENABLED` is temporarily true by default on the local debug
  backend: guests can view unreviewed project media/data, labelled as a temporary
  public preview. With the flag off, processing media is restricted to project
  editors/reviewers; public published galleries show approved media only. No raw
  documents, private buyer data, publication or write permissions are opened.
  Matches and shortlists remain published-only; withdrawn projects are omitted from snapshots.
- Project map pins show project names rather than prices or match scores; long names
  are truncated visually with the full name retained in the marker title.
  Pins use published coordinates or accepted processing-listing locations.
  In development public-preview mode, missing coordinates can use the latest saved
  unreviewed manual pin or matching Google route origin. These are explicitly labelled
  **provisional, not reviewed**, with dashed outlines. This does not approve or apply
  coordinates. Normal mode still requires published/reviewed coordinates.
  Projects without usable locations remain in the feed and are counted as unmapped.
- Publication and MahaRERA verification are separate. Verified registry status is shown
  only for published listings, on cards and marker outlines, and can be filtered.
  Fact-based filters exclude processing summaries because their facts are not public.
  A saved or accepted location does not publish a project.
- Metro and railway layers come from `GET /api/v1/map-stations`; opening the map never
  performs Google lookups or route calculations. Popups identify source, service status,
  and evidence date when available. Unverified candidates have dashed markers and a toggle.
- The local candidate CSV defaults to
  `reailEstate_common/backend/config/mmr-metro-candidates.csv`.
  `PROJECT_MAP_STATION_CANDIDATES_FILE` can select another CSV or be empty to disable it.
  Required columns: `name,line,latitude,longitude`; optional `kind` is `metro` (default)
  or `railway`. These rows always remain unverified, are capped at 1,000 rows/1 MB,
  and never enter the authority routing catalogue. Identical rows are deduplicated;
  conflicting coordinates for the same name/line/kind are withheld.
- Configured authority metro records retain their documented status. Accepted saved
  station references belonging to published projects can add railway/metro markers,
  but do not establish operating status. No unpublished project references are exposed.
- Stations remain visible when the published project catalogue is empty or unavailable.
  Complete the [workspace admin publication TODO](../../docs/10-mvp-backlog-and-traceability.md#workspace-admin-todo)
  to make reviewed projects available in Browse; opening the map never publishes them.
- OpenStreetMap tiles require network access and retain attribution. Tile failures
  show a retry state without blocking the feed or project markers. Review the
  [tile usage policy](https://operations.osmfoundation.org/policies/tiles/) before
  deployment; use a suitable provider for traffic that exceeds public-service limits.
- The catalogue API currently has no approved project-image field. Cards explicitly
  show missing images rather than unrelated stock photographs.
- Prices and score components retain unknown states. Scores come from the backend;
  the UI never substitutes a fake score or silently relaxes hard constraints.
- Daily destinations accept Google Maps search, Google Maps pin/share links or Plus Codes
  (include a locality for short codes), and direct latitude/longitude. Google lookups
  require sign-in and explicit action; search requires backend provider configuration.
  Missing providers or failed lookups produce errors, not approximate or fabricated pins.
  Legacy locality-only entries remain supported and are shown as unmapped.
  Commute remains unknown without backend routing metrics. Locality preferences and
  additional needs are saved but are not filters in the current scoring algorithm.
- Open **Shortlist -> Daily commute** to add destinations, select a saved requirement
  profile, or save the current draft with consent. Loading a profile replaces the
  entire draft and requires confirmation when it has unsaved edits. Submitted profiles
  open as new drafts. Saving destinations does not require completing home/budget
  sections, and uses the existing versioned private-profile API.
  Numbered destination markers appear only on Shortlist, never Browse, Discover or
  matches. They are black against white project pins in light mode and white against
  black project pins with the system dark colour scheme. Both are included in map fitting.

## Privacy and Concurrency

Budgets, destinations and notes stay in React memory until the user signs in and
consents to saving. Only home preferences, weights and an owner-keyed opaque latest
match URL are stored in session storage. No profile payloads or credentials are
stored in browser globals, local storage, logs or test traces.

Project and station data come from same-origin public APIs. OpenStreetMap receives
the client's IP address, browser information and requested map tile area, which may
reflect areas around destinations, but not destination labels or requirement profiles.
Explicit destination searches/links are sent to Google through authenticated POST
requests; they are not placed in request URLs. No provider keys reach the browser.
The map loads no Google frames or scripts. CSP blocks all frames with `frame-src 'none'`.

Consented, valid drafts autosave after one second. Explicit Save & exit can save
partially completed drafts accepted by the backend. Writes use `If-Match`; a conflict
offers the server version or a new profile, never an overwrite. Conflict choices
are available on Shortlist too. Submitted versions are immutable. Submission retries
retain their idempotency key unless the draft changes.
Signing out or detecting session expiry clears private state and cancels in-flight
requests. Refreshing an unsaved guest draft discards its sensitive fields.

## Verification

Run from the workspace root:

```powershell
npm --prefix reailEstate/reailEstate_common/frontend test
npm --prefix reailEstate/reailEstate_common/frontend run lint
npm --prefix reailEstate/reailEstate_common/frontend run build
npm exec --prefix reailEstate/reailEstate_common/frontend -- playwright install chromium
npm --prefix reailEstate/reailEstate_common/frontend run test:e2e
```

Browser tests start a separate temporary server on port 5175. Every API request is
intercepted with synthetic fixtures; no live profiles, shortlists or project records
are created. Tiles deliberately fail to exercise the retry fallback. Tests cover the
combined project/station map without an embed, an empty published catalogue, station
layers, schematic line separation and filtering, guest-to-score-sheet flow, version conflicts, session expiry, map/feed selection,
comparison, persisted shortlists and 1440/390/320px layouts. They assert no uncaught
browser errors or CSP violations. Screenshots are in ignored `test-results/`.
Real OpenStreetMap rendering also requires a network-enabled browser check.

To run the same browser suite against a production build:

```powershell
$env:BUYER_TEST_BUILD = 'preview'
try { npm --prefix reailEstate/reailEstate_common/frontend run test:e2e }
finally { Remove-Item Env:BUYER_TEST_BUILD -ErrorAction SilentlyContinue }
```

For local production-build inspection, `npm --prefix reailEstate/reailEstate_common/frontend run preview`
serves http://127.0.0.1:5174/. Vite preview is not a production application server.
Deployment must serve the SPA fallback, reverse-proxy `/api` on the same origin,
enforce HTTPS and secure backend session settings, and apply the CSP and other
response headers defined in [vite.config.ts](vite.config.ts). Fonts are bundled;
OpenStreetMap is the allowed external tile origin, and frames are blocked.
Preserve these directives in deployment.
