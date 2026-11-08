# Engine container seccomp profile

`engine.json` is the seccomp profile applied to the prod `engine` service:

```yaml
# docker-compose.yml
security_opt:
  - seccomp=./seccomp/engine.json
```

## What it is

Docker's **default** seccomp profile (moby `v25.0.5`
`profiles/seccomp/default.json`) **plus** one unconditional allow rule for the
namespace/mount syscalls bubblewrap needs to build the inner sandbox without
`CAP_SYS_ADMIN`:

```
clone, clone3, unshare, setns, mount, umount2, pivot_root, keyctl,
mount_setattr, open_tree, move_mount, fsopen, fsconfig, fsmount, fspick
```

Everything else is unchanged from the Docker default (`defaultAction:
SCMP_ACT_ERRNO`). This keeps container-level seccomp protection for the trusted
engine process while letting bwrap run.

The **untrusted agent code is not governed by this profile** — it runs one ring
deeper under the strict inner seccomp BPF blocklist in `src/sandbox/seccomp.py`,
inside a fresh user/mount/pid/net namespace created by bwrap.

## Why it's needed

Docker's default profile blocks the unprivileged user-namespace `clone()` and
`mount`/`pivot_root`, so without this profile bwrap fails with
*"No permissions to create new namespace"*. Two companion settings in
`docker-compose.yml` complete the picture:

- `systempaths=unconfined` — Docker masks `/proc`, which "locks" the proc mount
  and stops bwrap mounting a fresh `/proc`; unmasking lets the inner sandbox
  build its own proc view.
- `tmpfs: [/tmp:exec]` — the sandbox writes a shell script under `/tmp` and bwrap
  `execve()`s it; a default `noexec` tmpfs makes every bash run fail.

## Regenerate

```
python seccomp/build_profile.py
```

Then re-test that bwrap still works under the full hardening (see the sandbox
section of the Atlas, `architecture/sections/50_sandbox.md`).
