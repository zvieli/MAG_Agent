# MAG SPFx Frontend

This SharePoint Framework (SPFx) web part provides the user interface for MAG Agent. It enables uploading investigation PDFs, tracking pipeline orchestration, inspecting interactive knowledge graphs (Cytoscape.js), reviewing generated reports and timelines, auditing protocol completeness, and chatting with the query agent.

## Technology Stack

- SharePoint Framework 1.23
- React 17
- TypeScript 5.3
- Fluent UI 8
- Cytoscape.js (Interactive Knowledge Graph rendering)
- `marked` (Markdown rendering)
- IndexedDB (`browserCache`) for client-side API response caching

## Local Development & Workbench Setup

```bash
npm install
npm run trust-cert
npm run serve
```

The SPFx local dev server serves manifests from:

```text
https://localhost:4321/temp/build/manifests.js
```

Open your SharePoint Workbench:

```text
https://<tenant>.sharepoint.com/_layouts/15/workbench.aspx?debugManifestsFile=https%3A%2F%2Flocalhost%3A4321%2Ftemp%2Fbuild%2Fmanifests.js&loadSPFX=true
```

## Web Part Configuration Properties

- `apiBaseUrl`: Function App base URL (e.g. `http://localhost:7071` or `https://<app>.azurewebsites.net`).
- `functionKey`: Optional function key retained in Web Part properties for local/operational compatibility; build-time environment files never inject it into a production bundle, and it is not a production authentication fallback.
- `backendClientId`: Entra backend App Registration client ID. The default is `026e9069-414c-4df4-8513-bdeb9ff301d2`. Older saved `backendAppIdUri` values are normalized during migration.
- `useMockData`: Toggles mock service providers for local UI testing without a live backend.
- `pollIntervalMs`: Durable Functions orchestration polling interval in milliseconds.
- `enableDebugLogs`: Enables verbose request logging in the browser developer console.

## Client-Side Caching & HTTP API Contracts

`ReportService.ts` wraps all GET requests with IndexedDB client-side caching (`browserCache`, 30 minute TTL) to minimize redundant network calls:
- `GET /api/cases/{docId}/report` \(\rightarrow\) Cached in `report:${docId}`
- `GET /api/cases/{docId}/communities` \(\rightarrow\) Cached in `community_summaries:${docId}`
- `GET /api/cases/{docId}/completeness` \(\rightarrow\) Cached in `protocol_completeness:${docId}`

When on-demand agent verification is triggered via `verifyProtocolCompletenessWithAgent` (`POST /api/cases/{docId}/completeness/verify`), `ReportService` automatically invalidates the `protocol_completeness:${docId}` cache entry so updated findings load immediately upon completion.

All requests pass the client trace ID header:

```http
x-mag-trace-id: <client-generated-trace-id>
```

## Production Entra Authentication

The SPFx token provider must receive the bare backend client ID, not an App ID URI, delegated scope, or `/.default` value. A shared authentication boundary obtains complete request headers for pipeline, graph, report, timeline, and chat calls. For every non-loopback API URL, token acquisition failures stop the request before `fetch` or XHR and surface a sanitized security error in the console and UI; the client never retries without a Bearer token. Exact `localhost`, `127.0.0.1`, and `::1` URLs retain function-key-only local compatibility.

Configure the Function App with the authentication settings already used by the repository's `.env`:

```text
REQUIRE_ENTRA_AUTH=true
AZURE_Tenant_ID=<tenant-guid>
AZURE_CLIENT_ID=026e9069-414c-4df4-8513-bdeb9ff301d2
AZURE_Application_ID_URI=api://026e9069-414c-4df4-8513-bdeb9ff301d2
```

The backend requires the fixed delegated scope `user_impersonation`; it is not an additional environment setting. Production must keep `REQUIRE_ENTRA_AUTH=true`; a function key does not replace Entra JWT validation. The Web Part property and built-in client-ID default provide the frontend configuration.

Before deployment, DevOps must confirm that the backend registration exposes delegated scope `user_impersonation`, that its client ID and Application ID URI match the values above, and that admin consent was granted to the actual SPFx calling service principal. This staged package intentionally has no `webApiPermissionRequests` entry. If the bare-client-ID request still fails after that consent, add a request whose `resource` is the backend App Registration's exact Entra **display name**, redeploy, and approve it in SharePoint API Access. Never place a GUID in `resource`.

For live acceptance, use the deployed SharePoint package and verify that `pipeline-preflight` contains a Bearer token and returns 200, then exercise upload, orchestration polling, graph/report loading, and chat. A request without a token must continue to return 401. Localhost workbench certificate/loopback failures are tracked separately.

## Quality & Type Checks

```bash
./node_modules/.bin/tsc --noEmit
npm run test:auth
npm run build
npm run bundle -- --ship
npm run package-solution -- --ship
```
