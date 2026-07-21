# MAG SPFx Frontend

This SharePoint Framework web part is the user interface for MAG Agent. It supports uploading investigation PDFs, tracking pipeline progress, reviewing graph/report/timeline/completeness outputs, and chatting with the query agent.

## Stack

- SharePoint Framework 1.23
- React 17
- TypeScript 5.3
- Fluent UI 8
- Cytoscape.js
- `marked` for Markdown rendering

## Local Development

```bash
npm install
npm run trust-cert
npm run serve
```

The dev server exposes the manifest at:

```text
https://localhost:4321/temp/build/manifests.js
```

Open the SharePoint workbench with:

```text
https://<tenant>.sharepoint.com/_layouts/15/workbench.aspx?debugManifestsFile=https%3A%2F%2Flocalhost%3A4321%2Ftemp%2Fbuild%2Fmanifests.js&loadSPFX=true
```

## Web Part Properties

- `apiBaseUrl`: Function App base URL, for example `http://localhost:7071` or `https://<app>.azurewebsites.net`.
- `functionKey`: optional Function key for development/test environments. This is visible in browser tools and is not a production auth model.
- `useMockData`: toggles local mock services instead of live API calls.
- `pollIntervalMs`: Durable status polling interval.
- `enableDebugLogs`: enables frontend debug logs in the browser console.

## Backend Contracts Used By The UI

- `GET /api/health`
- `GET /api/pipeline-preflight?filename=<filename>`
- `POST /api/pipelines`
- `GET /api/pipelines/{instanceId}`
- `POST /api/query`
- `GET /api/cases/{docId}/graph`
- `GET /api/cases/{docId}/timeline`
- `GET /api/cases/{docId}/report`
- `GET /api/cases/{docId}/communities`
- `GET /api/cases/{docId}/completeness`

Pipeline and query requests send:

```http
x-mag-trace-id: <client-generated-trace-id>
```

## Observability

When `enableDebugLogs` is on, the frontend logs request/response groups with trace IDs. The same trace IDs are sent to the backend and can be searched in Function logs or App Insights.

Repeated identical chat questions may be served from browser cache and will not always start a backend query. Use a new question or clear browser cache when validating backend query observability.

## Checks

```bash
./node_modules/.bin/tsc --noEmit
npm run build
```
