# AWS deployment

AWS infrastructure is intentionally not part of the current Azure setup.

Future AWS work should preserve the provider-neutral contract in
[`../README.md`](../README.md), use its own state and credentials, and map the
gateway, co-located services, PostgreSQL, and shared media storage to AWS
services without changing application ownership boundaries.
