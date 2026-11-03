# Deployment status

Live host, account, resource and personal connection details are not stored here.
Use GitHub environment configuration and the Actions run/deployment history.

The supported release procedure is [the GitHub Actions guide](README.md). A source
change is not a completed deployment: verify the build and deploy jobs, readiness,
application pages and authorized media access for the selected environment. Keep
live operational records in your private operations system.

Record the reviewed commit, selected GitHub environment, `preview`/`https` mode,
`demo` visibility choice and successful Actions run in those private records.
`demo` defaults to true; eligible unreviewed previews can therefore be visible to
anonymous viewers. Pending migrations stop the workflow and must be handled in a
separately reviewed procedure. Processing remains off after a successful release.
Readiness/login-page checks do not prove a credential-based sign-in, successful
document processing or a restore/rollback rehearsal. Failed startup has no automatic
rollback. Use the [SSH guide](authentication-guide.md) for connection failures.
