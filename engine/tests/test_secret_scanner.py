"""Unit tests for src/security secret scanner, rules, and write guard.

Covers:
- Each of the 36 rules matches a canonical positive example and rejects a
  near-miss negative.
- Error messages never echo matched text (label-only exposure invariant).
- Per-rule deduplication: N occurrences of the same rule produce one match.
- Multiline private key detection across newlines (with the tightened
  body-length + matching END marker requirement).
- Empty / None / non-str input is a no-op.
- Lazy regex compilation — first `scan()` is the point of compilation.
- `redact()` replaces captured secret values with `[REDACTED]` and preserves
  surrounding boundary characters for rules that use the trailing-boundary
  pattern.
- `check_content_for_secrets` and `guard_content` entrypoints.
"""

from __future__ import annotations

import re

import pytest

from src.security.secret_rules import RULES, SecretRule, build_anthropic_sample
from src.security.secret_scanner import (
    SecretDetectedError,
    SecretScanner,
    get_scanner,
)
from src.security.write_guard import check_content_for_secrets, guard_content


ANT_SAMPLE = build_anthropic_sample()
ANT_ADMIN_SAMPLE = build_anthropic_sample(admin=True)

OPENAI_SAMPLE = "sk-proj-" + ("A" * 74) + "T3BlbkFJ" + ("A" * 74)

SENTRY_ORG_SAMPLE = (
    "sntrys_eyJpYXQiO" + ("A" * 20) + "LCJyZWdpb25fdXJs" + ("A" * 20) + "==" + "_" + ("A" * 43)
)

AZURE_SAMPLE = "abc0Q~" + ("a" * 31)

HC_SAMPLE = "abcdefghij1234" + ".atlasv1." + ("a" * 62)

GRAFANA_API_SAMPLE = "eyJrIjoi" + ("a" * 80)

GRAFANA_SA_SAMPLE = "glsa_" + ("a" * 32) + "_" + "deadbeef"

PRIVATE_KEY_SAMPLE = (
    "-----BEGIN RSA PRIVATE KEY-----\n" + ("a" * 64) + "\n-----END RSA PRIVATE KEY-----"
)


CANONICAL_POSITIVES: dict[str, str] = {
    "aws-access-token": "AKIAIOSFODNN7EXAMPLE",
    "gcp-api-key": "AIza" + ("a" * 35),
    "azure-ad-client-secret": AZURE_SAMPLE,
    "digitalocean-pat": "dop_v1_" + ("a" * 64),
    "digitalocean-access-token": "doo_v1_" + ("a" * 64),
    "anthropic-api-key": ANT_SAMPLE,
    "anthropic-admin-api-key": ANT_ADMIN_SAMPLE,
    "openai-api-key": OPENAI_SAMPLE,
    "huggingface-access-token": "hf_" + ("a" * 34),
    "github-pat": "ghp_" + ("a" * 36),
    "github-fine-grained-pat": "github_pat_" + ("a" * 82),
    "github-app-token": "ghu_" + ("a" * 36),
    "github-oauth": "gho_" + ("a" * 36),
    "github-refresh-token": "ghr_" + ("a" * 36),
    "gitlab-pat": "glpat-" + ("a" * 20),
    "gitlab-deploy-token": "gldt-" + ("a" * 20),
    "slack-bot-token": "xoxb-1234567890-1234567890-abcdefg",
    "slack-user-token": "xoxp-1234567890-1234567890-1234567890-" + ("a" * 30),
    "slack-app-token": "xapp-1-ABCDEFG-12345-abcdefg",
    "twilio-api-key": "SK" + ("a" * 32),
    "sendgrid-api-token": "SG." + ("a" * 66),
    "npm-access-token": "npm_" + ("a" * 36),
    "pypi-upload-token": "pypi-AgEIcHlwaS5vcmc" + ("a" * 60),
    "databricks-api-token": "dapi" + ("a" * 32),
    "hashicorp-tf-api-token": HC_SAMPLE,
    "pulumi-api-token": "pul-" + ("a" * 40),
    "postman-api-token": "PMAK-" + ("a" * 24) + "-" + ("a" * 34),
    "grafana-api-key": GRAFANA_API_SAMPLE,
    "grafana-cloud-api-token": "glc_" + ("a" * 60),
    "grafana-service-account-token": GRAFANA_SA_SAMPLE,
    "sentry-user-token": "sntryu_" + ("a" * 64),
    "sentry-org-token": SENTRY_ORG_SAMPLE,
    "stripe-access-token": "sk_live_" + ("a" * 24),
    "shopify-access-token": "shpat_" + ("a" * 32),
    "shopify-shared-secret": "shpss_" + ("a" * 32),
    "private-key": PRIVATE_KEY_SAMPLE,
}

