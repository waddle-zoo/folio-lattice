# Engineering prior art

Date: 2026-09-05

The goal is not to copy a large project's entire toolchain. It is to adopt the
small number of conventions that make a repository legible and trustworthy to
an enterprise engineering team.

## References

- [FastAPI's `pyproject.toml`](https://github.com/fastapi/fastapi/blob/master/pyproject.toml)
  keeps development, testing, documentation, and compatibility concerns
  explicit instead of hiding them in an install script.
- [Pydantic's `pyproject.toml`](https://github.com/pydantic/pydantic/blob/main/pyproject.toml)
  separates linting, type checking, testing, and build surfaces and publishes
  coverage configuration alongside the source package.
- [Ruff's CI and pre-commit integrations](https://docs.astral.sh/ruff/integrations/)
  provide one fast lint/format implementation that can run locally, in hooks,
  and in GitHub Actions.
- Hyperset's checked-in shape provides a local organizational precedent:
  `src`-equivalent package boundaries, `pyproject.toml` development groups,
  Ruff, pytest markers, Make targets, ADRs, and CI jobs that explain what each
  gate proves.

## Folio Lattice adoption

Folio Lattice adopts the proportionate subset:

- `src/folio_lattice` keeps import behavior honest and package boundaries clear.
- `uv.lock` makes the development and CI toolchain reproducible without adding
  runtime dependencies to the v0 service.
- `ruff check` and `ruff format --check` are explicit gates over `src/` and
  `tests/`; copied Gas Town skills are excluded as separate operations tooling.
- Pytest runs the existing unit, transport, and service-boundary tests with a
  visible coverage floor rather than treating test count as evidence.
- Mypy checks the typed product package in CI without making generated Gas Town
  tooling part of the product type surface.
- A second CI job builds and health-checks the Docker boundary.
- `CONTRIBUTING.md`, `SECURITY.md`, Dependabot, and adoption notes make the
  repository's operating expectations visible before an enterprise team has to
  reverse-engineer them.

A public release process, signed artifacts, and a compatibility policy remain
deliberate follow-ons. They should be added when the public contract and
integration surface are stable enough that the checks prove a real guarantee.
