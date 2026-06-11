# TypeScript SDK parity: `getOrCreate`

## What needs to be done

The Python SDK (`Daytona` and `AsyncDaytona`) now exposes a `get_or_create` method.  The
TypeScript SDK (`Daytona` in `src/Daytona.ts`) needs a matching `getOrCreate` method to
achieve feature parity.

### Proposed signature

```typescript
/**
 * Returns an existing Sandbox matching the idempotency key, or creates a new one.
 *
 * Match key — at least one of `params.name` or `params.labels` must be set:
 *   - `params.name` (preferred): single GET lookup by name.
 *   - `params.labels`: list all sandboxes with matching labels (superset match).
 *
 * Lifecycle handling:
 *   started             → return immediately
 *   starting/creating/restoring → wait until ready, then return
 *   stopped/archived/archiving  → start, wait, then return
 *   error/build_failed  → throw DaytonaError (or recreate if recreateOnError=true)
 *   destroyed/other     → treated as absent; a new sandbox is created
 *
 * Multiple matches (labels path): newest is returned, a warning is logged.
 *
 * @param params          Sandbox spec. `params.name` or `params.labels` must be set.
 * @param options.timeout Seconds to wait for creation/start (default 60).
 * @param options.recreateOnError  If true, delete an error sandbox and create fresh.
 * @returns The existing or newly created Sandbox (always in started state).
 */
public async getOrCreate(
  params: CreateSandboxFromSnapshotParams | CreateSandboxFromImageParams,
  options?: {
    timeout?: number
    onSnapshotCreateLogs?: (chunk: string) => void
    recreateOnError?: boolean
  },
): Promise<Sandbox>
```

### Implementation checklist

1. **Input validation** — throw `DaytonaValidationError` if `params.labels` is absent
   or empty.

2. **Concurrency** — JS/TS is single-threaded in the event loop, so a simple
   `Map<string, Promise<Sandbox>>` keyed by the sorted-label fingerprint is
   sufficient to coalesce concurrent in-flight calls.  No explicit mutex is needed;
   store the in-flight Promise and return it to subsequent callers with the same key.
   Remove the entry from the map once the Promise settles.

   ```typescript
   private readonly _getOrCreateInflight = new Map<string, Promise<Sandbox>>()

   private _labelKey(labels: Record<string, string>): string {
     return JSON.stringify(Object.fromEntries(Object.entries(labels).sort()))
   }
   ```

3. **List** — call `this.list({ labels: params.labels })` and collect sandboxes in
   `started` or `stopped` state.

4. **Restart** — if the match is `stopped`, call `await sandbox.start(timeout)`.
   If the match is `starting`, await `sandbox.waitForSandboxStart()`.

5. **Multiple matches** — sort by `createdAt` (newest first), emit
   `console.warn(...)`, return the first.

6. **Create** — if no live match, call the existing `create(params, options)`.

7. **Tests** — mirror the Python test suite in
   `src/__tests__/GetOrCreate.test.ts`:
   - first call creates
   - second call returns existing
   - stopped sandbox is restarted
   - multiple matches → warning + newest
   - empty labels throws
   - concurrent calls share the inflight Promise (no double-create)

### Files to change

| File | Change |
|---|---|
| `src/Daytona.ts` | Add `getOrCreate`, `_labelKey`, `_getOrCreateInflight` |
| `src/index.ts` | No change needed (public methods are exported via the class) |
| `src/__tests__/GetOrCreate.test.ts` | New test file (7+ test cases) |
| `README.md` | Add `getOrCreate` example in the "Quick start" section |
