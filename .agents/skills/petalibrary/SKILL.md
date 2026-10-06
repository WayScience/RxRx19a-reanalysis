______________________________________________________________________

## name: petalibrary description: 'Use before mounting, transferring to or from, or reasoning about CU Boulder/Anschutz PetaLibrary storage from local machines or Alpine, including sshfs, Globus, rclone, quotas, access paths, and data-handling rules.'

# PetaLibrary Skill

Use this note before mounting, transferring to/from, or reasoning about quotas
and access methods for CU Boulder/Anschutz's PetaLibrary storage service, as
used from a local machine or from Alpine. See
`.agents/skills/alpine/SKILL.md` for general Alpine/SSH/Slurm knowledge — this
file covers PetaLibrary specifically and cross-references that file rather than repeating it. For measured
performance numbers, see `docs/storage-mount-benchmark.md` and the
reproducible script at `examples/benchmark_storage_mount.sh` — this skill
intentionally does not carry exact benchmark figures itself, since they're
time/location/session-specific and go stale fast; re-run the script rather
than trusting a number recorded here. It does carry a rough, order-of-
magnitude expectation for off-campus-VPN vs. on-campus speed below, since
that's useful for planning a transfer even without a fresh benchmark run.

**Data handling rule, same as `.agents/skills/isilon/SKILL.md`: never record real
allocation/directory names, file names, or directory listings from inside
`/pl/active` (or any other PetaLibrary path) in this skill, in commit
messages, or anywhere else committed to the repo.** Allocation subdirectory
names under `/pl/active` are lab/group-identifying, and contents may include
Confidential or Highly Confidential data. Disposable, clearly-marked test
files/dirs are fine to create and immediately delete for validation.

## Current Position

- PetaLibrary is a CU Boulder Research Computing service for storing,
  archiving, and sharing research data — separate from Alpine's own
  `/scratch/alpine` and `/projects` filesystems, but reachable from the same
  login node. See What PetaLibrary Is.
- `/pl/active` is a real, listable, top-level directory on
  `login.rc.colorado.edu` containing per-group/per-project subdirectories
  (confirmed via plain `ssh`: 255 entries, mixed ownership/permissions). This
  project's specific allocation subdirectory has since been identified and
  confirmed reachable, but its name is intentionally not recorded here — see
  the data handling rule above.
- sshfs-mounting `/pl/active` at `~/mnt/alpine` is now fully working end to
  end — the FUSE approval blocker noted earlier in this file was cleared
  (presumably by the user completing the interactive macOS approval step),
  and this project's allocation subdirectory is listable and writable
  through it. See sshfs Access for the mount command. Note: at last check the
  live mount layout had shifted to separate `~/mnt/alpine/active` and
  `~/mnt/alpine/archive` subpaths rather than `~/mnt/alpine` itself being
  `/pl/active` directly — worth confirming current layout with `mount | grep macfuse` before relying on the exact path below, since this file hasn't
  been re-validated against that change.
- Don't reuse the `ssh-alpine` zsh alias for sshfs or any non-shell tool. It's
  a shell alias, not an SSH config entry, so tools that exec `ssh` directly
  (sshfs, rsync, some Globus/rclone flows) can't resolve it. Use the
  `~/.ssh/config` `Host alpine` entry created for this project instead — see
  sshfs Access.
- Globus and rclone are both real, CURC-documented access paths for Alpine
  data generally, and PetaLibrary's own allocation-types documentation lists
  Globus, rsync, and sftp as supported access methods for both Active and
  Archive tiers (Active also adds SMB). Neither has been validated against a
  PetaLibrary path specifically in this project yet — the source workshop
  material demos Globus against `/projects/<user>` and rclone against a
  OneDrive remote, not `/pl`. Treat both sections below as documented-but-
  unverified-for-PetaLibrary until tested. See Globus Access and rclone
  Access.

## What PetaLibrary Is

*Source: colorado.edu/rc/resources/petalibrary, curc.readthedocs.io
allocation_types page.*

