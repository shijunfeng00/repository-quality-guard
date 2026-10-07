# qg-example-profile

This directory is an executable reference Profile for Repository Quality Guard. The paths and type names are deliberately generic; copy the directory, rename the Profile, and replace them with contracts from your own repository.

`profile.json` demonstrates declarative policy: rule levels, rule disablement, non-blocking paths, test-baseline passthrough paths, a repository-owned `commit_policy`, and settings consumed by an extension.

`extension.py` demonstrates code-level plugin capability. It adds static-analysis contracts for a state object and a model adapter. This is the appropriate layer for project-specific AST, interface, relationship, or contract analysis that cannot be represented as JSON alone.

If a project only needs rule-level changes, rule disablement, path policy, or commit-message policy, `extension.py` is unnecessary; keep those decisions in `profile.json`. The example commit convention is only an example and may be replaced with the repository's own convention.
