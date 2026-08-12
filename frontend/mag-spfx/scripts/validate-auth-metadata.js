'use strict';

const fs = require('fs');
const path = require('path');

const projectRoot = path.resolve(__dirname, '..');
const readJson = (relativePath) => JSON.parse(fs.readFileSync(path.join(projectRoot, relativePath), 'utf8'));
const fail = (message) => {
  throw new Error(`[auth-metadata] ${message}`);
};

const packageJson = readJson('package.json');
const release = readJson('release-manifest.json');
const solution = readJson('config/package-solution.json');
const manifest = readJson('src/webparts/magApp/MagAppWebPart.manifest.json');
const webPartSource = fs.readFileSync(
  path.join(projectRoot, 'src/webparts/magApp/MagAppWebPart.ts'),
  'utf8'
);
const tokenServiceSource = fs.readFileSync(
  path.join(projectRoot, 'src/webparts/magApp/services/TokenService.ts'),
  'utf8'
);

if (packageJson.version !== release.packageVersion) {
  fail(`package version must match release-manifest.json (${release.packageVersion}), received ${packageJson.version}`);
}

if (packageJson.engines?.node !== '>=22.14.0 <23.0.0') {
  fail(`Node engine must be >=22.14.0 <23.0.0, received ${packageJson.engines?.node}`);
}

if (solution.solution.id !== release.solutionId) {
  fail(`solution ID must match release-manifest.json (${release.solutionId})`);
}

if (solution.solution.version !== release.solutionVersion) {
  fail(`solution version must match release-manifest.json (${release.solutionVersion}), received ${solution.solution.version}`);
}

const configuredPackagePath = path.posix.join('sharepoint', solution.paths.zippedPackage);
if (configuredPackagePath !== release.packagePath) {
  fail(`package path must match release-manifest.json (${release.packagePath}), received ${configuredPackagePath}`);
}

if (manifest.id !== release.componentId) {
  fail(`component ID must match release-manifest.json (${release.componentId})`);
}

const permissions = solution.solution.webApiPermissionRequests || [];
if (
  permissions.length !== 1 ||
  permissions[0].resource !== 'MAG-Agent-Backend-API' ||
  permissions[0].scope !== 'user_impersonation'
) {
  fail('webApiPermissionRequests must contain only MAG-Agent-Backend-API / user_impersonation');
}

if (/^[0-9a-f-]{36}$/i.test(permissions[0].resource) || /^api:\/\//i.test(permissions[0].resource)) {
  fail('webApiPermissionRequests.resource must be the service-principal display name, not a GUID or URI');
}

const properties = manifest.preconfiguredEntries?.[0]?.properties || {};
if (properties.backendResource !== 'api://026e9069-414c-4df4-8513-bdeb9ff301d2') {
  fail('manifest backendResource must be the canonical backend App ID URI');
}

if ('backendClientId' in properties || 'backendAppIdUri' in properties) {
  fail('deprecated backend fields must not appear in preconfigured manifest properties');
}

for (const legacyField of ['backendClientId', 'backendAppIdUri']) {
  const propertyPanePattern = new RegExp(`PropertyPaneTextField\\(['\"]${legacyField}['\"]`);
  if (propertyPanePattern.test(webPartSource)) {
    fail(`${legacyField} must not appear in the Property Pane`);
  }
}

if (tokenServiceSource.includes('DEFAULT_BACKEND_CLIENT_ID')) {
  fail('TokenService must not contain a hardcoded backend client-ID fallback');
}

if (/SPFXApp_(?:Client_ID|ID_URI)/.test(`${webPartSource}\n${tokenServiceSource}`)) {
  fail('frontend App Registration values must not participate in SPFx token acquisition');
}

console.log('[auth-metadata] SPFx authentication metadata is valid.');