PetaLibrary is a University of Colorado Boulder Research Computing service
for storage, archival, and sharing of research data, available as an
allocation (not provisioned automatically with an Alpine account). Requesting
one is a three-step process: review the allocation-tier documentation, submit
a request via a DocuSign form, then consult the user documentation to start
using it. Support: the RC Support Request Form, or `rc-help@colorado.edu`.

**Important default:** a single PetaLibrary allocation is one unprotected copy
of data with no backup by default — protection/backup is a property of which
tier you choose, not automatic.

## Allocation Tiers

*Source: curc.readthedocs.io allocation_types page. Costs are CU System,
FY27 rates as published there; re-check the live page before quoting a price,
since rates change by fiscal year.*

| Tier           | Cost (CU System, FY27) | Compute node access                                  | Login node access | Object/file limit        | Redundancy                                  | Compression | Access methods           |
| -------------- | ---------------------- | ---------------------------------------------------- | ----------------- | ------------------------ | ------------------------------------------- | ----------- | ------------------------ |
| Active         | $48/TB/yr              | Yes (Alpine, Blanca, Open OnDemand), interactive NFS | Yes               | None                     | Double parity RAID                          | lz4         | Globus, rsync, sftp, SMB |
| Archive        | $28/TB/yr              | No                                                   | Yes, interactive  | 10,000 files/dirs per TB | Triple parity RAID                          | zstd        | Globus, rsync, sftp      |
| Active+Archive | $76/TB/yr              | Yes (via the Active side)                            | Yes               | None                     | Both tiers' protections                     | Both        | Both tiers' methods      |
| Archive+DR     | $44/TB/yr              | No                                                   | Yes               | Same as Archive          | Triple parity RAID + monthly offsite backup | zstd        | Globus, rsync, sftp      |

Notes:

- Active+Archive replicates data between the two tiers automatically via
  ZFS-snapshot-based replication, on a 15-minute cycle — not something you
  manage yourself.
- Archive+DR adds a monthly offsite backup copy of the archive allocation to a
  separate data center, on top of Archive's triple-parity protection.
- All tiers include monthly checksum validation and snapshot capability.
- External (non-CU) customers pay materially more (e.g. $115/TB/yr for
  Active-only, vs. $48 for CU System).
- **Archive tiers are not mountable from Alpine compute nodes.** If a
  Nextflow/Slurm job on `acpu` needs to read or write PetaLibrary data
  directly, it needs an Active or Active+Archive allocation, or the data needs
  to be staged to `/scratch/alpine` or `/projects` first via a login-node-side
  transfer (Globus/rsync/sftp/sshfs).

## sshfs Access

*Scope: general — this is the access method validated furthest in this
project so far, from a local macOS machine.*

### Why the `ssh-alpine` alias doesn't work for sshfs

`ssh-alpine` (defined in `~/.zshrc`) is a zsh alias wrapping:

```bash
ssh -i ~/.ssh/alpine dabu57888@xsede.org@login.rc.colorado.edu
```

sshfs execs the `ssh` binary directly — it never goes through zsh, so it
can't resolve shell aliases. Worse, the username itself contains a literal
`@` (`dabu57888@xsede.org`), which breaks the plain `user@host:path` sshfs
argument syntax. Two things that do **not** work around this, both tested:

- `sshfs ... -o User='dabu57888@xsede.org' ...` — rejected outright by this
  system's sshfs/macFUSE build with `fuse: unknown option(s): -o User=dabu57888@xsede.org`. `User` isn't in its recognized ssh-option
  passthrough list; don't assume ssh_config option names pass through
  sshfs `-o` flags just because they're valid for `ssh` itself.
- Percent-encoding the `@` in the remote spec
  (`dabu57888%40xsede.org@login.rc.colorado.edu:/pl/active`) — sshfs (version
  `3.7.5`, Homebrew/macFUSE build) passes the string to `ssh` **without**
  decoding it, so `ssh` receives the literal username
  `dabu57888%40xsede.org` and authentication fails with `Permission denied`.
  Don't rely on percent-encoding for this sshfs build.

### What works: an SSH config Host entry

Add a `Host` block to `~/.ssh/config` (created fresh for this project — none
existed before):

```text
Host alpine
    HostName login.rc.colorado.edu
    User dabu57888@xsede.org
    IdentityFile ~/.ssh/alpine
```

