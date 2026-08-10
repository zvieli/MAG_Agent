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
- `backendClientId`: Entra backend App Registration client ID. Older saved `backendAppIdUri` values are normalized during migration.
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

The SPFx token provider must receive the bare backend client ID, not an App ID URI, delegated scope, or `/.default` value. A shared authentication boundary owns the actual `fetch` and XHR transports for pipeline, graph, report, timeline, and chat calls. For every non-loopback API URL, token acquisition and a final Bearer-header assertion complete before the native network primitive is invoked; failures surface a sanitized security error in the console and UI. Exact HTTP(S) `localhost`, `127.0.0.1`, and `::1` URLs remain tokenless for local development, where the backend must explicitly set `REQUIRE_ENTRA_AUTH=false`.

Configure the Function App with the authentication settings already used by the repository's `.env`:

```text
REQUIRE_ENTRA_AUTH=true
AZURE_Tenant_ID=<tenant-guid>
AZURE_CLIENT_ID=
AZURE_Application_ID_URI=api://
```

The backend requires the fixed delegated scope `user_impersonation`; it is not an additional environment setting. Production must keep `REQUIRE_ENTRA_AUTH=true`; omitting the setting also fails closed. The Web Part property and built-in client-ID default provide the frontend configuration.

The package requests `user_impersonation` from the backend resource display name `MAG-Agent-Backend-API`. Before packaging, DevOps must confirm that this remains the exact display name of service principal ``. Deploy the package and approve its pending permission in SharePoint Admin Center API access; never replace `resource` with a client or object GUID.

Easy Auth owns issuer, tenant, audience, signature, and calling-client validation. The Function reads its trusted `X-MS-CLIENT-PRINCIPAL` header and enforces `user_impersonation`. Configure Easy Auth to require authentication with a `401` response, exclude only `/api/ping`, and allowlist the tenant's SharePoint Online Client Extensibility application. See the repository [deployment runbook](../../EASY_AUTH_DEPLOYMENT.md).

For live acceptance, use the deployed SharePoint package and first verify the console fingerprint reports component version `0.1.4` and the new hashed bundle asset. Verify that `pipeline-preflight` contains a Bearer token and returns 200, then exercise upload, orchestration polling, graph/report loading, and chat. Force token acquisition to fail once and confirm that no backend request is created. A manual request without a token must return Easy Auth `401`; a valid token without the delegated scope must return application JSON `403`.

## Quality & Type Checks

```bash
./node_modules/.bin/tsc --noEmit
npm run test:auth
npm run build
npm run package:ship
```

`package:ship` cleans generated assets before bundling so an older hashed bundle cannot be carried into the new `.sppkg`.
