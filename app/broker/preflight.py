"""Prove the Bedrock credentials, the IAM policy and the profiles work -
before a real run starts spending on the models the town thinks with.

    uv run python -m broker.preflight            # config + credentials only
    uv run python -m broker.preflight --invoke   # also send ONE tiny call

The problem this solves: every failure mode here looks the same from
inside a run. Missing keys, an IAM policy that covers the foundation model
but not the profile, a profile ARN from the wrong region, a typo in
BEDROCK_INFERENCE_PROFILES - all of them surface as an AccessDenied or a
signature error on some agent's turn, three hundred ticks in, after money
has moved and with 314 engines mid-flight. Each one is trivial to see from
outside and expensive to see from inside.

Lives in broker/ because it imports boto3 and broker/ is the only package
allowed to (app/CONVENTIONS.md, enforced by
app/tests/test_module_boundaries.py).

WHAT IT COSTS. Without --invoke: nothing, and it cannot spend - it makes
no bedrock-runtime call at all. With --invoke: one `converse` of a few
tokens against the CONTROL profile, which is a handful of nano-dollars.
The control profile is deliberately not one of the town's models
(BEDROCK_CONTROL_PROFILE_ARN, not MODEL_ALLOWLIST): a canary should not be
a lane, and proving reachability should not warm a model the research then
measures.
"""

from __future__ import annotations

import sys

from broker.config import (
    BEDROCK_LANE_REGIONS,
    MODEL_ALLOWLIST,
    BrokerSettings,
    expected_lane_keys,
    export_aws_env,
    read_env_file,
)

OK = "  ok   "
BAD = "  FAIL "
SKIP = "  --   "


def credential_names_present() -> list[str]:
    """Which AWS credential variables are set, by NAME only.

    Values are never printed, here or anywhere else in this package. A
    preflight that echoes a secret to a terminal - and to whatever is
    scrolling that terminal into a screen share - has traded one problem
    for a worse one.
    """
    import os

    return [
        name
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                     "AWS_PROFILE", "AWS_REGION")
        if os.environ.get(name)
    ]


def check_identity(region: str) -> tuple[bool, str]:
    """Who are we? STS get_caller_identity is free and needs no Bedrock
    permission at all, so it separates "no credentials" from "credentials
    that Bedrock will not accept" - two problems with one error message."""
    try:
        import boto3

        ident = boto3.client("sts", region_name=region).get_caller_identity()
    except Exception as exc:  # noqa: BLE001 - every failure here is a report line
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"
    arn = str(ident.get("Arn", ""))
    return True, f"account {ident.get('Account')}  ({arn.rsplit('/', 1)[-1] or arn})"


def check_profiles(settings: BrokerSettings) -> list[tuple[str, bool, str]]:
    """One row per lane the pool will build: is there a profile for it, and
    is that profile's own region the lane's region?

    The region check matters because an application inference profile ARN
    names the region it was made in. Pasting the ap-south-1 ARN into the
    us-east-1 lane parses, validates, boots, and then fails at invoke time
    on somebody's turn.
    """
    rows: list[tuple[str, bool, str]] = []
    for lane in expected_lane_keys():
        arn = settings.bedrock_inference_profiles.get(lane)
        if not arn:
            rows.append((lane, True,
                         "bare model id (no profile mapping configured)"
                         if not settings.bedrock_inference_profiles
                         else "no profile - lane will not be built"))
            continue
        region = lane.rsplit("@", 1)[-1]
        parts = arn.split(":")
        arn_region = parts[3] if arn.startswith("arn:") and len(parts) > 4 else ""
        if arn_region and arn_region != region:
            rows.append((lane, False, f"profile is in {arn_region}, lane is {region}"))
        else:
            rows.append((lane, True, arn.rsplit("/", 1)[-1]))
    return rows