`chmod 600 ~/.ssh/config` afterward. This makes both `ssh alpine` and
`sshfs alpine:/path ...` resolve correctly with no escaping needed, since the
odd username and identity file live in config instead of the command line.
Validated: `ssh -o BatchMode=yes alpine "echo ok"` succeeds, and
`ssh alpine "ls -la /pl/active"` lists real content (255 entries, mixed
ownership — a mix of world-readable, root-owned directories and a smaller
number of group-restricted ones at `drwxrws---.`-style permissions).

### Mount command and local convention

This machine already uses a `~/mnt/<name>` convention for other remote
mounts — some are active `smbfs` mounts to other storage systems (see
`.agents/skills/isilon.md`), others exist as prepared-but-empty mount points.
Use `~/mnt/alpine` for PetaLibrary rather than inventing a new location:

```bash
mkdir -p ~/mnt/alpine   # already exists on this machine
sshfs alpine:/pl/active ~/mnt/alpine \
  -o reconnect \
  -o ServerAliveInterval=15 \
  -o volname=PetaLibrary
```

### Former blocker (resolved): FUSE mount didn't register when launched non-interactively

Running the mount command from a non-interactive/sandboxed subprocess (e.g. a
coding agent's shell tool) connected and authenticated successfully — `ps`
showed the `sshfs` process alive and sleeping — but the mount never appeared
in `mount`/`df -h`, even with the sandbox disabled for that one call. The
macFUSE kernel extension was already loaded
(`io.macfuse.filesystems.macfuse.25 (5.1.3)` per `kmutil showloaded`), so it
wasn't a totally-missing-extension problem. This matched the known macFUSE
behavior of requiring a one-time interactive approval (System Settings →
Privacy & Security system-extension prompt, and/or Full Disk Access for the
terminal app actually running the command) that can only be granted from a
real interactive terminal session, not a tool-invoked subprocess.

**Resolved**: after the user ran the same `sshfs` command directly in their
own interactive terminal (presumably clearing a permission prompt there), the
mount now works from both interactive and non-interactive contexts without
re-prompting. If this ever regresses (e.g. after a macOS/macFUSE update),
re-run the mount command interactively once to clear it again.

### To unmount

```bash
umount ~/mnt/alpine
# or, if that fails:
diskutil unmount ~/mnt/alpine
```

**Caution**, per a real incident on the Isilon side of this project (see
`.agents/skills/isilon.md` and `docs/storage-mount-benchmark.md`): unmounting
a live, in-use mount to force a cold-cache benchmark read can leave it unable
to remount non-interactively, even when the original mount command worked
fine moments before. Don't unmount this PetaLibrary mount just to chase a
benchmark number if it's otherwise in active use. For measured
listing/write/read timing on this mount, see
`docs/storage-mount-benchmark.md` and re-run
`examples/benchmark_storage_mount.sh` rather than trusting a stale number.

## Expected Speeds: Off-Campus VPN vs. On-Campus

*Scope: general expectation, not a guarantee. Based on one client machine's
measurements in `docs/storage-mount-benchmark.md` (greater Denver, CO,
off-campus over VPN vs. on-campus with no VPN, sshfs to the same allocation
subdirectory both times). Treat this as "what order of magnitude to expect,"
and re-run `examples/benchmark_storage_mount.sh` for a current number before
relying on anything more precise.*

- **Off-campus, over VPN (or via the Alpine login-node SSH hop generally):**
  expect sequential write throughput in the **low tens of MB/s** (~16.8 MB/s
  measured, one session — VPN involvement for this leg specifically wasn't
  independently confirmed, so treat this figure as the "SSH-to-Alpine over a
  home/off-campus network" case broadly, not strictly VPN-gated the way
  Isilon is).
- **On-campus:** expect roughly **4-5x faster** sequential write throughput
  than the off-campus case — around **77-82 MB/s** measured, same
  allocation subdirectory, same client.
- Small-directory listing/creation stayed sub-second in both conditions
  (roughly 0.02-0.07s on-campus vs. up to ~1.5s off-campus for listing a
  large shared root) — write throughput is the more load-bearing number for
  planning a transfer.
- If a Globus/rclone/sshfs transfer feels unexpectedly slow off-campus,
  network path (VPN or otherwise) is a more likely explanation than a
  PetaLibrary-side problem — on-campus was consistently faster by a wide
  enough margin (~4-5x) that it's the dominant variable, not the storage
  backend.
- This says nothing about read throughput (not validly measured in either
  condition — the read step is always cache-inflated, see To Unmount above)
  or behavior under concurrent access/many-small-files/larger transfers.
- Isilon and PetaLibrary tracked each other reasonably closely within each
  condition (see `.agents/skills/isilon.md` for its own version of this
  section, and `docs/storage-mount-benchmark.md` for the numbers) — this is
  about location/network, not a claim that one storage system beats the
  other.

## Globus Access

*Scope: general Alpine data transfer. Documented from CU Anschutz's
"Globus and rclone data transfer on Alpine" workshop deck (Kevin Fotso,
2025-09-08) — not yet validated against a PetaLibrary path in this project;
the deck's own demo transfers into `/projects/<user>@xsede.org`, not `/pl`.*

Globus is a managed data-transfer platform (GridFTP for bulk transfer, HTTPS
for small downloads) that works "directly from your own storage system,"
including a laptop.

Setup flow from the deck:

1. Confirm Alpine access via Open OnDemand
   (`https://ondemand-rmacc.rc.colorado.edu`) → CILogon → identity provider
   **"ACCESS CI (XSEDE)"** → log in with **ACCESS-CI credentials, not CU
   Anschutz SSO** → DUO push.
1. Log into `https://www.globus.org/` → "Use your existing organizational
   login" → **"ACCESS CI (formerly XSEDE)"** → same ACCESS-CI credentials →
   DUO push recommended. Verify under Settings that the linked identity ends
   in `@access-ci.org`.
1. Install Globus Connect Personal locally
   (`https://docs.globus.org/globus-connect-personal/install/mac/` for
   macOS), log in, then create your own personal collection/endpoint (owner
   identity, collection name, description; optional "High Assurance" toggle
   for PHI/CUI data).
1. From the personal endpoint's "Open in File Manager" view: pick the local
   folder, "Transfer or Sync to...", then in the destination panel search
   Collection for **"CU Boulder Research Computing"** — the CURC-managed
   Globus collection (GCS, backed by `DTN23`, owner `colorado@globusid.org`).
   There is also a **"CU Boulder Research Computing ACCESS (Legacy - Do Not
   Use)"** collection — don't use it. Enter the destination path (the deck
   uses `/projects/<alpine-username>@xsede.org`; for PetaLibrary this would
   presumably be a `/pl/active/<allocation>` or `/pl/archive/<allocation>`
   path — unconfirmed, see Open Questions) and click Start.
