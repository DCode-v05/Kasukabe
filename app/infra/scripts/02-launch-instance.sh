#!/usr/bin/env bash
# Kasukabe host-bringup, step 2 (RUNBOOK.md section 4).
#
# Run from the OPERATOR'S LOCAL MACHINE, after 01-setup-s3-and-iam.sh.
# Launches the genesis host: m5.metal, ON-DEMAND (not spot - TASK spec is
# explicit this must not be reclaimed mid-run), 96 vCPU / 384 GiB,
# $4.848/hr in ap-south-1, persistent gp3 EBS root, in a security group
# that only ever opens SSH (to one IP), 80, and 443 to the internet.
#
# Requires:
#   - BUCKET_NAME, INSTANCE_PROFILE_NAME exported from step 1's output.
#   - KEY_NAME: an EXISTING EC2 key pair in ap-south-1. This script does
#     NOT create one - minting and storing an SSH private key is a
#     conscious, one-time operator action, not something to hide inside
#     a script. If you don't have one yet:
#       aws ec2 create-key-pair --region ap-south-1 \
#         --key-name kasukabe-genesis --query 'KeyMaterial' --output text \
#         > ~/.ssh/kasukabe-genesis.pem
#       chmod 400 ~/.ssh/kasukabe-genesis.pem
#     then export KEY_NAME=kasukabe-genesis before running this script.
#   - OPERATOR_IP: your current public IP in CIDR form (curl ifconfig.me),
#     e.g. export OPERATOR_IP=203.0.113.7/32 - SSH is locked to this
#     single address, not 0.0.0.0/0.
set -euo pipefail

: "${BUCKET_NAME:?export BUCKET_NAME from step 1 first}"
: "${INSTANCE_PROFILE_NAME:?export INSTANCE_PROFILE_NAME from step 1 first}"
: "${KEY_NAME:?export KEY_NAME to an existing EC2 key pair name (see script header)}"
: "${OPERATOR_IP:?export OPERATOR_IP=<your-ip>/32 (curl ifconfig.me)}"

REGION="${AWS_REGION:-ap-south-1}"
INSTANCE_NAME="${INSTANCE_NAME:-kasukabe-genesis-host}"
SG_NAME="${SG_NAME:-kasukabe-genesis-sg}"
ROOT_VOLUME_GB="${ROOT_VOLUME_GB:-500}"

echo "== region ${REGION} · instance ${INSTANCE_NAME} =="

# ── default VPC + subnet (the m5.metal host has no need for a custom
# VPC topology - this is a single self-contained box) ──────────────────
VPC_ID="$(aws ec2 describe-vpcs --region "${REGION}" \
	--filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)"
if [ "${VPC_ID}" = "None" ] || [ -z "${VPC_ID}" ]; then
	echo "no default VPC in ${REGION} - create one or set VPC_ID/SUBNET_ID manually" >&2
	exit 1
fi
SUBNET_ID="${SUBNET_ID:-$(aws ec2 describe-subnets --region "${REGION}" \
	--filters Name=vpc-id,Values="${VPC_ID}" Name=default-for-az,Values=true \
	--query 'Subnets[0].SubnetId' --output text)}"

# ── security group ───────────────────────────────────────────────────
SG_ID="$(aws ec2 describe-security-groups --region "${REGION}" \
	--filters Name=group-name,Values="${SG_NAME}" Name=vpc-id,Values="${VPC_ID}" \
	--query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)"
if [ -z "${SG_ID}" ] || [ "${SG_ID}" = "None" ]; then
	echo "creating security group ${SG_NAME}"
	SG_ID="$(aws ec2 create-security-group --region "${REGION}" \
		--group-name "${SG_NAME}" --vpc-id "${VPC_ID}" \
		--description "Kasukabe genesis host: SSH (one IP) + HTTP/HTTPS only" \
		--query 'GroupId' --output text)"
	aws ec2 authorize-security-group-ingress --region "${REGION}" --group-id "${SG_ID}" \
		--ip-permissions \
		"IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=${OPERATOR_IP},Description=operator-ssh}]" \
		"IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0,Description=acme-http-01}]" \
		"IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0,Description=https}]"
	# Deliberately NOT opening 8100/8200/8300/8400 here. World, broker,
	# game, and console all bind 127.0.0.1 (or 0.0.0.0 for broker, reached
	# only by the Firecracker bridge network on this same host - see
	# systemd/kasukabe-broker.service) and are only reachable through
	# Caddy's reverse proxy on 443, or from this box itself.
