# Remaining work
## manual
1) manual TODOs fix stations on M1 and m3 lines something seems wrong
2) add local train stations


## General
1) review current project and tell me all the changes needed to make this a truly cloud native design
2) verify if Document storage pattern is optimal for s3
3) Store the layout’s coordinates on the original PDF page, so we can render a sharper image directly from the PDF instead of zooming into an existing PNG and making it blurry.
    Ans) The inefficient part is [`move_document()` (line 36)](E:/Projects/Workspace-publish/reailEstate/reailEstate_common/backend/platform_api/artifact_storage.py:36). It reads the document, writes and verifies another copy, updates its database key, then deletes the previous copy. Reprocessing can move it back. For general-purpose S3 buckets, changing a key requires copying and deleting the object. AWS copy/move documentation
    Cloud configuration redirects this storage to /workspace/legacy-media. That name is misleading: it receives current project pictures too. My earlier description of it as historical storage was incomplete.
    For migration, both storage paths must change:
    - Changing only Django default storage moves cover pictures to S3, while documents remain filesystem-backed.
    - Changing only RoutedStorage moves documents, while cover pictures remain local.
    Configure both consistently, migrate and checksum all three roots, and verify application reads before retiring the filesystem locations.
4) remove long term storage dependency from reailEstate\reailEstate_common\backend\media , that is scratchpad for local storage only 
  a) on local all processed data must be stored in output\media
  b) on cloud it should be similiarly in EBS/EFS and output should be in S3
  c) ideally we should remove it al together 

## Code Standards
1) Merge Builder and Admin New Project paths as much as possible
    a) On document Upload -> Current admin limitation: the separate admin service does not expose these three upload endpoints, and EvidenceDocumentAdmin is not a file-upload form. Admins have review, retry, source-page and media tools, but the complete document-upload UI lives in the builder portal. A staff account using that portal still needs builder membership.
    b) same for cropping of layouts
    c) same for builder and admin location, ammenities,crop_car,external_sources, layout, layout_floor_plan
2) lets create a directory to keep all floor_plan and layout related tasks in it 
3) are reailEstate/reailEstate_common/backend/platform_api/migrations/*.py needed? cant we assume a new (empty db) for future clients
4) Decouple artifact storage from filesystem paths
5) add licence
6) all git tasks should be yaml
7) remove unnecessary scripts, also add new ones for linux
8) update readme.md
9) for each feature add feature.md/flow.feature.md
10) reailestate_common acts as an active service for all 3 portals, instead it should only contain function calls, it is supposed to be a python package(library) in future 


## Future Updates
1) Allow Admin to pass ownership of a project to builders (i.e this simulates onboarding help eg Dosti West county to Org Dosti)
2) reailEstate/reailEstate_common/backend/platform_api/static/platform_api/** and reailEstate/reailEstate_common/backend/platform_api/templates/admin/platform_api/** can we move it to common frontend instead
3) lets update RagFactExtractor to use a langchain method instead of current Regex method i.e Extend or replace with evidence-grounded, schema-bound model extraction.
4) on document upload path lets add some document verifying routine i.e  in a sandbox: document -> worker -> verified -> document is commited to files (Low Priority for now)
5) Floor-plan page selection: Augment with OCR and multimodal retrieval; retain broad enough coverage to avoid missing layouts


## Scaling/ Cloud
1) for our workers and soft queues , i am planning on moving to kafka (kafka cli for local), and instances for aws/azure deployments , is this  a good idea
2) Configure a shared cache -> Admin drafts currently use process-local cache. A draft created by one Gunicorn process can disappear when the next request reaches another process. Throttles also vary by process.
3) Stream uploads -> Uploads currently buffer up to 100 MiB while holding a database transaction and row lock. Stage bytes first, then finalize with a short transaction.
4) Add database availability and recovery protection -> Production needs an appropriate HA configuration, deletion protection, backup/PITR policy, and verified database-plus-evidence restoration.
5) Make migrations compatible with rolling releases -> Use separate migration jobs and staged schema changes so old and new application versions can coexist safely.
6) Give each service meaningful health checks

### Deployment
1) Separate buyer, builder, admin, and worker deployments
2) Make job claiming support multiple workers -> The dispatcher deliberately rejects a second instance. Replace the global singleton with durable claims, renewable leases, and protection against stale workers committing results.

## AuthN/Z
1) currenty all users are written to the same table, should we update our AuthN/Z protocols areound this
2) Currently we are leaky on the passwords, we are using username/pwd we should move onto OpenID/HeaderAuth
3) Remove extraction from HTTP handlers -> One builder endpoint executes queued processing inside the request. Return an operation immediately and let workers execute it.
4) Separate identities and privileges\