1. To share with a collaborator who has no Alpine account, they install
   Globus locally and you set up a Globus guest collection (referenced by the
   deck, not walked through step by step).

**Recommendation from the deck, general Alpine guidance:** never use Open
OnDemand's web-based Upload/Download for files over 11GB — use Globus or
rclone instead.

## rclone Access

*Scope: general Alpine data transfer. Same source deck as Globus above — the
deck's rclone demo configures a `onedrive` remote type and never targets
`/pl`, so nothing here is PetaLibrary-specific yet. CURC's own rclone
reference (linked from the deck, not itself fetched for this skill):
`https://curc.readthedocs.io/en/latest/compute/data-transfer.html#rclone`.*

rclone is a CLI tool for managing files on cloud storage (transfer, backup,
restore), usable against many storage backends via `rclone config` remotes.

Local install (macOS, as shown in the deck):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install rclone
which rclone
```

On Alpine (compute node, not login node — get one via `acompile`):

```bash
acompile --ntasks=4 --time=12:00:00
module load rclone
rclone config
```

Interactive wizard (values shown are for the deck's OneDrive example; the
remote name/type would differ for a PetaLibrary-facing remote, e.g. `sftp` or
`webdav` against the PetaLibrary host — not yet determined for this project):

- `n` for new remote, give it a name
- storage type (deck used `onedrive`)
- leave `client_id`/`client_secret` blank unless required
- region `global` (backend-specific)
- **`n` for "Use auto config?"** — required since Alpine is headless; this
  prints an `rclone authorize "<remote>"` command to run locally instead
- on the local machine: `rclone authorize <remote>` opens a browser, log in,
  get a success page, then copy the full JSON token it prints back into
  Alpine's `config_token>` prompt
- finish any remote-specific prompts (e.g. `config_type`, drive selection)

Verify and use:

```bash
rclone listremotes
rclone ls <remote>:
rclone copy --progress <remote>:<source-path> <local-folder>
```

(`copy`, not `sync`: `rclone sync` deletes destination-only files, so a
typo in the source path would wipe the local copy. Sync is only for
deliberate mirror-style uploads, never for downloads.)

Can be wrapped in a batch job, e.g.:

```bash
#!/bin/bash
#SBATCH --job-name=rclone
#SBATCH --time=00:30:00
#SBATCH --output=rclone.out
#SBATCH --error=rclone.err
#SBATCH --qos=normal
#SBATCH --partition=amilan
#SBATCH --nodes=1
#SBATCH --ntasks=4
#SBATCH --mail-type=BEGIN,FAIL,END
#SBATCH --mail-user=<user>@cuanschutz.edu