else
	echo "security group ${SG_NAME} already exists (${SG_ID}), skipping create"
fi

# ── latest Ubuntu 22.04 LTS amd64 AMI via the official SSM parameter -
# avoids a hardcoded AMI id going stale ────────────────────────────────
AMI_ID="$(aws ssm get-parameter --region "${REGION}" \
	--name /aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
	--query 'Parameter.Value' --output text)"
echo "AMI: ${AMI_ID}"

# ── launch: m5.metal, on-demand (no --instance-market-options = on-demand,
# TASK spec: "deliberately not spot, it must not be reclaimed mid-run"),
# persistent gp3 root sized for 250 microVM rootfs images + the SQLite
# trace DB + logs, DeleteOnTermination=false so the disk survives an
# accidental instance termination until an operator confirms S3 has the
# final Litestream/Parquet copies (RUNBOOK.md teardown section) ─────────
BLOCK_DEVICE_MAPPINGS=$(cat <<EOF
[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":${ROOT_VOLUME_GB},"VolumeType":"gp3","Iops":6000,"Throughput":500,"DeleteOnTermination":false}}]
EOF
)

INSTANCE_ID="$(aws ec2 run-instances --region "${REGION}" \
	--image-id "${AMI_ID}" \
	--instance-type m5.metal \
	--key-name "${KEY_NAME}" \
	--subnet-id "${SUBNET_ID}" \
	--security-group-ids "${SG_ID}" \
	--iam-instance-profile Name="${INSTANCE_PROFILE_NAME}" \
	--block-device-mappings "${BLOCK_DEVICE_MAPPINGS}" \
	--tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${INSTANCE_NAME}},{Key=project,Value=kasukabe}]" \
	--metadata-options "HttpTokens=required" \
	--count 1 \
	--query 'Instances[0].InstanceId' --output text)"
echo "instance: ${INSTANCE_ID} (waiting for it to enter 'running')"
aws ec2 wait instance-running --region "${REGION}" --instance-ids "${INSTANCE_ID}"

# ── Elastic IP: a stable address to point the two DNS records at, and
# one that survives a stop/start (not that this instance should ever
# stop mid-run - persistent EBS root means it CAN restart cleanly if it
# ever needs to, without losing the disk) ───────────────────────────────
ALLOC_ID="$(aws ec2 allocate-address --region "${REGION}" --domain vpc \
	--tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=${INSTANCE_NAME}}]" \
	--query 'AllocationId' --output text)"
aws ec2 associate-address --region "${REGION}" --instance-id "${INSTANCE_ID}" --allocation-id "${ALLOC_ID}" >/dev/null
PUBLIC_IP="$(aws ec2 describe-addresses --region "${REGION}" --allocation-ids "${ALLOC_ID}" \
	--query 'Addresses[0].PublicIp' --output text)"

cat <<EOF

== done ==
INSTANCE_ID=${INSTANCE_ID}
PUBLIC_IP=${PUBLIC_IP}
ALLOC_ID=${ALLOC_ID}
SG_ID=${SG_ID}

Next (RUNBOOK.md section 5):
  1. Point DNS A records at ${PUBLIC_IP}:
       kasukabe.september.wtf         -> ${PUBLIC_IP}
       console.kasukabe.september.wtf -> ${PUBLIC_IP}
  2. SSH in once cloud-init settles (~1-2 min after 'running'):
       ssh -i ~/.ssh/${KEY_NAME}.pem ubuntu@${PUBLIC_IP}
  3. Save these for teardown.sh:
       export INSTANCE_ID=${INSTANCE_ID}
       export ALLOC_ID=${ALLOC_ID}
       export SG_ID=${SG_ID}
EOF