CANONICAL_NEGATIVES: dict[str, str] = {
    "aws-access-token": "AKIA1234",
    "gcp-api-key": "AIza" + ("a" * 34),
    "azure-ad-client-secret": "abc0Q~" + ("a" * 30),
    "digitalocean-pat": "dop_v1_" + ("a" * 63),
    "digitalocean-access-token": "doo_v1_" + ("a" * 63),
    "anthropic-api-key": build_anthropic_sample(body_len=92),
    "anthropic-admin-api-key": build_anthropic_sample(admin=True, body_len=92),
    "openai-api-key": "sk-proj-" + ("A" * 60),
    "huggingface-access-token": "hf_" + ("a" * 33),
    "github-pat": "ghp_" + ("a" * 35),
    "github-fine-grained-pat": "github_pat_" + ("a" * 81),
    "github-app-token": "ghx_" + ("a" * 36),
    "github-oauth": "gho_" + ("a" * 35),
    "github-refresh-token": "ghr_" + ("a" * 35),
    "gitlab-pat": "glpat-" + ("a" * 19),
    "gitlab-deploy-token": "gldt-" + ("a" * 19),
    "slack-bot-token": "xoxb-abc",
    "slack-user-token": "xoxa-1234567890-1234567890-1234567890-" + ("a" * 30),
    "slack-app-token": "xapp-a-A-1-b",
    "twilio-api-key": "SK" + ("a" * 31),
    "sendgrid-api-token": "SG." + ("a" * 60),
    "npm-access-token": "npm_" + ("a" * 35),
    "pypi-upload-token": "pypi-WRONG" + ("a" * 60),
    "databricks-api-token": "dapi" + ("a" * 31),
    "hashicorp-tf-api-token": "abcdefghij1234.other.x" + ("a" * 60),
    "pulumi-api-token": "pul-" + ("a" * 39),
    "postman-api-token": "PMAK-" + ("a" * 23) + "-" + ("a" * 34),
    "grafana-api-key": "eyJrIjoi" + ("a" * 65),
    "grafana-cloud-api-token": "glc_" + ("a" * 30),
    "grafana-service-account-token": "glsa_" + ("a" * 32) + "_deadbee",
    "sentry-user-token": "sntryu_" + ("a" * 63),
    "sentry-org-token": "sntrys_WRONG"
    + ("A" * 50)
    + "LCJyZWdpb25fdXJs"
    + ("A" * 20)
    + "_"
    + ("A" * 43),
    "stripe-access-token": "sk_unknown_" + ("a" * 20),
    "shopify-access-token": "shpat_" + ("a" * 31),
    "shopify-shared-secret": "shpss_" + ("a" * 31),
    "private-key": ("-----BEGIN RSA PRIVATE KEY-----\nshort\n-----END RSA PRIVATE KEY-----"),
}


def all_rule_ids() -> list[str]:
    return [rule.id for rule in RULES]


