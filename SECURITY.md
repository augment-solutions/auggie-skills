# Security Policy

Thank you for helping keep `auggie-skills` and its users safe.

## Reporting a Vulnerability

If you believe you've found a security vulnerability in this repository,
please **do not** open a public GitHub issue, pull request, or discussion.

Use one of the following private channels instead:

1. **GitHub Private Vulnerability Reporting** (preferred):
   <https://github.com/augment-solutions/auggie-skills/security/advisories/new>

2. **Email**: <security@augmentcode.com>

When reporting, please include as much of the following as you can:

- A description of the vulnerability and its potential impact.
- Steps to reproduce, or a minimal proof-of-concept.
- The affected file(s), commit SHA, or release tag.
- Any suggested remediation, if you have one.
- Whether you would like to be credited in the advisory.

We will acknowledge your report within **5 business days** and aim to
provide a more detailed response within **10 business days**, including
an assessment of the issue and an expected timeline for a fix.

## Scope

In scope:

- Code in this repository (skills, scripts, prompts, helpers).
- Default configurations shipped with the skills that could lead to
  credential exposure, command injection, path traversal, or unsafe
  network behavior when the skills are run as documented.

Out of scope:

- Vulnerabilities in third-party services the skills interact with
  (GitHub, the Augment Auggie CLI, model providers). Please report
  those directly to the corresponding vendor.
- Findings that require the operator to deliberately misconfigure the
  skill in ways the documentation warns against.
- Issues that depend on a compromised local machine, malicious git
  remote, or social engineering of the operator.

## Disclosure

We follow a coordinated disclosure model. We ask that you give us a
reasonable amount of time to investigate and patch a reported issue
before any public disclosure. Once a fix is available, we will publish
a GitHub Security Advisory and credit the reporter (unless anonymity
is requested).

## Supported Versions

Only the `main` branch and the most recent tagged release receive
security updates. Older tags will not be patched; please upgrade.
