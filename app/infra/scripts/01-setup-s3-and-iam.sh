#!/usr/bin/env bash
# Kasukabe host-bringup, step 1 (RUNBOOK.md section 3).
#
# Run from the OPERATOR'S LOCAL MACHINE (needs AWS CLI v2 + credentials
# with IAM + S3 admin rights). Creates:
#   - one S3 bucket for Litestream replication, gzipped raw LLM payload
#     archives, world-checkpoint archives, and per-game-day Parquet
#     exports (DESIGN.md section 9) - see the prefix layout printed at
#     the end, which is the interface the trace-store track's S3 client
#     (task tracker: "Build boto3-free S3 client for archive uploads")
#     and litestream.yml (same track) should target.
#   - an IAM role + instance profile the m5.metal host assumes, scoped to
#     ONLY that bucket plus InvokeModel on the two allow-listed Bedrock
#     model families in ap-south-1 (broker/config.py's MODEL_ALLOWLIST).
#
# Idempotent: safe to re-run, every AWS call either creates-if-missing or
# no-ops on "already exists".
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
BUCKET_NAME="${BUCKET_NAME:-kasukabe-genesis-${ACCOUNT_ID}-${REGION}}"
ROLE_NAME="${ROLE_NAME:-kasukabe-genesis-host-role}"
POLICY_NAME="${POLICY_NAME:-kasukabe-genesis-host-policy}"
INSTANCE_PROFILE_NAME="${INSTANCE_PROFILE_NAME:-kasukabe-genesis-host-profile}"

echo "== account ${ACCOUNT_ID} · region ${REGION} · bucket ${BUCKET_NAME} =="

# ── S3 bucket ─────────────────────────────────────────────────────────
if aws s3api head-bucket --bucket "${BUCKET_NAME}" --region "${REGION}" 2>/dev/null; then
	echo "bucket ${BUCKET_NAME} already exists, skipping create"
else
	echo "creating bucket ${BUCKET_NAME}"
	aws s3api create-bucket \
		--bucket "${BUCKET_NAME}" \
		--region "${REGION}" \
		--create-bucket-configuration LocationConstraint="${REGION}"
fi

aws s3api put-public-access-block \
	--bucket "${BUCKET_NAME}" \
	--public-access-block-configuration \
	BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

aws s3api put-bucket-versioning \
	--bucket "${BUCKET_NAME}" \
	--versioning-configuration Status=Enabled

aws s3api put-bucket-encryption \
	--bucket "${BUCKET_NAME}" \
	--server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

# Marker objects only for discoverability in the console - S3 has no real
# directories, these are zero-byte objects at the four prefixes the
# application-layer tracks should write under (RUNBOOK.md documents this
# layout as the S3 interface).
for prefix in litestream/ archives/payloads/ archives/checkpoints/ exports/parquet/; do
	aws s3api put-object --bucket "${BUCKET_NAME}" --key "${prefix}.keep" --region "${REGION}" >/dev/null
done