def smoke_invoke(arn: str, region: str) -> tuple[bool, str]:
    """One real `converse` of a few tokens. The only call here that costs."""
    try:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=region)
        res = client.converse(
            modelId=arn,
            messages=[{"role": "user", "content": [{"text": "Reply with the single word: ok"}]}],
            inferenceConfig={"maxTokens": 5, "temperature": 0},
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:200]}"
    usage = res.get("usage", {})
    text = ""
    for block in res.get("output", {}).get("message", {}).get("content", []):
        text += block.get("text", "")
    return True, (f"replied {text.strip()[:24]!r} "
                  f"({usage.get('inputTokens')}/{usage.get('outputTokens')} tok)")


def main(argv: list[str]) -> int:
    do_invoke = "--invoke" in argv
    env = read_env_file()
    settings, errors = BrokerSettings.build_from_env(env)

    print("Kasukabe Bedrock preflight")
    print("=" * 62)

    if errors:
        print(f"{BAD} configuration")
        for e in errors:
            print(f"         {e}")
        return 2
    print(f"{OK} configuration parses")

    semantic = settings.validate()
    if semantic:
        print(f"{BAD} configuration is not valid")
        for e in semantic:
            print(f"         {e}")
        return 2
    print(f"{OK} configuration is valid")

    print(f"\n  models  : {', '.join(MODEL_ALLOWLIST)}")
    print(f"  regions : {', '.join(BEDROCK_LANE_REGIONS)}")
    print(f"  lanes   : {len(MODEL_ALLOWLIST) * len(BEDROCK_LANE_REGIONS)}"
          f"  ({settings.rpm_per_lane} RPM each)")

    if settings.mock_mode:
        print(f"\n{SKIP} MOCK_MODE=true - this broker will not call Bedrock at all.")
        print("         Nothing below is required for a mock run.")

    export_aws_env()
    names = credential_names_present()
    print(f"\n  credentials in the environment: "
          f"{', '.join(names) if names else 'NONE'}")
    if not any(n in names for n in ("AWS_ACCESS_KEY_ID", "AWS_PROFILE")):
        print(f"{BAD} no credentials. Fill AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY")
        print("         in app/.env, or set AWS_PROFILE to a profile in ~/.aws.")
        return 3

    region = BEDROCK_LANE_REGIONS[0]
    ok, detail = check_identity(region)
    print(f"{OK if ok else BAD} identity: {detail}")
    if not ok:
        return 3

    print("\n  lanes:")
    all_ok = True
    for lane, ok, detail in check_profiles(settings):
        print(f"{OK if ok else BAD} {lane:<34} {detail}")
        all_ok = all_ok and ok

    arn = settings.bedrock_control_profile_arn
    if do_invoke:
        if not arn:
            print(f"\n{SKIP} --invoke needs BEDROCK_CONTROL_PROFILE_ARN in app/.env")
        else:
            arn_region = arn.split(":")[3] if arn.startswith("arn:") else region
            ok, detail = smoke_invoke(arn, arn_region)
            print(f"\n{OK if ok else BAD} control invoke @{arn_region}: {detail}")
            all_ok = all_ok and ok
    elif arn:
        print(f"\n{SKIP} control profile configured; re-run with --invoke to call it")

    print("=" * 62)
    configured = settings.bedrock_inference_profiles
    servable = {
        model
        for model in MODEL_ALLOWLIST
        if not configured or any(k.startswith(f"{model}@") for k in configured)
    }
    unservable = sorted(set(MODEL_ALLOWLIST) - servable)
    if unservable:
        print(f"NOT READY - no lane at all for: {', '.join(unservable)}")
        all_ok = False
    elif configured:
        built = len(configured)
        total = len(MODEL_ALLOWLIST) * len(BEDROCK_LANE_REGIONS)
        print(f"  {built} of {total} lanes will be built ({built * settings.rpm_per_lane} RPM)")
    print("READY" if all_ok else "NOT READY - fix the FAIL lines above")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
