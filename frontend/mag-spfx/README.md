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

`serve` performs the debug build itself; do not run a separate `gulp bundle`
before it. With current Chrome versions, grant the SharePoint origin the site
permission named **Loopback network** or **Local network access** so the hosted
workbench can load assets from `localhost`. If the permission is managed or hidden,
use an unmanaged development profile or request the corresponding enterprise-policy
allowlist from IT rather than disabling browser security.

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
- `backendResource`: Canonical backend App ID URI (`api://026e9069-414c-4df4-8513-bdeb9ff301d2`). Saved legacy `backendClientId` and `backendAppIdUri` values are accepted only for migration and normalized to this URI.
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

The SPFx token provider receives the backend's exact App ID URI through `backendResource`; the delegated scope and `/.default` are not appended to that resource. A shared authentication boundary owns the actual `fetch` and XHR transports for pipeline, graph, report, timeline, and chat calls. For every non-loopback API URL, token acquisition and a final Bearer-header assertion complete before the native network primitive is invoked; token acquisition failures and backend 401/403 responses surface as sanitized security errors in the console and UI. Exact HTTP(S) `localhost`, `127.0.0.1`, and `::1` URLs remain tokenless for local development, where the backend must explicitly set `REQUIRE_ENTRA_AUTH=false`.

SPFx uses the tenant's Microsoft-managed SharePoint Online Client Extensibility Web Application Principal as its OAuth client. A separately provisioned frontend App Registration is not used by `AadTokenProvider`, is not passed to token acquisition, and must not replace the SharePoint principal in Easy Auth's allowed client applications.

Configure the Function App with the authentication settings already used by the repository's `.env`:

```text
REQUIRE_ENTRA_AUTH=true
AZURE_Tenant_ID=<tenant-guid>
AZURE_CLIENT_ID=026e9069-414c-4df4-8513-bdeb9ff301d2
AZURE_Application_ID_URI=api://026e9069-414c-4df4-8513-bdeb9ff301d2
```

The backend requires the fixed delegated scope `user_impersonation`; it is separate from the Application ID URI. Production must keep `REQUIRE_ENTRA_AUTH=true`; omitting the setting also fails closed. The preconfigured Web Part manifest supplies `backendResource`; the TypeScript token service has no hardcoded fallback.

The package requests `user_impersonation` from the backend resource display name `MAG-Agent-Backend-API`. Before packaging, DevOps must confirm that this remains the exact service-principal display name. Deploy the package and approve its pending permission in SharePoint Admin Center API access; never replace `resource` with a client or object GUID.

Easy Auth owns issuer, tenant, audience, signature, and calling-client validation. The Function reads its trusted `X-MS-CLIENT-PRINCIPAL` header and enforces `user_impersonation`. Configure Easy Auth to require authentication with a `401` response, exclude only `/api/ping`, and allowlist the tenant's SharePoint Online Client Extensibility application. See the repository [deployment runbook](../../EASY_AUTH_DEPLOYMENT.md).

For local acceptance, verify that `temp/build/manifests.js` reports component version
`0.1.6`, that `dist/mag-app-web-part.js` loads from `https://localhost:4321`, and
that the console runtime fingerprint reports both values. For live acceptance, use
the deployed SharePoint package and verify the fingerprint reports component version
and bundle recorded in `release-manifest.json`. Verify that `pipeline-preflight`
contains a Bearer token and returns 200, then exercise upload, orchestration polling,
graph/report loading, and chat. Force token acquisition to fail once and confirm that
no backend request is created. A manual request without a token must return Easy Auth
`401`; a valid token without the delegated scope must return application JSON `403`.

## Quality & Type Checks

```bash
nvm use
./node_modules/.bin/tsc --noEmit
npm run test:auth
npm run build
npm run package:ship
REQUIRE_SPFX_PACKAGE=1 ../../.venv/bin/pytest -q ../../tests/test_spfx_package_metadata.py
```

`package:ship` cleans generated assets before bundling so an older hashed bundle cannot be carried into the new `.sppkg`.
After producing the final package, update `release-manifest.json` with its SHA-256
and hashed bundle. This file is the only release identity record; the deployable
artifact is always `sharepoint/solution/mag-spfx.sppkg`.