@pytest.mark.parametrize("rule_id", all_rule_ids())
def test_each_rule_matches_canonical_positive(rule_id: str) -> None:
    sample = CANONICAL_POSITIVES[rule_id]
    scanner = SecretScanner()
    matches = scanner.scan(f"prefix text {sample} trailing text")
    ids = [m.rule_id for m in matches]
    assert rule_id in ids, (
        f"Rule {rule_id!r} failed to match its canonical example. Got matches: {ids}"
    )


@pytest.mark.parametrize("rule_id", all_rule_ids())
def test_each_rule_rejects_canonical_negative(rule_id: str) -> None:
    negative = CANONICAL_NEGATIVES[rule_id]
    scanner = SecretScanner()
    matches = scanner.scan(f"prefix text {negative} trailing text")
    ids = [m.rule_id for m in matches]
    assert rule_id not in ids, f"Rule {rule_id!r} incorrectly matched its near-miss negative"


def test_clean_text_produces_no_matches() -> None:
    scanner = SecretScanner()
    assert scanner.scan("The quick brown fox jumps over the lazy dog.") == []
    assert scanner.scan("pip install requests && echo done") == []
    assert scanner.scan("Lorem ipsum dolor sit amet, consectetur adipiscing.") == []


def test_empty_and_none_content_return_empty() -> None:
    scanner = SecretScanner()
    assert scanner.scan("") == []
    assert scanner.scan(None) == []
    assert scanner.scan(b"not a str") == []  # type: ignore[arg-type]


def test_rules_catalog_is_36() -> None:
    """Sanity check that the catalog has the expected reference parity."""
    assert len(RULES) == 36
    for rule in RULES:
        assert isinstance(rule, SecretRule)
        assert rule.id and rule.label and rule.pattern


def test_per_rule_deduplication() -> None:
    """Three distinct AWS keys in one blob → one SecretMatch for aws-access-token."""
    scanner = SecretScanner()
    blob = (
        "first key AKIAIOSFODNN7EXAMPLE "
        "second key AKIAJBCDEFGHIJKL2345 "
        "third key AKIA3456789ABCDEFGHI"
    )
    matches = scanner.scan(blob)
    aws_matches = [m for m in matches if m.rule_id == "aws-access-token"]
    assert len(aws_matches) == 1, f"Expected 1 deduped match, got {len(aws_matches)}"


def test_mixed_secrets_each_report_once() -> None:
    scanner = SecretScanner()
    blob = (
        "a "
        + CANONICAL_POSITIVES["aws-access-token"]
        + " b "
        + CANONICAL_POSITIVES["github-pat"]
        + " c "
        + CANONICAL_POSITIVES["github-pat"]
        + " d "
        + CANONICAL_POSITIVES["huggingface-access-token"]
        + " e "
    )
    matches = scanner.scan(blob)
    ids = sorted(m.rule_id for m in matches)
    assert ids == ["aws-access-token", "github-pat", "huggingface-access-token"]


def test_scan_result_never_contains_matched_text() -> None:
    scanner = SecretScanner()
    secret = CANONICAL_POSITIVES["github-pat"]
    matches = scanner.scan(f"leaked token: {secret} ")
    assert len(matches) == 1
    m = matches[0]
    assert secret not in m.rule_id
    assert secret not in m.label
    for field in m._fields:
        value = getattr(m, field)
        assert secret not in str(value)


def test_secret_detected_error_never_contains_matched_text() -> None:
    secret = CANONICAL_POSITIVES["aws-access-token"]
    content = f"database password is {secret} please save"
    try:
        guard_content(content, where="test")
    except SecretDetectedError as exc:
        rendered = str(exc)
        assert secret not in rendered
        assert "AWS Access Token" in rendered
        for arg in exc.args:
            assert secret not in str(arg)
        assert secret not in str(exc.labels)
    else:
        pytest.fail("guard_content should have raised")


