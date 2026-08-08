"""Operator tooling that reads across deployments rather than serving one.

Modules here are deliberately outside ``ragstack.api``: they take a *set* of
configurations as input (every deployment sharing a backend, including stopped
ones) where the API takes exactly one. Nothing in here is imported by the
serving path.
"""
