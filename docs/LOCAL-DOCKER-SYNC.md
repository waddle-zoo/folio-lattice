# Local Docker sync

The local test stack can follow the latest fast-forwardable `origin/main`
commit with:

```sh
make docker-sync
```

The watcher checks once per minute. When the remote SHA changes it:

1. fetches `origin/main`;
2. fast-forwards the checkout only when the worktree is clean;
3. rebuilds and starts both Compose services; and
4. checks the control and renderer health endpoints before recording the SHA
   as ready.

Run one synchronization cycle instead:

```sh
make docker-sync-once
```

Useful overrides:

```sh
FOLIO_SYNC_INTERVAL=15 make docker-sync
FOLIO_SYNC_BRANCH=main FOLIO_SYNC_REMOTE=origin make docker-sync
```

The watcher refuses to pull when there are local edits, refuses to merge a
diverged branch, and retries after Git, Docker, or health-check failures. Stop
it with `Ctrl-C`. This is a local development/testing workflow: automatically
executing every new commit is not an appropriate production deployment or a
release-approval process.