def test_multiline_private_key_detected() -> None:
    scanner = SecretScanner()
    content = (
        "Here is my ssh key for the deployment:\n\n"
        + CANONICAL_POSITIVES["private-key"]
        + "\n\nPlease save it."
    )
    matches = scanner.scan(content)
    ids = [m.rule_id for m in matches]
    assert "private-key" in ids


def test_private_key_without_end_marker_not_matched() -> None:
    scanner = SecretScanner()
    content = "-----BEGIN RSA PRIVATE KEY-----\n" + ("a" * 64) + "\nno end here"
    matches = [m for m in scanner.scan(content) if m.rule_id == "private-key"]
    assert matches == []


def test_private_key_with_nonkey_marker_not_matched() -> None:
    scanner = SecretScanner()
    content = (
        "-----BEGIN DOCUMENTATION-----\n"
        + "This section discusses PRIVATE KEY management.\n"
        + ("info " * 20)
        + "\n-----END DOCUMENTATION-----"
    )
    matches = [m for m in scanner.scan(content) if m.rule_id == "private-key"]
    assert matches == []


def test_private_key_matches_case_insensitively() -> None:
    scanner = SecretScanner()
    content = "-----begin rsa private key-----\n" + ("a" * 64) + "\n-----end rsa private key-----"
    matches = [m for m in scanner.scan(content) if m.rule_id == "private-key"]
    assert len(matches) == 1


def test_openai_without_t3blbkfj_anchor_rejected() -> None:
    """Ensures the openai rule actually requires the T3BlbkFJ anchor —
    a common bug in scanners that port the regex incorrectly."""
    scanner = SecretScanner()
    content = "token: sk-proj-" + ("A" * 80) + " end"
    matches = [m for m in scanner.scan(content) if m.rule_id == "openai-api-key"]
    assert matches == []


def test_openai_short_form_with_anchor_matches() -> None:
    scanner = SecretScanner()
    sample = "sk-" + ("a" * 20) + "T3BlbkFJ" + ("a" * 20)
    content = f"token: {sample} end"
    matches = [m for m in scanner.scan(content) if m.rule_id == "openai-api-key"]
    assert len(matches) == 1


def test_sentry_org_requires_eyjpyxqio_prefix() -> None:
    """Real Sentry org tokens start with sntrys_eyJpYXQiO — anything else
    is a different token type or a false pattern."""
    scanner = SecretScanner()
    content = "token: sntrys_" + ("A" * 80) + " end"
    matches = [m for m in scanner.scan(content) if m.rule_id == "sentry-org-token"]
    assert matches == []


def test_lazy_compilation_defers_until_first_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = {"n": 0}
    real_compile = re.compile

    def tracking_compile(pattern, flags=0):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        return real_compile(pattern, flags)

    monkeypatch.setattr(re, "compile", tracking_compile)

    s = SecretScanner()
    assert call_count["n"] == 0

    s.scan("clean content")
    assert call_count["n"] == len(RULES)

    s.scan("more clean content")
    assert call_count["n"] == len(RULES)


def test_get_scanner_returns_shared_singleton() -> None:
    a = get_scanner()
    b = get_scanner()
    assert a is b


def test_redact_replaces_matched_text_without_capture_group() -> None:
    """github-pat has no capture group — full match is replaced."""
    scanner = SecretScanner()
    secret = CANONICAL_POSITIVES["github-pat"]
    content = f"before {secret} after"
    redacted = scanner.redact(content)
    assert secret not in redacted
    assert "[REDACTED]" in redacted
    assert "before " in redacted
    assert " after" in redacted


def test_redact_preserves_trailing_boundary_char() -> None:
    """gcp-api-key has capture group + trailing-boundary pattern. The
    captured secret should be replaced, but the trailing whitespace that
    the pattern consumes should be preserved in the output."""
    scanner = SecretScanner()
    secret = CANONICAL_POSITIVES["gcp-api-key"]
    content = f"api key {secret} used for maps"
    redacted = scanner.redact(content)
    assert secret not in redacted
    assert "[REDACTED]" in redacted
    assert "[REDACTED] used" in redacted


