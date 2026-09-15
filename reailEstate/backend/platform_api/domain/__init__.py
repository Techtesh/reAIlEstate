"""Pure leaf helpers consumed by models, policies and application services.

Keep this package initializer import-free so title formatting does not load the
image stack. Domain modules must not import ORM models, storage or policies.
"""