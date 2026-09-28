# CMN-C2-686 — Resume Batch Screening & Classification

> **Category**: Cat 2 (domain workflow — business logic in src/)
> **Industry**: Common (industry-agnostic)

## Overview

Screens a batch of resumes against one set of job requirement criteria and classifies each
candidate. An HR team supplies a job description together with a batch of resumes; the agent works
through the batch one candidate at a time and returns a Pass / Review / Reject classification for
each, with a score and written reasoning, alongside a ranked shortlist and the score distribution
across the batch.

Protected attributes are redacted from every resume before the screening loop starts, rather than
lazily inside it, so the scoring step can only ever see job-relevant content. The batch is validated
up front — job description present, batch non-empty, size within a configured ceiling — and personal
identifiers are stripped from agent state before the result is returned. The run is a bounded
think-act loop: the agent fetches the next candidate and scores it until the batch is exhausted or
the configured iteration limit is reached, so a batch cannot run unbounded.

Example: *"Screen these 200 resumes against this job spec and classify into Pass / Review / Reject
with rationale."* → a per-candidate classification with score and reasoning, a ranked shortlist, and
the score distribution across the batch.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent raises
`PlatformRequired` during graph compile / start-up preflight rather than starting in a partially
working state. This is intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
```

See `docs/` for the design spec and test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.

