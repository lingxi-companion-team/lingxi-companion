# Contributing to Lingxi Companion

## Branches

- `main`: stable and released code; do not push directly.
- `develop`: daily integration branch.
- `dev-1`, `dev-2`, `dev-3`: reserved member branches. Their owners and responsibilities are intentionally undecided for now.
- `feature/*`: short-lived task branches created from the latest `develop`.

Until responsibilities are assigned, use a neutral branch name such as `feature/<topic>` rather than encoding a person or module into the branch name.

## Pull requests

Every PR should include:

1. a concise change summary;
2. related task or issue information;
3. tests that were run;
4. notes about interface, dependency, model, or data changes.

Changes to `common/perception_types.py` require agreement from all three future maintainers and regression tests for the affected interfaces.

## Data and model files

Do not commit raw videos, personally identifiable information, local virtual environments, or large model binaries. Add reproducible download/conversion instructions and checksums instead.