def test_redact_clean_content_returns_unchanged() -> None:
    scanner = SecretScanner()
    content = "No secrets at all, just plain text."
    assert scanner.redact(content) == content


def test_redact_empty_or_none() -> None:
    scanner = SecretScanner()
    assert scanner.redact("") == ""
    assert scanner.redact(None) == ""


def test_redact_tail_mode_preserves_prefix_and_suffix_for_long_values() -> None:
    """Values >= 16 chars render as `<first 8>...<last 4>` so operators
    can distinguish between two leaked credentials in a log without
    exposing the full secret. Uses aws-access-token because its pattern
    has an explicit capture group; rules without one always full-redact
    regardless of mode (see test_redact_tail_mode_is_noop_without_capture)."""
    scanner = SecretScanner()
    secret = CANONICAL_POSITIVES["aws-access-token"]
    out = scanner.redact(f"creds={secret} end", mode="tail")
    assert secret not in out
    assert "..." in out
    assert secret[:8] in out
    assert secret[-4:] in out


def test_redact_tail_mode_is_noop_without_capture_group() -> None:
    """Rules without a capture group (e.g. github-pat) always fall back
    to full `[REDACTED]` because there is no bounded sub-value to
    tail-preserve. Documents the intentional limitation."""
    scanner = SecretScanner()
    secret = CANONICAL_POSITIVES["github-pat"]
    out = scanner.redact(f"token={secret}", mode="tail")
    assert secret not in out
    assert "[REDACTED]" in out


def test_redact_tail_mode_falls_back_to_full_redaction_for_short_values() -> None:
    """Captured values shorter than 16 chars would leak a huge fraction
    of the secret if we showed a prefix, so tail mode masks them fully."""
    short_rule = SecretRule(
        id="short-capture",
        label="Short Capture",
        pattern=r"tok=(\w{8})\b",
    )
    custom = SecretScanner(rules=(short_rule,))
    out = custom.redact("tok=abcd1234 trailing", mode="tail")
    assert "abcd1234" not in out
    assert "[REDACTED]" in out


def test_redact_full_mode_is_default() -> None:
    """Default behaviour matches the pre-existing contract — full mask."""
    scanner = SecretScanner()
    secret = CANONICAL_POSITIVES["github-pat"]
    assert "[REDACTED]" in scanner.redact(f"token={secret}")


def test_redact_json_fields_masks_opaque_token_value() -> None:
    scanner = SecretScanner()
    body = '{"token":"opaque-xxx-1234567","visible":"ok"}'
    out = scanner.redact_json_fields(body)
    assert "opaque-xxx-1234567" not in out
    assert '"token":"[REDACTED]"' in out
    assert '"visible":"ok"' in out


def test_redact_json_fields_is_case_insensitive() -> None:
    scanner = SecretScanner()
    out = scanner.redact_json_fields('{"Authorization":"Bearer zzz-opaque-abc"}')
    assert "zzz-opaque-abc" not in out
    assert "[REDACTED]" in out


def test_redact_json_fields_handles_all_configured_names() -> None:
    scanner = SecretScanner()
    names = [
        "access_token",
        "api_key",
        "authorization",
        "bearer_token",
        "client_secret",
        "environment_secret",
        "password",
        "refresh_token",
        "secret",
        "session_ingress_token",
        "token",
    ]
    for name in names:
        out = scanner.redact_json_fields(f'{{"{name}":"opaque-value-42"}}')
        assert "opaque-value-42" not in out, f"field {name} leaked"
        assert "[REDACTED]" in out, f"field {name} not masked"


