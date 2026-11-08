"""Secret detection rules — regex patterns for known credential formats.

Patterns ported from the reference scanner at
`services/teamMemorySync/secretScanner.ts` (the reference is a curated subset
of gitleaks rules: https://github.com/gitleaks/gitleaks, MIT license). Only
rules with distinctive prefixes and near-zero false-positive rates are
included — generic keyword-context rules are intentionally omitted.

Rule IDs mirror gitleaks kebab-case IDs so analytics and comparisons stay
aligned with the upstream project. Labels are human-readable and are the
ONLY identifier surfaced in error messages or logs. Matched text is never
returned by the scanner (see secret_scanner.py).

Both Anthropic key prefixes (`sk-ant-api03-` and `sk-ant-admin01-`) are
assembled at runtime via string concatenation so this source file does not
contain the literal scannable substrings. This prevents our own scanner —
or external scanners scanning this repo — from flagging our rule source.

Trailing boundary pattern (`TAIL`): many gitleaks rules include a trailing
`(?:[\\x60'"\\s;]|\\\\[nr]|$)` consuming boundary so the secret value is
delimited by whitespace, quotes, backticks, semicolons, or end-of-string.
The capture group is the secret value itself; the consumed boundary char
is preserved on redaction (see SecretScanner.redact).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SecretRule:
    id: str
    label: str
    pattern: str
    flags: int = 0


ANT_PREFIX = "-".join(["sk", "ant", "api"]) + "03-"
ANT_ADMIN_PREFIX = "-".join(["sk", "ant", "admin"]) + "01-"


TAIL = r"""(?:[\x60'"\s;]|\\[nr]|$)"""


RULES: tuple[SecretRule, ...] = (
    SecretRule(
        id="aws-access-token",
        label="AWS Access Token",
        pattern=r"\b((?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16})\b",
    ),
    SecretRule(
        id="gcp-api-key",
        label="GCP API Key",
        pattern=r"\b(AIza[\w\-]{35})" + TAIL,
    ),
    SecretRule(
        id="azure-ad-client-secret",
        label="Azure AD Client Secret",
        pattern=(
            r"""(?:^|[\\'"\x60\s>=:(,)])"""
            r"([a-zA-Z0-9_~.]{3}\dQ~[a-zA-Z0-9_~.\-]{31,34})"
            r"""(?:$|[\\'"\x60\s<),])"""
        ),
    ),
    SecretRule(
        id="digitalocean-pat",
        label="DigitalOcean PAT",
        pattern=r"\b(dop_v1_[a-f0-9]{64})" + TAIL,
    ),
    SecretRule(
        id="digitalocean-access-token",
        label="DigitalOcean Access Token",
        pattern=r"\b(doo_v1_[a-f0-9]{64})" + TAIL,
    ),
    SecretRule(
        id="anthropic-api-key",
        label="Anthropic API Key",
        pattern=(r"\b(" + re.escape(ANT_PREFIX) + r"[a-zA-Z0-9_\-]{93}AA)" + TAIL),
    ),
    SecretRule(
        id="anthropic-admin-api-key",
        label="Anthropic Admin API Key",
        pattern=(r"\b(" + re.escape(ANT_ADMIN_PREFIX) + r"[a-zA-Z0-9_\-]{93}AA)" + TAIL),
    ),
    SecretRule(
        id="openai-api-key",
        label="OpenAI API Key",
        pattern=(
            r"\b(sk-(?:proj|svcacct|admin)-"
            r"(?:[A-Za-z0-9_\-]{74}|[A-Za-z0-9_\-]{58})"
            r"T3BlbkFJ"
            r"(?:[A-Za-z0-9_\-]{74}|[A-Za-z0-9_\-]{58})\b"
            r"|sk-[a-zA-Z0-9]{20}T3BlbkFJ[a-zA-Z0-9]{20})" + TAIL
        ),
    ),
    SecretRule(
        id="huggingface-access-token",
        label="HuggingFace Access Token",
        pattern=r"\b(hf_[a-zA-Z]{34})" + TAIL,
    ),
    SecretRule(
        id="github-pat",
        label="GitHub PAT",
        pattern=r"ghp_[0-9a-zA-Z]{36}",
    ),
    SecretRule(
        id="github-fine-grained-pat",
        label="GitHub Fine-Grained PAT",
        pattern=r"github_pat_\w{82}",
    ),
    SecretRule(
        id="github-app-token",
        label="GitHub App Token",
        pattern=r"(?:ghu|ghs)_[0-9a-zA-Z]{36}",
    ),
    SecretRule(
        id="github-oauth",
        label="GitHub OAuth Token",
        pattern=r"gho_[0-9a-zA-Z]{36}",
    ),
    SecretRule(
        id="github-refresh-token",
        label="GitHub Refresh Token",
        pattern=r"ghr_[0-9a-zA-Z]{36}",
    ),
    SecretRule(
        id="gitlab-pat",
        label="GitLab PAT",
        pattern=r"glpat-[\w\-]{20}",
    ),
    SecretRule(
        id="gitlab-deploy-token",
        label="GitLab Deploy Token",
        pattern=r"gldt-[0-9a-zA-Z_\-]{20}",
    ),
    SecretRule(
        id="slack-bot-token",
        label="Slack Bot Token",
        pattern=r"xoxb-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9\-]*",
    ),
    SecretRule(
        id="slack-user-token",
        label="Slack User Token",
        pattern=r"xox[pe](?:-[0-9]{10,13}){3}-[a-zA-Z0-9\-]{28,34}",
    ),
    SecretRule(
        id="slack-app-token",
        label="Slack App Token",
        pattern=r"xapp-\d-[A-Z0-9]+-\d+-[a-z0-9]+",
        flags=re.IGNORECASE,
    ),
    SecretRule(
        id="twilio-api-key",
        label="Twilio API Key",
        pattern=r"SK[0-9a-fA-F]{32}",
    ),
    SecretRule(
        id="sendgrid-api-token",
        label="SendGrid API Token",
        pattern=r"\b(SG\.[a-zA-Z0-9=_\-.]{66})" + TAIL,
    ),
    SecretRule(
        id="npm-access-token",
        label="NPM Access Token",
        pattern=r"\b(npm_[a-zA-Z0-9]{36})" + TAIL,
    ),
    SecretRule(
        id="pypi-upload-token",
        label="PyPI Upload Token",
        pattern=r"pypi-AgEIcHlwaS5vcmc[\w\-]{50,1000}",
    ),
    SecretRule(
        id="databricks-api-token",
        label="Databricks API Token",
        pattern=r"\b(dapi[a-f0-9]{32}(?:-\d)?)" + TAIL,
    ),
    SecretRule(
        id="hashicorp-tf-api-token",
        label="HashiCorp TF API Token",
        pattern=r"[a-zA-Z0-9]{14}\.atlasv1\.[a-zA-Z0-9\-_=]{60,70}",
    ),
    SecretRule(
        id="pulumi-api-token",
        label="Pulumi API Token",
        pattern=r"\b(pul-[a-f0-9]{40})" + TAIL,
    ),
    SecretRule(
        id="postman-api-token",
        label="Postman API Token",
        pattern=r"\b(PMAK-[a-fA-F0-9]{24}-[a-fA-F0-9]{34})" + TAIL,
    ),
    SecretRule(
        id="grafana-api-key",
        label="Grafana API Key",
        pattern=r"\b(eyJrIjoi[A-Za-z0-9+/]{70,400}={0,3})" + TAIL,
    ),
    SecretRule(
        id="grafana-cloud-api-token",
        label="Grafana Cloud API Token",
        pattern=r"\b(glc_[A-Za-z0-9+/]{32,400}={0,3})" + TAIL,
    ),
    SecretRule(
        id="grafana-service-account-token",
        label="Grafana Service Account Token",
        pattern=r"\b(glsa_[A-Za-z0-9]{32}_[A-Fa-f0-9]{8})" + TAIL,
    ),
    SecretRule(
        id="sentry-user-token",
        label="Sentry User Token",
        pattern=r"\b(sntryu_[a-f0-9]{64})" + TAIL,
    ),
    SecretRule(
        id="sentry-org-token",
        label="Sentry Org Token",
        pattern=(
            r"\bsntrys_eyJpYXQiO[a-zA-Z0-9+/]{10,200}"
            r"(?:LCJyZWdpb25fdXJs|InJlZ2lvbl91cmwi|cmVnaW9uX3VybCI6)"
            r"[a-zA-Z0-9+/]{10,200}={0,2}_[a-zA-Z0-9+/]{43}"
        ),
    ),
    SecretRule(
        id="stripe-access-token",
        label="Stripe Access Token",
        pattern=r"\b((?:sk|rk)_(?:test|live|prod)_[a-zA-Z0-9]{10,99})" + TAIL,
    ),
    SecretRule(
        id="shopify-access-token",
        label="Shopify Access Token",
        pattern=r"shpat_[a-fA-F0-9]{32}",
    ),
    SecretRule(
        id="shopify-shared-secret",
        label="Shopify Shared Secret",
        pattern=r"shpss_[a-fA-F0-9]{32}",
    ),
    SecretRule(
        id="private-key",
        label="Private Key",
        pattern=(
            r"-----BEGIN[ A-Z0-9_\-]{0,100}PRIVATE KEY(?: BLOCK)?-----"
            r"[\s\S]{64,}?"
            r"-----END[ A-Z0-9_\-]{0,100}PRIVATE KEY(?: BLOCK)?-----"
        ),
        flags=re.IGNORECASE,
    ),
)


def assert_unique_ids() -> None:
    """Fail-fast sanity check: rule IDs must be unique (scanner dedupes on ID)."""
    seen: set[str] = set()
    for rule in RULES:
        if rule.id in seen:
            raise RuntimeError(f"Duplicate SecretRule id: {rule.id}")
        seen.add(rule.id)


assert_unique_ids()


def build_anthropic_sample(*, admin: bool = False, body_len: int = 93) -> str:
    """Test helper: build an Anthropic-key-shaped sample string.

    Returns a fabricated value whose shape matches the anthropic-api-key or
    anthropic-admin-api-key pattern when `body_len == 93`. Passing a smaller
    `body_len` produces a near-miss negative for regex rejection tests.
    Tests use this instead of importing the private `ANT_PREFIX` /
    `ANT_ADMIN_PREFIX` module constants.
    """
    prefix = ANT_ADMIN_PREFIX if admin else ANT_PREFIX
    return prefix + ("a" * body_len) + "AA"
