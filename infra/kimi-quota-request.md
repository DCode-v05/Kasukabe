# Kimi K2.5 RPM quota increase — request draft

## The ask

| Field | Value |
|---|---|
| Service | Amazon Bedrock |
| Quota name | On-demand model inference requests per minute for Moonshot AI Kimi K2.5 |
| Quota code | `L-C1711F26` |
| Region | **ap-south-1 (Mumbai) only** |
| Current value | 100 requests/minute |
| Requested value | **500 requests/minute** |
| Account | 771650096076 |

Note: the tokens-per-minute quota (`L-B8D04761`, 100M TPM) is NOT the constraint and needs no change; peak usage is under 1.5M tokens/minute.

## Why the API path will not work

`Adjustable: False` on this quota (verified 2026-07-31 via `aws service-quotas list-service-quotas`), so `request-service-quota-increase` will be rejected. Submission paths, in order:

1. **Service Quotas console** (sometimes offers a request form even when the API says non-adjustable):
   https://ap-south-1.console.aws.amazon.com/servicequotas/home/services/bedrock/quotas — search "Kimi K2.5", try "Request increase at account level".
2. **AWS Support Center** → Create case → Account and billing (or "Looking for service limit increases?") → paste the case text below.
3. **AWS Sales / account team** — same channel Bedrock errors reference: https://aws.amazon.com/contact-us/sales-support/

## Case text (paste as-is)

> **Subject:** Bedrock on-demand RPM increase for Moonshot AI Kimi K2.5 in ap-south-1 (quota L-C1711F26): 100 → 500
>
> We are running a bounded multi-agent research simulation on Amazon Bedrock in ap-south-1: ~250 concurrent LLM agents in an agent-based social/economic simulation (internal research at September, info@september.wtf). All inference uses a single model, Moonshot AI Kimi K2.5, invoked on-demand at the flex service tier.
>
> Our workload is smooth and centrally brokered: a single dispatcher enforces a client-side token-bucket rate limit, exponential backoff, and a bounded queue — there is no thundering-herd pattern. At the current 100 RPM ceiling the simulation completes in ~14 wall-clock hours; the requested 500 RPM shortens the experiment window to ~3 hours without changing total request volume (~280k requests over the run) or token volume (peak under 1.5M tokens/minute, far below the existing 100M TPM quota).
>
> The workload is finite (single research campaign plus a possible follow-up at larger scale), fully retry-safe, and non-latency-sensitive; we are requesting throughput headroom only, not reserved capacity. Happy to share more workload detail if useful.

## If refused

The design already works at 100 RPM (event-driven scheduling, ~2.2 hr per game-day, ~14 hr total run). The increase is an optimization, not a dependency. Do NOT redesign around a refusal.
