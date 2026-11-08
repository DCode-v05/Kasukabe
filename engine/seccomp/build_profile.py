#!/usr/bin/env python3
"""Regenerate engine.json = Docker default seccomp + the syscalls bubblewrap needs.

Usage:  python seccomp/build_profile.py

Docker's default seccomp profile blocks the unprivileged user-namespace clone()
plus mount/pivot_root, so bwrap cannot build the inner sandbox without
CAP_SYS_ADMIN. This script takes the official moby default profile and adds one
unconditional allow rule for exactly the namespace/mount syscalls bwrap needs.
Everything else stays as the Docker default (defaultAction: SCMP_ACT_ERRNO).

The untrusted agent code is NOT governed by this profile — it runs one ring
deeper under the strict inner seccomp BPF blocklist in src/sandbox/seccomp.py.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

DEFAULT_URL = "https://raw.githubusercontent.com/moby/moby/v25.0.5/profiles/seccomp/default.json"

ALLOW = [
    "clone", "clone3", "unshare", "setns",
    "mount", "umount2", "pivot_root",
    "keyctl", "mount_setattr", "open_tree", "move_mount",
    "fsopen", "fsconfig", "fsmount", "fspick",
]


def main() -> None:
    raw = subprocess.run(
        ["curl", "-fsSL", DEFAULT_URL], check=True, capture_output=True,
    ).stdout
    profile = json.loads(raw)

    allowset = set(ALLOW)
    out = []
    for sc in profile["syscalls"]:
        names = [n for n in sc.get("names", []) if n not in allowset]
        if not names:
            continue
        sc["names"] = names
        out.append(sc)

    out.insert(0, {"names": ALLOW, "action": "SCMP_ACT_ALLOW"})
    profile["syscalls"] = out

    dest = pathlib.Path(__file__).with_name("engine.json")
    dest.write_text(json.dumps(profile, indent=2) + "\n")
    print(f"wrote {dest} ({len(out)} syscall groups, defaultAction={profile['defaultAction']})")


if __name__ == "__main__":
    main()
