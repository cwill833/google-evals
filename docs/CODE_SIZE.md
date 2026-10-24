# Source size after evidence separation

Physical lines include comments and blanks; dependencies, lockfiles, docs and generated evidence are excluded.

| Area | Files | Lines |
|---|---:|---:|
| Reusable evaluation core | 14 | 2,872 |
| Example agents, assertions and legacy agent | 13 | 412 |
| Verification, deployment and support scripts | 26 | 2,584 |
| Hosted server and container configuration | 7 | 58 |
| Tests, synthetic fixtures and controlled fault agent | 29 | 3,879 |
| Evaluation datasets, configs, policies and freezes | 51 | 3,329 |

Implementation and supporting scripts: **5,926 lines**. With tests: **9,805**.
Including evaluation data/configuration: **13,134**. Reusable core: **2,872 lines**.

The PR/source package contains 171 files, including documentation, datasets and the
dependency lockfile. Generated runs, reports and archive verification sidecars are
excluded. All 5,010 historical evidence files remain local, unchanged. Tests construct
small synthetic fixtures in temporary directories; historical native replay is optional
and takes an explicitly supplied archive. scripts/count_lines.py retains its historical
finance-only scope.

The Google UI research addition is a curated exception: three small JSON evidence
files (913 lines), a research note and a 128-line opt-in reproduction helper. The
JSON evidence is documentation data and is excluded from the implementation totals.
The larger historical archive remains local; see [the scope and redactions](GOOGLE_UI_RESEARCH.md).
