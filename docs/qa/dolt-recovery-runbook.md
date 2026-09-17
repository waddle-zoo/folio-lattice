# Dolt failure recovery rehearsal

This runbook covers a Dolt server incident in which `bd`/`gt` status hangs for
more than 30 seconds and an auto-started rig-local impostor occupies port
3311. It is an operator procedure, not a license to reset or bootstrap the
production data plane.

## Capture before intervention

1. Record the UTC time and the exact command that hung. Do not run a blind
   `gt dolt stop && gt dolt start`.
2. Capture a goroutine dump from the configured town server without killing it:

   ```sh
   kill -QUIT "$(cat ~/gt/.dolt-data/dolt.pid)"
   gt dolt status 2>&1 | tee "/tmp/dolt-hang-$(date +%s).log"
   gt escalate -s HIGH "Dolt: status hung >30s; diagnostics captured"
   ```

3. Identify the process currently listening on 3311 and record PID, command,
   current working directory, and data-dir arguments. On macOS use `lsof -nP
   -iTCP:3311 -sTCP:LISTEN` followed by `ps -ww -p PID -o pid=,ppid=,command=`;
   on Linux also record `/proc/PID/cwd` and `/proc/PID/cmdline`.

## Isolate the impostor

The configured production server must use `~/gt/.dolt-data` (or the exact
data-dir reported by the town configuration). A process whose cwd/data-dir is
inside `gastown/folio_lattice/.beads/dolt` is an impostor and may not be used
for recovery. Verify both paths before sending a signal. Terminate only that
identified impostor PID, then confirm that 3311 is free; never delete files,
especially anything under a `.dolt` directory, and never remove `noms/LOCK`.

Start the exact configured town server with `gt dolt start`. Confirm its PID,
cwd, data-dir, and port with `gt dolt status`; if those do not match, stop and
escalate rather than bootstrapping another server.

## Verification and recovery evidence

After the configured server is healthy, verify both production databases and
read access before any write:

```sh
gt dolt status
dolt --data-dir "$GT_DOLT_DATA" sql -q "SHOW DATABASES"
BEADS_DIR="$GT_BEADS_DIR" bd list --limit 1
BEADS_DIR="$FOLIO_BEADS_DIR" bd list --limit 1
```

Run the named, isolated backup/restore rehearsal for the release candidate and
record its manifest/checksums, artifact/version/graph/tenant/ACL/audit counts,
and elapsed RPO/RTO. The rehearsal must use a temporary data-dir and fixture
databases; it must not point at `~/gt/.dolt-data`, any rig `.beads/dolt`, or a
shared production volume. Attach the transcript to G8 and keep `.23`/`.24`
open until the clean restore and hosted runtime rerun are independently
verified.

The executable companion `tests/test_dolt_recovery_runbook.py` checks that the
operator procedure retains the pre-intervention diagnostics, path identity,
impostor-only termination, and isolated-fixture constraints without touching a
real Dolt data directory.
