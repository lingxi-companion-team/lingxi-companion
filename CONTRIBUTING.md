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

## Line endings

The repository pins line endings via `.gitattributes` (`* text=auto eol=lf`). Do not rely on your local `core.autocrlf` setting — the file in the repo is authoritative.

If you cloned before `.gitattributes` was added, your working copy may still hold CRLF files that Git reports as unchanged. Run this **once** to align it:

```bash
git add --renormalize .
git commit -m "chore: 按 .gitattributes 重新规范化行尾"
```

Note on scope: the index was already 100% LF when `.gitattributes` landed, so this commit changes no file contents — it only rewrites your local working copy. If the command stages nothing, your checkout is already correct and you need no further action.

Why this matters: without a repo-level rule, every fresh clone lands CRLF on a `core.autocrlf=true` machine, which shows up as dozens of phantom diffs and makes real changes hard to spot in review — especially once `feature/*` branches run in parallel.

## Data and model files

Do not commit raw videos, personally identifiable information, local virtual environments, or large model binaries. Add reproducible download/conversion instructions and checksums instead.
