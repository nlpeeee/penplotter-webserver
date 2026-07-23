# PCP V2 preview

PCP V2 is served alongside the immutable V1 interface. It uses the same
server-side geometry, projects, job queue, serial lock, database, uploads, and
configuration. V2 introduces no runtime-data migration.

## Routes

- `/v1` — frozen V1.
- `/v2` — New Cut.
- `/v2/workbench` — guided workbench.
- `/v2/projects` — project library and immutable revision access.
- `/v2/jobs` — active, queued, and historical jobs with technical activity.
- `/v2/settings` — connection, profiles, calibration, automation, and system.
- `/api/ui-state` — read-only cutter, port, serial-operation, active-job,
  progress, queue, reset, and latest connection-error state.

V2 is marked **V2 Preview** while `/` defaults to V1.

## Operator workflow

1. Import one SVG, one HPGL, or a batch of SVG artwork from New Cut.
2. Use Design for size and transforms, Layout for copies and roll placement,
   and Prepare for optimization and cutting aids.
3. Use Cut to resolve linked readiness checks.
4. Generate HPGL. This does not start the cutter.
5. Send the current HPGL only when the configured port is available.

An edit after generation marks the output stale. The Send action stays
disabled until HPGL is regenerated from the current server preview hash.
Compensation and calibration test transmissions retain their additional
operator confirmation.

The global active-job strip and **CANCEL CUT** action remain available across
V2 screens. Commands already buffered by the cutter can continue after the Pi
stops transmitting.

Phones provide monitoring, queue/cancellation, settings/reset access, and
read-only preview. Use a tablet or desktop for precise geometry editing.

## Build and verification

V2 dependencies are vendored into its versioned static namespace:

```bash
npm install
npm run build
npx playwright install chromium
npm run test:browser
python -m unittest discover -s tests -v
```

The V1 stylesheet is not an output of the V2 build.

## Preview deployment

Deploy the feature branch under the existing `/root/webplotter` checkout and
restart `webplotter.service`. Do not set `PCP_UI_DEFAULT` during preview
deployment; `/` therefore remains V1 and `/v2` is opt-in.

No ordinary deployment or smoke test sends data to the physical cutter.

## Acceptance, cutover, and rollback

After operator acceptance:

1. Back up live runtime data again.
2. Configure `PCP_UI_DEFAULT=v2` for `webplotter.service`.
3. Restart the service and verify `/`, `/v1`, and `/v2`.
4. Tag the accepted commit `v2.0.0`; never move `v1.0.0`.

Rollback changes `PCP_UI_DEFAULT` to `v1` and restarts the service. No database,
project, queue, or cutter rollback is required.
