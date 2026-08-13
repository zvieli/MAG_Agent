# Azure Easy Auth and SPFx MSAL Deployment Runbook

This runbook deploys SPFx 0.1.7 with its independent MSAL SPA client. `/api/ping`
is public liveness; all other routes require delegated `user_impersonation`.

## 1. Security boundary

Easy Auth bearer authentication is the only production browser mechanism. Do not
put Function keys, client secrets, tokens, or sensitive payloads in SPFx properties,
source, build variables, package assets, browser storage, or support logs. The SPA
uses Authorization Code + PKCE and therefore has no client secret.

## 2. Validate Entra registrations

Run read-only checks in Azure Cloud Shell after replacing the deployment placeholders:

```bash
BACKEND_CLIENT_ID="026e9069-414c-4df4-8513-bdeb9ff301d2"
SPFX_CLIENT_ID="14b4e437-8b19-4950-9150-a46ab8d626b8"
RESOURCE_GROUP="<resource-group>"
FUNCTION_APP="<function-app>"

az account show --query '{tenantId:tenantId,subscription:id}' -o json
az ad sp show --id "$BACKEND_CLIENT_ID" --query '{displayName:displayName,appId:appId,id:id}' -o json
az ad sp show --id "$SPFX_CLIENT_ID" --query '{displayName:displayName,appId:appId,id:id}' -o json
az webapp auth show --resource-group "$RESOURCE_GROUP" --name "$FUNCTION_APP" -o json
az functionapp cors show --resource-group "$RESOURCE_GROUP" --name "$FUNCTION_APP" -o json
```

Confirm all of the following before packaging:

- Tenant ID is `78820852-55fa-450b-908d-45c0d911e76b`.
- The independent client is registered as an SPA with redirect URI
  `https://tikshuv.sharepoint.com/_forms/spfxsinglesignon.aspx`.
- It has delegated
  `api://026e9069-414c-4df4-8513-bdeb9ff301d2/user_impersonation` with admin consent.
- The same independent Client ID remains in Easy Auth Allowed Client Applications.
- There is no SharePoint Client Extensibility fallback.

Export a recoverable snapshot of Authentication and CORS before changing them.

## 3. Build SPFx 0.1.7

Use Node 22. Supply the public deployment identifiers through the build environment;
they are intentionally absent from TypeScript and Web Part properties:

```bash
export MAG_API_BASE_URL="https://<function-app>.azurewebsites.net"
export MAG_ENTRA_TENANT_ID="78820852-55fa-450b-908d-45c0d911e76b"
export MAG_ENTRA_CLIENT_ID="14b4e437-8b19-4950-9150-a46ab8d626b8"
export MAG_ENTRA_AUTHORITY="https://login.microsoftonline.com/78820852-55fa-450b-908d-45c0d911e76b"
export MAG_ENTRA_BACKEND_SCOPE="api://026e9069-414c-4df4-8513-bdeb9ff301d2/user_impersonation"
export MAG_ENTRA_REDIRECT_URI="https://tikshuv.sharepoint.com/_forms/spfxsinglesignon.aspx"

cd frontend/mag-spfx
npm run test:auth
npm run package:ship
sha256sum sharepoint/solution/mag-spfx.sppkg
```

Compare the checksum, solution version, and hashed bundle with
`release-manifest.json`. The package has no `webApiPermissionRequests`; do not wait
for or approve a SharePoint Admin Center API-access request.

Upload the package to the tenant App Catalog, deploy it, replace the installed app,
and verify that the catalog's deployment-error column is empty. If a site collection
catalog shadows the tenant package, upgrade that source and installation too.

## 4. Configure Easy Auth

In Function App > Settings > Authentication:

1. Use the existing backend App Registration as the Microsoft identity provider.
2. Require the production workforce tenant.
3. Allow the backend audience and its exact Application ID URI.
4. Allow the independent SPFx SPA Client ID as the calling application.
5. Require authentication and return HTTP 401 for unauthenticated requests.
6. Exclude exactly `/api/ping`.

The backend must keep `REQUIRE_ENTRA_AUTH=true`, trust only the Easy Auth principal,
and enforce `user_impersonation`. Logs may include `tid`, `oid`, `scp`, `azp`, and a
sanitized correlation ID, but never the bearer token or request payload.

## 5. CORS

Restrict Function App CORS to the production SharePoint origin and do not enable
credential support:

```bash
az functionapp cors remove --resource-group "$RESOURCE_GROUP" --name "$FUNCTION_APP" --allowed-origins '*'
az functionapp cors add --resource-group "$RESOURCE_GROUP" --name "$FUNCTION_APP" \
  --allowed-origins 'https://tikshuv.sharepoint.com'
az functionapp cors show --resource-group "$RESOURCE_GROUP" --name "$FUNCTION_APP" -o json
```

Allow only the required methods and headers, including `Authorization`,
`Content-Type`, `Content-Disposition`, `x-mag-file-name`, `x-mag-trace-id`,
`Cache-Control`, and `Pragma`.

## 6. Production acceptance

Test both a regular SharePoint page and a private window using the installed package,
without debug manifests, localhost, workbench, or debug query parameters:

1. Tokenless `/api/ping` returns 200; tokenless `/api/health` returns platform 401.
2. The Auth Gate appears when silent SSO is insufficient.
3. Clicking “התחברות למערכת” opens the popup and returns to the Web Part.
4. The authorize request contains Client ID
   `14b4e437-8b19-4950-9150-a46ab8d626b8` and the exact delegated scope.
5. The backend principal has that client as `azp` and `scp=user_impersonation`.
6. A non-allowlisted client is rejected and a token without the scope returns 403.
7. Authorized CORS preflight succeeds; a disallowed origin receives no allow-origin.
8. Upload, duplicate preflight, Durable polling, graph, report, timeline, and chat
   complete without `x-functions-key`.
9. Force silent acquisition to fail and confirm no backend fetch or XHR is created.

The console fingerprint must report component version 0.1.7 and the bundle recorded
in `release-manifest.json`.

Stop deployment on `AADSTS50011` or if the popup does not return to the application.
The SharePoint redirect page is not compatible with the MSAL v5 redirect bridge; a
dedicated callback must be provisioned before upgrading beyond pinned v4.25.1.

## Rollback

Restore the saved Easy Auth and CORS snapshot and redeploy the prior known-good SPFx
package. Do not restore a Function key or the SharePoint Client Extensibility OAuth
client as an authentication fallback.
