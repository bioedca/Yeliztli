# Security Policy

Yeliztli is a **privacy-first, local-only** personal-genomics platform. It runs
entirely on `localhost`, does no cloud processing, and sends **no outbound
variant data** — your genotypes never leave your machine. Security reports that
touch that guarantee are taken especially seriously.

## Supported versions

Yeliztli is pre-1.0 and ships continuously from `main`. Security fixes are
applied to the latest released version only.

| Version | Supported |
| ------- | --------- |
| latest `0.2.x` (current) | ✅ |
| older | ❌ — please update first |

## Reporting a vulnerability

**Please do not open a public issue for a security vulnerability.**

Report it privately through GitHub's private vulnerability reporting:

- Go to the repository's **[Security → Advisories → Report a vulnerability](https://github.com/bioedca/Yeliztli/security/advisories/new)**, or
- Email **bioedca@gmail.com** with the details below.

Include, as far as you can:

- A description of the issue and its impact.
- Steps to reproduce, or a proof of concept.
- Affected version / commit and platform (native, Docker, WSL2).
- Any suggested mitigation.

> ⚠️ Do **not** include your own raw genotype data or genome file in a report.
> If a reproduction needs genomic input, use a small synthetic/test fixture.

## What to expect

- **Acknowledgement** within a few days.
- An assessment of severity and affected versions, kept confidential while we
  work on a fix.
- **Coordinated disclosure**: we'll agree on a disclosure timeline with you and
  credit you in the release notes unless you prefer to remain anonymous.

## Scope — what we especially care about

Because the platform's core promise is that genomic data stays local, these
classes of issue are high priority:

- Any path that would cause **variant/genotype data to leave the machine**
  (unexpected outbound requests, telemetry, logging of genomic content).
- **Binding to a non-loopback interface** or otherwise exposing the local API
  beyond `localhost` without explicit user action.
- Auth/session weaknesses in the local app, path traversal, SSRF, or injection
  in the ingestion/annotation paths.
- Supply-chain risks in bundled reference data or dependencies.

The project already carries automated guards for the local-only posture (see
`tests/backend/test_security_audit.py`: loopback binding, no outbound variant
data, no telemetry) and CodeQL scanning (`security-extended`). A report that
defeats one of those guarantees is exactly what this policy is for.

## Out of scope

- Findings that require an attacker to already have local shell/file access to
  the user's machine (the threat model assumes the local machine is trusted).
- The documented, non-genomic outbound connections (e.g. the app-version check)
  — see [Privacy & data handling](docs/privacy.md).
