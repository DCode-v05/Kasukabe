#!/usr/bin/env bash
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
BUCKET_NAME="${BUCKET_NAME:-kasukabe-genesis-${ACCOUNT_ID}-${REGION}}"
ROLE_NAME="${ROLE_NAME:-kasukabe-genesis-host-role}"
POLICY_NAME="${POLICY_NAME:-kasukabe-genesis-host-policy}"
INSTANCE_PROFILE_NAME="${INSTANCE_PROFILE_NAME:-kasukabe-genesis-host-profile}"

echo "== account ${ACCOUNT_ID} · region ${REGION} · bucket ${BUCKET_NAME} =="

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

for prefix in litestream/ archives/payloads/ archives/checkpoints/ exports/parquet/; do
	aws s3api put-object --bucket "${BUCKET_NAME}" --key "${prefix}.keep" --region "${REGION}" >/dev/null
done

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
