"""Shared application policies; services and UI depend on these, not vice versa.

Keep imports explicit at the leaf module. Policies may query models but must not
depend on cart persistence, evaluation orchestration or admin views.
"""