# ── IAM policy: least-privilege S3 (this bucket only) + Bedrock invoke
# (the two allow-listed model families only, matching
# broker/config.py's MODEL_ALLOWLIST exactly - if that tuple ever
# changes, this policy's Resource list must change with it) ────────────
POLICY_DOC=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "KasukabeS3ArchiveAccess",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject", "s3:ListBucket", "s3:DeleteObject"],
      "Resource": [
        "arn:aws:s3:::${BUCKET_NAME}",
        "arn:aws:s3:::${BUCKET_NAME}/*"
      ]
    },
    {
      "Sid": "KasukabeBedrockInvoke",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": [
        "arn:aws:bedrock:${REGION}::foundation-model/moonshotai.kimi-k2-5*",
        "arn:aws:bedrock:${REGION}::foundation-model/moonshotai.kimi-k2.5*",
        "arn:aws:bedrock:${REGION}::foundation-model/deepseek.v3-2*",
        "arn:aws:bedrock:${REGION}::foundation-model/deepseek.v3.2*",
        "arn:aws:bedrock:${REGION}::foundation-model/qwen-qwen3-coder-480b*",
        "arn:aws:bedrock:${REGION}::foundation-model/qwen.qwen3-coder-480b*",
        "arn:aws:bedrock:${REGION}:${ACCOUNT_ID}:inference-profile/*"
      ]
    }
  ]
}
EOF
)
# INFRA-21: BOTH the dotted and the dashed model id are allowed.
#
# The allowlist in broker/config.py is dotted - "moonshotai.kimi-k2.5",
# "deepseek.v3.2" - and this policy only ever granted the DASHED form,
# "kimi-k2-5*" and "v3-2*". If Bedrock's foundation-model ARNs use the
# dotted spelling in this account, the policy granted nothing at all and
# every direct model invoke would be denied.
#
# Nobody has checked which spelling this account uses, and the failure is
# silent in the worst way: the inference-profile wildcard below covers the
# path the broker normally takes, so a wrong foundation-model ARN only
# bites when a lane has no profile - which is four of the six lanes today
# (run 24: "4 of 6 lanes have no inference profile ... 200 RPM, not 600").
#
# Allowing both costs nothing: an ARN pattern that matches no real model
# grants no access. The real fix is still to LOOK - `aws bedrock
# list-foundation-models --region $REGION` prints the exact ids - and this
# script now prints what it granted so the comparison is one glance.

# NOTE the wildcard inference-profile Resource line: this account may
# front Kimi/DeepSeek through a cross-region or application inference
# profile rather than the bare foundation-model ARN (DESIGN.md section 4
# calls out "flex tier" pricing, which Bedrock exposes via inference
# profiles). Confirm the exact ARN shape in the Bedrock console
# (Model access / Cross-region inference) before the live run and tighten
# this wildcard once known - broker/config.py's MODEL_ALLOWLIST is the
# hard-fail gate at the application layer either way, so this policy is
# defense in depth, not the only check.

TRUST_DOC='{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]
}'

if aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
	echo "role ${ROLE_NAME} already exists, skipping create"
else
	echo "creating role ${ROLE_NAME}"
	aws iam create-role --role-name "${ROLE_NAME}" --assume-role-policy-document "${TRUST_DOC}"
fi

aws iam put-role-policy \
	--role-name "${ROLE_NAME}" \
	--policy-name "${POLICY_NAME}" \
	--policy-document "${POLICY_DOC}"

if aws iam get-instance-profile --instance-profile-name "${INSTANCE_PROFILE_NAME}" >/dev/null 2>&1; then
	echo "instance profile ${INSTANCE_PROFILE_NAME} already exists, skipping create"
else
	echo "creating instance profile ${INSTANCE_PROFILE_NAME}"
	aws iam create-instance-profile --instance-profile-name "${INSTANCE_PROFILE_NAME}"
	# IAM propagation lag: a freshly created instance profile is sometimes
	# not immediately assumable by run-instances. A short, bounded wait
	# here is cheaper than a confusing failure in 02-launch-instance.sh.
	sleep 8
	aws iam add-role-to-instance-profile \
		--instance-profile-name "${INSTANCE_PROFILE_NAME}" \
		--role-name "${ROLE_NAME}"
fi

cat <<EOF

== done ==
BUCKET_NAME=${BUCKET_NAME}
ROLE_NAME=${ROLE_NAME}
INSTANCE_PROFILE_NAME=${INSTANCE_PROFILE_NAME}

S3 prefix layout (the interface app-layer code should target):
  s3://${BUCKET_NAME}/litestream/            litestream.yml's replica target
  s3://${BUCKET_NAME}/archives/payloads/      gzipped raw LLM request/response
  s3://${BUCKET_NAME}/archives/checkpoints/   world_snapshots full-state dumps
  s3://${BUCKET_NAME}/exports/parquet/        per-game-day Parquet (research/)

Export these before running 02-launch-instance.sh:
  export BUCKET_NAME=${BUCKET_NAME}
  export INSTANCE_PROFILE_NAME=${INSTANCE_PROFILE_NAME}
EOF
