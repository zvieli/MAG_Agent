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

- `useMockData`: Toggles mock service providers for local UI testing without a live backend.
- `pollIntervalMs`: Durable Functions orchestration polling interval in milliseconds.
- `enableDebugLogs`: Enables verbose request logging in the browser developer console.

Backend and Entra values are deployment configuration, not Web Part properties and
not source-code defaults. Supply these public values to the build environment (or
the existing untracked `frontend/app/.env.local` input):

```bash
cp ../app/.env.example ../app/.env.local
```

```text
MAG_API_BASE_URL=https://<function-app>.azurewebsites.net
MAG_ENTRA_TENANT_ID=78820852-55fa-450b-908d-45c0d911e76b
MAG_ENTRA_CLIENT_ID=14b4e437-8b19-4950-9150-a46ab8d626b8
MAG_ENTRA_AUTHORITY=https://login.microsoftonline.com/78820852-55fa-450b-908d-45c0d911e76b
MAG_ENTRA_BACKEND_SCOPE=api://026e9069-414c-4df4-8513-bdeb9ff301d2/user_impersonation
MAG_ENTRA_REDIRECT_URI=https://tikshuv.sharepoint.com/_forms/spfxsinglesignon.aspx
```

The app fails closed if an authentication value is missing or invalid. These are
identifiers, not secrets. Never expose a client secret, Function key, token, or
sensitive payload through client environment variables.

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

The Web Part uses one `@azure/msal-browser@4.25.1`
`PublicClientApplication` with Authorization Code + PKCE. Startup selects only a
cached account from the configured tenant, or attempts `ssoSilent` using the
SharePoint user's address. If interaction is required, a Hebrew Auth Gate blocks the
application until its button invokes `loginPopup`. Backend calls never invoke a
popup: they use only `acquireTokenSilent` and are blocked before `fetch` or XHR when
a token is absent.

A shared authentication boundary owns all pipeline, graph, report, timeline, and
chat transport. It removes caller-supplied Authorization and supplies its own Bearer
token. Exact HTTP(S) `localhost`, `127.0.0.1`, and `::1` URLs remain tokenless for
local development. There is no fallback to SharePoint Client Extensibility.

Configure the Function App with the authentication settings already used by the repository's `.env`:

```text
REQUIRE_ENTRA_AUTH=true
AZURE_Tenant_ID=<tenant-guid>
AZURE_CLIENT_ID=026e9069-414c-4df4-8513-bdeb9ff301d2
AZURE_Application_ID_URI=api://026e9069-414c-4df4-8513-bdeb9ff301d2
```

The backend requires delegated `user_impersonation`. The SPA registration must have
that permission with admin consent. The SPFx package has no
`webApiPermissionRequests`; SharePoint Admin Center API access does not participate.

Easy Auth owns issuer, tenant, audience, signature, and calling-client validation.
Keep the independent SPA Client ID in Allowed Client Applications and do not add the
SharePoint principal as fallback. The Function reads `X-MS-CLIENT-PRINCIPAL` and
enforces `user_impersonation`. See the [deployment runbook](../../EASY_AUTH_DEPLOYMENT.md).

MSAL is pinned to v4 because the supplied SharePoint SSO redirect page does not host
the redirect bridge required by MSAL v5. A v5 upgrade requires a dedicated callback.

For local acceptance, verify that `temp/build/manifests.js` reports component version
`0.1.7`, that `dist/mag-app-web-part.js` loads from `https://localhost:4321`, and
that the console runtime fingerprint reports both values. For live acceptance, use
the deployed SharePoint package and verify the fingerprint reports component version
and bundle recorded in `release-manifest.json`. Verify that `pipeline-preflight`
contains a Bearer token and returns 200, then exercise upload, orchestration polling,
graph/report loading, and chat. Force token acquisition to fail once and confirm that
no backend request is created. A manual request without a token must return Easy Auth
`401`; a valid token without the delegated scope must return application JSON `403`.
In normal and private SharePoint windows, verify the authorize request uses the
configured client and scope and the backend sees that client as `azp` with
`scp=user_impersonation`. Stop deployment on `AADSTS50011` or when the popup does not
return; Azure must provide a compatible callback before any MSAL v5 migration.

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
