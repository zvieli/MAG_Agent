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
- `functionKey`: Function App API key for authenticated endpoints.
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

## Quality & Type Checks

```bash
./node_modules/.bin/tsc --noEmit
npm run build
```
