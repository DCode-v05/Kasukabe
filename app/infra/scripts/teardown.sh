#!/usr/bin/env bash
set -euo pipefail

: "${INSTANCE_ID:?export INSTANCE_ID}"
: "${ALLOC_ID:?export ALLOC_ID}"
: "${SG_ID:?export SG_ID}"

REGION="${AWS_REGION:-ap-south-1}"
NUKE_ALL=0
[ "${1:-}" = "--nuke-all" ] && NUKE_ALL=1

echo "== terminating ${INSTANCE_ID} =="
aws ec2 terminate-instances --region "${REGION}" --instance-ids "${INSTANCE_ID}" >/dev/null
aws ec2 wait instance-terminated --region "${REGION}" --instance-ids "${INSTANCE_ID}"

echo "== releasing Elastic IP ${ALLOC_ID} =="
aws ec2 release-address --region "${REGION}" --allocation-id "${ALLOC_ID}"

if [ "${NUKE_ALL}" -eq 0 ]; then
	cat <<EOF

== instance + EIP torn down. Left in place (billed, but cheap): ==
  - EBS root volume (still tagged to the terminated instance - find it
    with: aws ec2 describe-volumes --region ${REGION} --filters Name=status,Values=available)
  - security group ${SG_ID}
  - S3 bucket, IAM role/instance profile/policy (from 01-setup-s3-and-iam.sh)

Re-run 02-launch-instance.sh with the same BUCKET_NAME/INSTANCE_PROFILE_NAME
to bring a fresh host back up against the same S3 data. Pass --nuke-all
to this script instead if you are fully done with the experiment.
EOF
	exit 0
fi

echo "== --nuke-all: deleting security group, orphaned volume, IAM role/profile =="

aws ec2 delete-security-group --region "${REGION}" --group-id "${SG_ID}" || true

VOLUME_IDS="$(aws ec2 describe-volumes --region "${REGION}" \
	--filters "Name=tag:Name,Values=kasukabe-genesis-host" "Name=status,Values=available" \
	--query 'Volumes[].VolumeId' --output text)"
for vol in ${VOLUME_IDS}; do
	echo "deleting orphaned volume ${vol}"
	aws ec2 delete-volume --region "${REGION}" --volume-id "${vol}"
done

ROLE_NAME="${ROLE_NAME:-kasukabe-genesis-host-role}"
POLICY_NAME="${POLICY_NAME:-kasukabe-genesis-host-policy}"
INSTANCE_PROFILE_NAME="${INSTANCE_PROFILE_NAME:-kasukabe-genesis-host-profile}"
aws iam remove-role-from-instance-profile --instance-profile-name "${INSTANCE_PROFILE_NAME}" --role-name "${ROLE_NAME}" || true
aws iam delete-instance-profile --instance-profile-name "${INSTANCE_PROFILE_NAME}" || true
aws iam delete-role-policy --role-name "${ROLE_NAME}" --policy-name "${POLICY_NAME}" || true
aws iam delete-role --role-name "${ROLE_NAME}" || true

if [ -n "${BUCKET_NAME:-}" ]; then
	echo "== --nuke-all also empties and deletes s3://${BUCKET_NAME} - this is your only copy of the run's data =="
	read -r -p "type the bucket name to confirm PERMANENT deletion: " CONFIRM
	if [ "${CONFIRM}" = "${BUCKET_NAME}" ]; then
		aws s3 rm "s3://${BUCKET_NAME}" --recursive
		aws s3api delete-bucket --region "${REGION}" --bucket "${BUCKET_NAME}"
	else
		echo "confirmation did not match - leaving bucket ${BUCKET_NAME} in place"
	fi
else
	echo "BUCKET_NAME not set - leaving any S3 bucket in place (export BUCKET_NAME and re-run --nuke-all to also delete it)"
fi

echo "== nuke-all complete =="
