"""Read-only sweep: which Bedrock inference profiles exist, per lane.

Prints a ready-to-paste BEDROCK_INFERENCE_PROFILES value for
broker/config.py's bedrock_inference_profiles, so the six lane ARNs never
have to be assembled by hand.

    python -m broker.discover_profiles

Lives in broker/ because it imports boto3 and broker/ is the only package
allowed to (app/CONVENTIONS.md, enforced by
app/tests/test_module_boundaries.py). It calls only the `bedrock` control
plane's list_inference_profiles - never bedrock-runtime, never `converse`
- so it cannot spend a cent no matter how it is invoked.

A region that raises (not enabled, no permission, no such API) is
reported and skipped rather than aborting the sweep: partial coverage is
exactly the thing worth discovering, and BrokerSettings.validate() will
refuse to boot on a partially-filled mapping anyway.
"""

from __future__ import annotations

import sys

from broker.config import (
    BEDROCK_LANE_REGIONS,
    MODEL_ALLOWLIST,
    expected_lane_keys,
    export_aws_env,
    lane_key,
)


def profiles_in_region(region: str) -> list[dict]:
    """Every inference profile visible in one region, both types."""
    import boto3

    client = boto3.client("bedrock", region_name=region)
    summaries: list[dict] = []
    for type_filter in ("SYSTEM_DEFINED", "APPLICATION"):
        token: str | None = None
        while True:
            kwargs: dict = {"maxResults": 100, "typeEquals": type_filter}
            if token:
                kwargs["nextToken"] = token
            page = client.list_inference_profiles(**kwargs)
            summaries.extend(page.get("inferenceProfileSummaries", []))
            token = page.get("nextToken")
            if not token:
                break
    return summaries


def matches_model(summary: dict, model_id: str) -> bool:
    """True if this profile routes to `model_id`.

    Substring match on the underlying foundation-model ARNs, not equality:
    an FM ARN carries a version suffix (".../deepseek.v3.2-v1:0") that the
    allowlist id deliberately does not. Falls back to the profile's own id
    and name for profiles that report no models list.
    """
    for model in summary.get("models", []):
        if model_id in model.get("modelArn", ""):
            return True
    haystack = summary.get("inferenceProfileId", "") + " " + summary.get("inferenceProfileName", "")
    return model_id in haystack


def discover() -> tuple[dict[str, str], list[str]]:
    found: dict[str, str] = {}
    problems: list[str] = []

    for region in BEDROCK_LANE_REGIONS:
        try:
            summaries = profiles_in_region(region)
        except Exception as e:  # noqa: BLE001 - any failure is "skip and report"
            problems.append(f"{region}: {type(e).__name__}: {e}")
            continue

        for model_id in MODEL_ALLOWLIST:
            hits = [s for s in summaries if matches_model(s, model_id)]
            active = [s for s in hits if s.get("status", "ACTIVE") == "ACTIVE"] or hits
            if not active:
                problems.append(f"{lane_key(model_id, region)}: no inference profile found")
                continue
            if len(active) > 1:
                names = ", ".join(s.get("inferenceProfileId", "?") for s in active)
                problems.append(
                    f"{lane_key(model_id, region)}: {len(active)} profiles match "
                    f"({names}) - picked the first, edit if that is wrong"
                )
            chosen = active[0]
            found[lane_key(model_id, region)] = (
                chosen.get("inferenceProfileArn") or chosen["inferenceProfileId"]
            )

    return found, problems


def probe_invoke(model_id: str, region: str) -> str:
    """One minimal `converse` call, to answer the only question listing
    cannot: is this model invokable AT ALL with these credentials?

    THIS SPENDS MONEY - a few tokens, fractions of a cent - and is opt-in
    behind --invoke for that reason. Everything else in this module is
    control-plane only. maxTokens=1 keeps it to the smallest billable unit.

    Returns a short human-readable verdict rather than raising, so one
    denied model does not hide the result for the others.
    """
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    client = boto3.client(
        "bedrock-runtime", region_name=region, config=Config(retries={"max_attempts": 1})
    )
    try:
        resp = client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
            inferenceConfig={"maxTokens": 1},
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "?")
        msg = e.response.get("Error", {}).get("Message", str(e))
        return f"{code}: {msg[:160]}"
    usage = resp.get("usage", {})
    return f"OK (in={usage.get('inputTokens')} out={usage.get('outputTokens')})"


def main() -> int:
    if exported := export_aws_env():
        print(f"Using credentials from .env ({', '.join(exported)})")
    print(f"Sweeping {len(BEDROCK_LANE_REGIONS)} regions for {len(MODEL_ALLOWLIST)} models")
    print(f"  models : {', '.join(MODEL_ALLOWLIST)}")
    print(f"  regions: {', '.join(BEDROCK_LANE_REGIONS)}")
    print()

    found, problems = discover()

    for key in expected_lane_keys():
        mark = "OK  " if key in found else "MISS"
        print(f"  [{mark}] {key:<40} {found.get(key, '-')}")
    print()

    if problems:
        print("Problems:")
        for p in problems:
            print(f"  - {p}")
        print()

    if "--invoke" in sys.argv:
        print("--invoke: making ONE minimal real call per lane (spends a few tokens)")
        for model_id in MODEL_ALLOWLIST:
            for region in BEDROCK_LANE_REGIONS:
                print(f"  {lane_key(model_id, region):<40} {probe_invoke(model_id, region)}")
        print()

    if not found:
        print("No profiles found. If this account invokes foundation models")
        print("directly, leave BEDROCK_INFERENCE_PROFILES unset - the bare")
        print("model id is then correct and the broker needs no change.")
        return 1

    print(f"Found {len(found)} of {len(expected_lane_keys())} lanes.")
    if len(found) < len(expected_lane_keys()):
        print("PARTIAL - the broker refuses to boot on a partly-filled mapping.")
        print("Either get the missing lanes granted, or narrow")
        print("BEDROCK_LANE_REGIONS in broker/config.py to the regions you hold.")
        print()

    print("BEDROCK_INFERENCE_PROFILES=" + ",".join(f"{k}={v}" for k, v in found.items()))
    return 0 if len(found) == len(expected_lane_keys()) else 2


if __name__ == "__main__":
    sys.exit(main())
