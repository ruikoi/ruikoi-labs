# New project template

English | [简体中文](README.zh-CN.md)

Copy this directory to `projects/<project-name>/` and document:

1. Goals, a brief implementation overview, source repositories, and exact commits rather than floating branch names alone.
2. Supported Linux/macOS hosts and host dependencies.
3. `build.sh`, `run.sh`, and `test.sh`, including required input artifacts.
4. Success criteria for smoke tests, full applications, and optional benchmarks.
5. Implemented features, unvalidated features, and unsupported scope.
6. Actual screenshots or architecture diagrams and their sources. Clearly label log-derived output figures as excerpts and distinguish functional from RTL validation.
7. English `README.md` and Chinese `README.zh-CN.md`, linked to each other with matching commands and support statements.

Keep source changes in the corresponding fork and outputs in ignored work directories. Do not commit machine addresses, user-specific paths, SSH configuration, or credentials.
