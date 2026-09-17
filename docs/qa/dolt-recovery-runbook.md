# Dolt failure recovery rehearsal

This runbook covers a Dolt server incident in which `bd`/`gt` status hangs for
more than 30 seconds and an auto-started rig-local impostor occupies port
3311. It is an operator procedure, not a license to reset or bootstrap the
production data plane.

## Capture before intervention

1. Record the UTC time and the exact command that hung. Do not run a blind
   `gt dolt stop && gt dolt start`.
2. Resolve the configured town root/data directory from the active Gas Town
   configuration first. Do not infer it from `$HOME`, a rig, or this runbook.
   Record the configured absolute `GT_DOLT_DATA` path, PID-file path, and log
   target. Before signalling, record the listener PID, command, cwd, and
   data-dir. `SIGQUIT` is a diagnostic request, but it may terminate the Dolt
   process on the installed build; never claim it is non-terminating:

   ```sh
   # Set this from the town's configured data-dir (for example, the path
   # printed by a healthy `gt dolt status`), never by guessing from $HOME.
   GT_DOLT_DATA="/absolute/path/from-town-configuration"
   GT_DOLT_PIDFILE="$GT_DOLT_DATA/dolt.pid"
   GT_DOLT_LOG="/tmp/dolt-hang-$(date +%s).log"
   # Capture PID/command/cwd/data-dir and the log target before SIGQUIT.
   cat "$GT_DOLT_PIDFILE"
   lsof -nP -iTCP:3311 -sTCP:LISTEN
   ps -ww -p "$(cat "$GT_DOLT_PIDFILE")" -o pid=,ppid=,command=
   kill -QUIT "$(cat "$GT_DOLT_PIDFILE")"
   # Immediately check whether that PID and port survived; do not issue a
   # second stop if SIGQUIT terminated it.
   kill -0 "$(cat "$GT_DOLT_PIDFILE")" 2>/dev/null || true
   lsof -nP -iTCP:3311 -sTCP:LISTEN || true
   gt dolt status 2>&1 | tee "$GT_DOLT_LOG"
   gt escalate -s HIGH "Dolt: status hung >30s; diagnostics captured"
   ```

3. Identify the process currently listening on 3311 and record PID, command,
   current working directory, and data-dir arguments. On macOS use `lsof -nP
   -iTCP:3311 -sTCP:LISTEN` followed by `ps -ww -p PID -o pid=,ppid=,command=`;
   on Linux also record `/proc/PID/cwd` and `/proc/PID/cmdline`.

## Isolate the impostor

The configured production server must use the exact absolute data-dir reported
by town configuration. A process whose cwd/data-dir is inside
`gastown/folio_lattice/.beads/dolt` is an impostor and may not be used for
recovery. Verify both paths before sending a signal. Terminate only that
identified impostor PID, then confirm that 3311 is free; never delete files,
especially anything under a `.dolt` directory, and never remove `noms/LOCK`.

Start the exact configured town server with `gt dolt start`. Confirm its PID,
cwd, data-dir, and port with `gt dolt status`; if those do not match, stop and
escalate rather than bootstrapping another server. If the pre-signal process
exited after `SIGQUIT`, treat that as the observed failure mode and proceed
with the single exact configured start; do not send a second stop.

## Verification and recovery evidence

After the configured server is healthy, verify both production databases and
read access before any write:

```sh
gt dolt status
dolt --data-dir="$GT_DOLT_DATA" sql -q "SHOW DATABASES"
BEADS_DIR="$GT_BEADS_DIR" bd list --limit 1
BEADS_DIR="$FOLIO_BEADS_DIR" bd list --limit 1
```

Run the named, isolated backup/restore rehearsal for the release candidate and
record its manifest/checksums, artifact/version/graph/tenant/ACL/audit counts,
and elapsed RPO/RTO. The rehearsal must use a temporary data-dir and fixture
databases; it must not point at the configured production data-dir, any rig `.beads/dolt`, or a
shared production volume. Attach the transcript to G8 and keep `.23`/`.24`
open until the clean restore and hosted runtime rerun are independently
verified.

The executable companion `tests/test_dolt_recovery_runbook.py` checks that the
operator procedure retains the pre-intervention diagnostics, path identity,
impostor-only termination, and isolated-fixture constraints without touching a
real Dolt data directory.
