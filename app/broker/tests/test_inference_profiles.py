"""Inference profiles: parsing, all-or-nothing validation, the modelId
swap at the boto3 boundary, and - the point of the whole file - that the
profile actually REACHES the lane's gateway.

HANDOVER.md section 6's wiring audit is why that last test exists: "a
module that is imported by nothing, an event nobody subscribes to and a
route no host mounts all pass their own unit tests and are all still
broken." A profile mapping that parses perfectly and is never handed to a
gateway is exactly that shape of bug, and it would surface as an
AccessDenied mid-run, after money had been spent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from broker.bedrock import Boto3BedrockGateway
from broker.config import (
    BEDROCK_MODELS,
    BEDROCK_LANE_REGIONS,
    DEEPSEEK_MODEL_ID,
    KIMI_MODEL_ID,
    BrokerSettings,
    expected_lane_keys,
    lane_key,
    parse_inference_profiles,
)
from broker.lanes import lane_id

ARN = "arn:aws:bedrock:{region}:123456789012:application-inference-profile/{slug}"


def full_mapping() -> dict[str, str]:
    """A profile for every real lane - derived, so a new model is covered."""
    return {
        lane_key(model, region): ARN.format(region=region, slug=f"{i}{j}")
        for i, model in enumerate(BEDROCK_MODELS)
        for j, region in enumerate(BEDROCK_LANE_REGIONS)
    }


def test_lane_key_matches_lanes_lane_id() -> None:
    for model in BEDROCK_MODELS:
        for region in BEDROCK_LANE_REGIONS:
            assert lane_key(model, region) == lane_id(model, region)


def test_expected_lane_keys_is_every_model_in_every_region() -> None:
    assert len(expected_lane_keys()) == len(BEDROCK_MODELS) * len(BEDROCK_LANE_REGIONS)
    assert lane_key(KIMI_MODEL_ID, "ap-south-1") in expected_lane_keys()


def test_parses_comma_separated() -> None:
    parsed = parse_inference_profiles(
        "X", f"{KIMI_MODEL_ID}@ap-south-1=arn:one,{DEEPSEEK_MODEL_ID}@us-east-1=arn:two"
    )
    assert parsed == {
        f"{KIMI_MODEL_ID}@ap-south-1": "arn:one",
        f"{DEEPSEEK_MODEL_ID}@us-east-1": "arn:two",
    }


def test_parses_newline_separated_and_ignores_blanks() -> None:
    raw = "\n".join(["", f"{KIMI_MODEL_ID}@ap-south-1=arn:one", "  ", f"{DEEPSEEK_MODEL_ID}@us-east-1=arn:two"])
    assert len(parse_inference_profiles("X", raw)) == 2


def test_arn_colons_and_slashes_survive_parsing() -> None:
    arn = ARN.format(region="us-east-1", slug="wyrkssalyyw3")
    parsed = parse_inference_profiles("X", f"{KIMI_MODEL_ID}@us-east-1={arn}")
    assert parsed[f"{KIMI_MODEL_ID}@us-east-1"] == arn


@pytest.mark.parametrize(
    "raw",
    ["no-equals-sign", f"{KIMI_MODEL_ID}@ap-south-1=", "=arn:orphan"],
)
def test_malformed_entries_raise(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_inference_profiles("X", raw)


def test_duplicate_lane_raises() -> None:
    with pytest.raises(ValueError, match="twice"):
        parse_inference_profiles("X", f"{KIMI_MODEL_ID}@ap-south-1=a,{KIMI_MODEL_ID}@ap-south-1=b")


def test_empty_mapping_is_valid_in_real_mode() -> None:
    settings = BrokerSettings(mock_mode=False, world_base_url="http://w", trace_sqlite_path="/tmp/k.db")
    assert settings.validate() == []


def test_full_mapping_is_valid() -> None:
    settings = BrokerSettings(
        mock_mode=False,
        world_base_url="http://w",
        trace_sqlite_path="/tmp/k.db",
        bedrock_inference_profiles=full_mapping(),
    )
    assert settings.validate() == []


def test_partial_mapping_is_legal_and_builds_only_those_lanes() -> None:
    """A partial mapping is the COMMON case, not an error.

    Application inference profiles are region-scoped, and a least-privilege
    IAM user may hold them in one region only - measured on this
    deployment: both models invokable in ap-south-1, neither in us-east-1
    or us-west-2 (ValidationException: model identifier is invalid).

    The safety property that used to justify rejecting this is preserved a
    better way: a lane with no profile is NOT BUILT, so it can never be
    left sending a bare model id and failing with AccessDenied mid-run.
    Two real lanes beat six of which four are traps.
    """
    from broker.lanes import build_lane_pool

    one_region = {
        lane_key(model, BEDROCK_LANE_REGIONS[0]): ARN.format(
            region=BEDROCK_LANE_REGIONS[0], slug=f"p{i}"
        )
        for i, model in enumerate(BEDROCK_MODELS)
    }
    settings = BrokerSettings(
        mock_mode=False,
        world_base_url="http://w",
        trace_sqlite_path="/tmp/k.db",
        bedrock_inference_profiles=one_region,
    )
    assert settings.validate() == [], "a partial mapping must not block boot"

    pool = build_lane_pool(settings, lambda model, region: object())
    lanes = pool.all_lanes()
    assert len(lanes) == len(BEDROCK_MODELS), (
        "only the lanes with a profile should exist - one per model, in the "
        "single region the partial mapping covers"
    )
    assert {lane.region for lane in lanes} == {BEDROCK_LANE_REGIONS[0]}
    assert {lane.model for lane in lanes} == set(BEDROCK_MODELS)


def test_a_model_with_no_lanes_at_all_is_a_hard_error() -> None:
    """Silently serving one model when two were configured would make the
    routing policy (Kimi preferred, DeepSeek overflow) quietly meaningless."""
    import pytest as _pytest

    from broker.lanes import build_lane_pool

    kimi_only = {
        lane_key(KIMI_MODEL_ID, BEDROCK_LANE_REGIONS[0]): ARN.format(
            region=BEDROCK_LANE_REGIONS[0], slug="k"
        )
    }
    settings = BrokerSettings(
        mock_mode=False,
        world_base_url="http://w",
        trace_sqlite_path="/tmp/k.db",
        bedrock_inference_profiles=kimi_only,
    )
    with _pytest.raises(ValueError, match="cannot be served at all"):
        build_lane_pool(settings, lambda model, region: object())


def test_unknown_lane_is_rejected() -> None:
    mapping = full_mapping()
    mapping["anthropic.claude-opus-5@eu-west-1"] = "arn:nope"
    settings = BrokerSettings(
        mock_mode=False,
        world_base_url="http://w",
        trace_sqlite_path="/tmp/k.db",
        bedrock_inference_profiles=mapping,
    )
    assert any("do not exist" in e for e in settings.validate())


def test_mock_mode_never_requires_profiles() -> None:
    partial = {lane_key(KIMI_MODEL_ID, "ap-south-1"): "arn:only-one"}
    assert BrokerSettings(mock_mode=True, bedrock_inference_profiles=partial).validate() == []


def test_build_from_env_reads_the_var() -> None:
    settings, errors = BrokerSettings.build_from_env(
        {"BEDROCK_INFERENCE_PROFILES": f"{KIMI_MODEL_ID}@ap-south-1=arn:one"}
    )
    assert errors == []
    assert settings.bedrock_inference_profiles == {f"{KIMI_MODEL_ID}@ap-south-1": "arn:one"}


def test_build_from_env_reports_malformed_value() -> None:
    _, errors = BrokerSettings.build_from_env({"BEDROCK_INFERENCE_PROFILES": "garbage"})
    assert any("BEDROCK_INFERENCE_PROFILES" in e for e in errors)


def gateway(profile: "str | None") -> Boto3BedrockGateway:
    return Boto3BedrockGateway("ap-south-1", inference_profile_id=profile)


def test_no_profile_leaves_the_request_alone() -> None:
    request = {"modelId": KIMI_MODEL_ID, "messages": []}
    assert gateway(None).resolved_request(request) == request


def test_profile_replaces_model_id() -> None:
    arn = ARN.format(region="ap-south-1", slug="3q4aq98lc3ui")
    resolved = gateway(arn).resolved_request({"modelId": KIMI_MODEL_ID, "messages": [], "system": ["s"]})
    assert resolved["modelId"] == arn
    assert resolved["messages"] == [] and resolved["system"] == ["s"]


def test_swap_never_mutates_the_callers_request() -> None:
    request = {"modelId": KIMI_MODEL_ID, "messages": []}
    gateway(ARN.format(region="ap-south-1", slug="x")).resolved_request(request)
    assert request["modelId"] == KIMI_MODEL_ID


def test_build_service_hands_every_lane_its_own_profile(tmp_path) -> None:
    """The audit test: parsing is worthless if nothing carries the result.

    Builds the real service the way broker/app.py's create_app does, then
    reads the profile back off each of the six constructed gateways. A
    factory that dropped `model` on the floor - the easy mistake, since
    Boto3BedrockGateway's first positional arg is `region` - passes every
    other test in this file and fails only this one.
    """
    from broker.app import build_service

    mapping = full_mapping()
    settings = BrokerSettings(
        mock_mode=False,
        world_base_url="http://127.0.0.1:8100",
        trace_sqlite_path=str(tmp_path / "trace.db"),
        bedrock_inference_profiles=mapping,
    )
    assert settings.validate() == []

    service = build_service(settings)
    lanes = service.lane_pool.all_lanes()
    assert len(lanes) == len(BEDROCK_MODELS) * len(BEDROCK_LANE_REGIONS)

    for lane in lanes:
        expected = mapping[lane_key(lane.model, lane.region)]
        assert lane.gateway.inference_profile_id == expected, (
            f"lane {lane.id} got {lane.gateway.inference_profile_id!r}, expected {expected!r}"
        )
        assert lane.gateway.region == lane.region


def test_build_service_without_profiles_sends_bare_model_ids(tmp_path) -> None:
    from broker.app import build_service

    settings = BrokerSettings(
        mock_mode=False,
        world_base_url="http://127.0.0.1:8100",
        trace_sqlite_path=str(tmp_path / "trace.db"),
    )
    service = build_service(settings)
    for lane in service.lane_pool.all_lanes():
        assert lane.gateway.inference_profile_id is None
        assert lane.gateway.resolved_request({"modelId": lane.model})["modelId"] == lane.model


def write_env(tmp_path, *lines: str) -> Path:
    """Write a .env from bare lines. Takes lines rather than one blob so
    no test has to embed a newline escape."""
    p: Path = tmp_path / ".env"
    p.write_text("".join(line + chr(10) for line in lines), encoding="utf-8")
    return p


def test_exports_credentials_into_the_environment(tmp_path, monkeypatch) -> None:
    import os

    from broker.config import export_aws_env

    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    env = write_env(
        tmp_path,
        "AWS_ACCESS_KEY_ID=AKIAEXAMPLE",
        "AWS_SECRET_ACCESS_KEY=s3cr3t",
        "AWS_SESSION_TOKEN=tok",
    )

    exported = export_aws_env(env)

    assert sorted(exported) == ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"]
    assert os.environ["AWS_ACCESS_KEY_ID"] == "AKIAEXAMPLE"
    assert os.environ["AWS_SECRET_ACCESS_KEY"] == "s3cr3t"


def test_returns_names_never_values(tmp_path, monkeypatch) -> None:
    """The return value gets logged, so it must not be able to carry a
    secret. broker/app.py logs ", ".join(exported) verbatim."""
    from broker.config import export_aws_env

    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    env = write_env(tmp_path, "AWS_SECRET_ACCESS_KEY=super-secret-value")

    exported = export_aws_env(env)

    assert exported == ["AWS_SECRET_ACCESS_KEY"]
    assert "super-secret-value" not in " ".join(exported)


def test_real_environment_wins_over_the_file(tmp_path, monkeypatch) -> None:
    import os

    from broker.config import export_aws_env

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "from-the-shell")
    env = write_env(tmp_path, "AWS_ACCESS_KEY_ID=from-the-file")

    exported = export_aws_env(env)

    assert exported == []
    assert os.environ["AWS_ACCESS_KEY_ID"] == "from-the-shell"


def test_blank_entries_are_skipped_not_exported_empty(tmp_path, monkeypatch) -> None:
    """An empty AWS_ACCESS_KEY_ID is not "unset" to boto3 - it means
    "authenticate with nothing", which fails with an opaque signature
    error instead of falling through to ~/.aws."""
    import os

    from broker.config import export_aws_env

    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    env = write_env(tmp_path, "AWS_ACCESS_KEY_ID=", "AWS_PROFILE=kasukabe")

    exported = export_aws_env(env)

    assert exported == ["AWS_PROFILE"]
    assert "AWS_ACCESS_KEY_ID" not in os.environ


def test_mock_mode_never_exports_credentials(tmp_path, monkeypatch) -> None:
    """Wiring: create_app must not lift secret material into the
    environment of a process that cannot use it. Mock mode never builds a
    boto3 client, so there is nothing to authenticate."""
    import os

    from broker.app import create_app

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    write_env(tmp_path, "AWS_ACCESS_KEY_ID=should-not-be-exported")

    create_app(settings=BrokerSettings(mock_mode=True))

    assert "AWS_ACCESS_KEY_ID" not in os.environ
