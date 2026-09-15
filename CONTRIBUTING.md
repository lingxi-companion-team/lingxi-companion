# Contributing to Lingxi Companion

## Branches

- `main`: stable and released code; do not push directly.
- `develop`: daily integration branch.
- `feature/*`: short-lived task branches created from the latest `develop`; delete them once merged.

## Pull requests

Every PR should include:

1. a concise change summary;
2. related task or issue information;
3. tests that were run;
4. notes about interface, dependency, model, or data changes.

Changes to `common/perception_types.py` require agreement from all three future maintainers and regression tests for the affected interfaces.

## Data and model files

Do not commit raw videos, personally identifiable information, local virtual environments, or large model binaries. Add reproducible download/conversion instructions and checksums instead.