module load rclone
rclone copy --progress <remote>:<source-path> <local-folder>
```

Note the deck's example partition (`amilan`) is a predecessor/sibling
partition name, not `acpu` — see `.agents/skills/alpine.md` Slurm Defaults for
this project's current CPU partition/QOS naming and the note that Alpine
partition names have changed before.

**Security hygiene called out in the deck:** delete the rclone remote config
when done (`rclone config` → `d` → select remote → confirm) rather than
leaving a stored access token around.

## Important Failure Modes

- Do not use the `ssh-alpine` zsh alias as a host argument for sshfs, rsync,
  or any tool that execs `ssh` directly rather than going through a shell —
  it will not resolve. Use the `~/.ssh/config` `Host alpine` entry.
- Do not pass `-o User=...` to this system's sshfs build expecting it to
  reach `ssh` — it's rejected as an unrecognized FUSE option. Put `User` in
  `~/.ssh/config` instead.
- Do not percent-encode an `@`-containing username directly in an sshfs
  `user@host:path` argument and assume it will be decoded — this sshfs build
  (`3.7.5`) passes it through literally, causing a silent-looking
  `Permission denied` that has nothing to do with the SSH key itself.
- Do not run the actual `sshfs` mount command from a non-interactive
  agent/subprocess and expect it to succeed on first use — macFUSE's
  interactive approval step (system-extension prompt / Full Disk Access) can
  only be satisfied in a real interactive terminal session. A process that
  connects and authenticates but never shows up in `mount`/`df` is this
  failure mode, not an auth or config problem.
- Do not assume an Archive or Archive+DR PetaLibrary allocation can be
  mounted or read directly from an Alpine compute node (`acpu`) — those tiers
  are login-node-only. Stage data to `/scratch/alpine` or `/projects` first
  if a Slurm/Nextflow job needs it.
- Do not use Open OnDemand's web Upload/Download for anything over ~11GB —
  use Globus or rclone.

## Open Questions

- This project's actual allocation subdirectory under `/pl/active` is now
  identified and confirmed reachable/writable (name intentionally not
  recorded here). Worth updating the sshfs mount command to target that
  specific subdirectory directly rather than the whole `/pl/active` tree, if
  day-to-day use doesn't actually need visibility into other groups'
  allocations.
- Which PetaLibrary tier does this project actually hold (Active, Archive,
  Active+Archive, Archive+DR)? Determines whether direct compute-node access
  is possible at all, or whether every real run needs a staging step.
- What's the correct Globus destination path and rclone remote type/host for
  this project's specific PetaLibrary allocation? The source deck never
  targets `/pl` directly — both need a real test against this project's
  actual allocation before being treated as validated.
- Does CURC publish a PetaLibrary-specific rclone remote type (sftp? webdav?
  s3-compatible?) anywhere, as opposed to the generic cloud-storage examples
  in the workshop deck?
