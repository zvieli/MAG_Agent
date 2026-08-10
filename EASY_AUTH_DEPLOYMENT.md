# Azure Easy Auth and SPFx Deployment Runbook

This runbook cuts the production Function App over from application-managed JWT
validation to Azure App Service Authentication (Easy Auth). SPFx is the only
authenticated production client. `/api/ping` is public liveness; all other routes
require the delegated `user_impersonation` scope.

## 1. Retire the exposed Function key

The Function key captured in the deployment diagnostics is compromised. Renew or
delete it in Function App > Functions > App keys before continuing, redact every
retained copy of the diagnostics, and do not add the replacement to SPFx properties,
package assets, browser storage, or support logs. Easy Auth bearer authentication is
the only production browser authentication mechanism.

## 2. Resolve and record tenant values

Run these read-only commands in Azure Cloud Shell while signed into the production
tenant. Replace the resource group and Function App placeholders first.

```bash
BACKEND_CLIENT_ID="026e9069-414c-4df4-8513-bdeb9ff301d2"
RESOURCE_GROUP="<resource-group>"
FUNCTION_APP="<function-app>"

az account show --query '{tenantId:tenantId,subscription:id}' -o json
az ad sp show --id "$BACKEND_CLIENT_ID" --query '{displayName:displayName,appId:appId,id:id}' -o json
az ad sp list --filter "displayName eq 'SharePoint Online Client Extensibility Web Application Principal'" \
  --query '[].{displayName:displayName,appId:appId,id:id}' -o json
az webapp auth show --resource-group "$RESOURCE_GROUP" --name "$FUNCTION_APP" -o json
az functionapp cors show --resource-group "$RESOURCE_GROUP" --name "$FUNCTION_APP" -o json
```

Stop before packaging unless the backend display name is exactly
`MAG-Agent-Backend-API`. If it differs, update `resource` in
`frontend/mag-spfx/config/package-solution.json` to the exact live display name.
Record the SharePoint principal's `appId`; Easy Auth needs the application ID, not
the tenant-specific service-principal object ID. Confirm the same value appears as
`azp` (v2 token) or `appid` (v1 token) in a controlled SPFx access-token diagnostic.
Never log or commit the token itself.

Export a recoverable snapshot of the Function App's current Authentication and
CORS settings before changing them.

## 3. Deploy and approve SPFx 0.1.5

```bash
cd frontend/mag-spfx
npm run test:auth
sha256sum sharepoint/solution/mag-spfx.sppkg
```

Use Node 22 for any SPFx build. The approved `0.1.5` artifact is already generated;
do not rebuild it immediately before this recovery upload because package timestamps
change its checksum. Run `npm run package:ship` only when generating a deliberately
new artifact, then record and approve that artifact's new checksum.

Upload `sharepoint/solution/mag-spfx.sppkg` to the tenant App Catalog, deploy it,
and approve the pending `MAG-Agent-Backend-API / user_impersonation` request under
SharePoint Admin Center > Advanced > API access. Replace the installed package and
verify that a cloud request sends `Authorization: Bearer ...` and never sends
`x-functions-key` before continuing.

Before upload, verify the release identity:

```text
SHA-256: 2708b8c4182cea796364ba0f8e726e26236be7ef12cead2ce746ebd5d0080bc3
Solution ID: b071a1d2-aa81-47ac-9849-768c2e54f213
Version: 0.1.5.0
Bundle: mag-app-web-part_eadc7fc94fd4022de255.js
```

The catalog's deployment error column must be empty. If the target site uses a
site collection App Catalog or has a site-installed app shadowing the tenant
package, replace and upgrade that same source and installation. The `0.1.5.0`
version intentionally forces an explicit upgrade from any earlier `0.1.4` upload.

## 4. Stage Easy Auth without enforcing the gateway

In Function App > Settings > Authentication:

1. Add or edit the Microsoft identity provider using the existing backend App
   Registration and enable the token store.
2. Set the tenant requirement to the production workforce tenant.
3. Set allowed token audiences to both the backend client ID and its exact
   Application ID URI (`api://026e9069-414c-4df4-8513-bdeb9ff301d2`, unless the
   live registration reports a different URI).
4. Set allowed client applications to only the resolved SharePoint Client
   Extensibility `appId`.
5. Temporarily select **Allow unauthenticated access**.

Deploy the backend release with `REQUIRE_ENTRA_AUTH=true`. A valid SPFx call must
reach the Function with a parsed principal and log only `tid`, `oid`, `scp`, and
`azp`. Missing/malformed principals must receive application JSON `401`, while a
principal without the exact scope receives JSON `403`.

## 5. Enforce Easy Auth and CORS

Return to Authentication settings and select:

- Require authentication.
- HTTP 401 Unauthorized for unauthenticated requests.
- Excluded path: exactly `/api/ping`.

Restrict Function App CORS:

```bash
az functionapp cors remove --resource-group "$RESOURCE_GROUP" --name "$FUNCTION_APP" --allowed-origins '*'
az functionapp cors add --resource-group "$RESOURCE_GROUP" --name "$FUNCTION_APP" \
  --allowed-origins 'https://tikshuv.sharepoint.com'
az functionapp cors show --resource-group "$RESOURCE_GROUP" --name "$FUNCTION_APP" -o json
```

Do not enable credential support. The deployed host/application allowlist contains
only `Authorization`, `Content-Type`, `Content-Disposition`, `x-mag-file-name`,
`x-mag-trace-id`, `Cache-Control`, and `Pragma`, with `GET`, `POST`, and `OPTIONS`.

Configure Azure App Service Health check and external liveness monitors to call
`/api/ping`. Monitoring that needs dependency details must call `/api/health` using
the SPFx flow; no separate automation application is allowlisted.

## 6. Production acceptance

Verify in order:

1. Tokenless `GET /api/ping` returns `200`; tokenless `GET /api/health` returns
   platform `401`.
2. Wrong issuer/audience tokens are rejected; a valid token from a non-allowlisted
   client receives platform `403`.
3. A valid SPFx principal without `user_impersonation` receives application `403`.
4. An unauthenticated `OPTIONS` preflight from
   `https://tikshuv.sharepoint.com` succeeds with `2xx` and all required headers.
   A disallowed origin receives no `Access-Control-Allow-Origin`. Stop the rollout
   if the authorized preflight fails.
5. Upload, duplicate preflight, Durable polling, graph, report, timeline, and chat
   all complete without `x-functions-key`.

Run acceptance from a normal page using the installed catalog package in a new
private browser session. Remove `debugManifestsFile`, `debug`, localhost, and
workbench parameters. If the old asset remains, clear SharePoint service-worker and
site data, then reopen the page. The console fingerprint must report component
version `0.1.5` and bundle `mag-app-web-part_eadc7fc94fd4022de255.js`.

## Rollback

If principal injection, preflight, or SPFx traffic fails, set Easy Auth back to
**Allow unauthenticated access** and redeploy the previous JWT-validating backend.
Keep SPFx 0.1.5 installed: it already sends bearer tokens and remains compatible
with the prior validator. Restore the saved Authentication/CORS snapshot if the
failure is configuration-related. Do not restore the retired Function key as an
authentication fallback.