def test_redact_json_fields_handles_escaped_quotes_in_value() -> None:
    """A value containing `\\"` must not terminate the match early — the
    inner pattern consumes backslash-escaped sequences as one unit.

    Assertions verify three distinct invariants:
      1. The field's value becomes `[REDACTED]` (match fired and replaced).
      2. Neither the literal escaped form nor the opaque fragment survives
         anywhere in the output (proves the full value was captured, not
         just the prefix up to the escaped quote).
      3. The trailing field `visible` is still present with an intact
         key/value pair (proves the regex did not over-consume past the
         closing quote and swallow the rest of the JSON object).
    """
    scanner = SecretScanner()
    body = r'{"token":"abc\"xyz-opaque","visible":"ok"}'
    out = scanner.redact_json_fields(body)
    assert '"token":"[REDACTED]"' in out
    assert r"abc\"xyz-opaque" not in out
    assert "xyz-opaque" not in out
    assert '"visible":"ok"' in out


def test_redact_json_fields_tail_mode_preview() -> None:
    scanner = SecretScanner()
    value = "abcdefghijklmnopqrstuvwxyz"
    body = f'{{"api_key":"{value}"}}'
    out = scanner.redact_json_fields(body, mode="tail")
    assert value not in out
    assert "..." in out
    assert value[:8] in out
    assert value[-4:] in out


def test_redact_json_fields_ignores_non_sensitive_keys() -> None:
    scanner = SecretScanner()
    body = '{"username":"alice","email":"a@b.com"}'
    assert scanner.redact_json_fields(body) == body


def test_redact_json_fields_empty_or_none() -> None:
    scanner = SecretScanner()
    assert scanner.redact_json_fields("") == ""
    assert scanner.redact_json_fields(None) == ""


def test_check_content_for_secrets_returns_none_on_clean() -> None:
    assert check_content_for_secrets("hello world") is None
    assert check_content_for_secrets("") is None
    assert check_content_for_secrets(None) is None


def test_check_content_for_secrets_returns_labels_on_dirty() -> None:
    content = f"password {CANONICAL_POSITIVES['aws-access-token']} end"
    labels = check_content_for_secrets(content)
    assert labels == ["AWS Access Token"]


def test_guard_content_noop_on_clean() -> None:
    guard_content("hello", where="test")
    guard_content("", where="test")
    guard_content(None, where="test")


def test_guard_content_raises_with_where_in_message() -> None:
    content = f"token: {CANONICAL_POSITIVES['github-pat']} end"
    with pytest.raises(SecretDetectedError) as exc_info:
        guard_content(content, where="lc-knowledge-write")
    assert "lc-knowledge-write" in str(exc_info.value)
    assert "GitHub PAT" in str(exc_info.value)
    assert exc_info.value.labels == ["GitHub PAT"]
    assert exc_info.value.where == "lc-knowledge-write"


def test_secret_detected_error_carries_multiple_labels() -> None:
    content = (
        "a "
        + CANONICAL_POSITIVES["aws-access-token"]
        + " b "
        + CANONICAL_POSITIVES["github-pat"]
        + " end"
    )
    with pytest.raises(SecretDetectedError) as exc_info:
        guard_content(content, where="signal-ingest")
    labels = exc_info.value.labels
    assert set(labels) == {"AWS Access Token", "GitHub PAT"}


def test_anthropic_admin_key_detected() -> None:
    """Safety-critical: we ship on top of Claude. The admin key is the
    highest-risk Anthropic credential and MUST be caught."""
    scanner = SecretScanner()
    matches = scanner.scan(f"admin: {ANT_ADMIN_SAMPLE} end")
    ids = [m.rule_id for m in matches]
    assert "anthropic-admin-api-key" in ids


def test_anthropic_api_and_admin_keys_are_separate_rules() -> None:
    """The two Anthropic rules must not collapse — each has a distinct ID."""
    scanner = SecretScanner()
    blob = f"regular: {ANT_SAMPLE} admin: {ANT_ADMIN_SAMPLE} end"
    matches = scanner.scan(blob)
    ids = {m.rule_id for m in matches}
    assert "anthropic-api-key" in ids
    assert "anthropic-admin-api-key" in ids
