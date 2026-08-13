'use strict';

const fs = require('fs');
const path = require('path');

const projectRoot = path.resolve(__dirname, '..');
const readJson = (relativePath) => JSON.parse(fs.readFileSync(path.join(projectRoot, relativePath), 'utf8'));
const readSource = (relativePath) => fs.readFileSync(path.join(projectRoot, relativePath), 'utf8');
const fail = (message) => { throw new Error(`[auth-metadata] ${message}`); };

const packageJson = readJson('package.json');
const release = readJson('release-manifest.json');
const solution = readJson('config/package-solution.json');
const manifest = readJson('src/webparts/magApp/MagAppWebPart.manifest.json');
const webPartSource = readSource('src/webparts/magApp/MagAppWebPart.ts');
const authenticationSource = readSource('src/webparts/magApp/auth/AuthenticationService.ts');
const authConfigSource = readSource('src/webparts/magApp/auth/authConfig.ts');

if (packageJson.version !== '0.1.7' || packageJson.version !== release.packageVersion) {
  fail(`package version must be 0.1.7 and match release-manifest, received ${packageJson.version}`);
}
if (packageJson.dependencies?.['@azure/msal-browser'] !== '4.25.1') {
  fail('@azure/msal-browser must be pinned exactly to 4.25.1');
}
if (packageJson.engines?.node !== '>=22.14.0 <23.0.0') {
  fail(`Node engine must be >=22.14.0 <23.0.0, received ${packageJson.engines?.node}`);
}
if (solution.solution.id !== release.solutionId || solution.solution.version !== release.solutionVersion) {
  fail('solution ID and version must match release-manifest.json');
}
const configuredPackagePath = path.posix.join('sharepoint', solution.paths.zippedPackage);
if (configuredPackagePath !== release.packagePath) fail('package path must match release-manifest.json');
if (manifest.id !== release.componentId) fail('component ID must match release-manifest.json');

if ('webApiPermissionRequests' in solution.solution) {
  fail('webApiPermissionRequests must be absent for the independent MSAL client');
}
if (JSON.stringify(manifest.supportedHosts) !== JSON.stringify(['SharePointWebPart'])) {
  fail('supportedHosts must contain SharePointWebPart only');
}

const properties = manifest.preconfiguredEntries?.[0]?.properties || {};
for (const field of ['backendResource', 'backendClientId', 'backendAppIdUri', 'apiBaseUrl']) {
  if (field in properties) fail(`${field} must not be persisted in manifest properties`);
  const propertyPanePattern = new RegExp(`PropertyPaneTextField\\(['"]${field}['"]`);
  if (propertyPanePattern.test(webPartSource)) fail(`${field} must not appear in the Property Pane`);
}

const sourceTree = `${webPartSource}\n${authenticationSource}\n${authConfigSource}`;
if (/AadTokenProvider|aadTokenProviderFactory/.test(sourceTree)) {
  fail('SPFx AadTokenProvider must not participate in authentication');
}
for (const key of [
  'MAG_ENTRA_TENANT_ID',
  'MAG_ENTRA_CLIENT_ID',
  'MAG_ENTRA_BACKEND_SCOPE',
  'MAG_ENTRA_REDIRECT_URI',
  'MAG_ENTRA_AUTHORITY'
]) {
  if (!authConfigSource.includes(`process.env.${key}`)) fail(`auth config must read ${key}`);
}
if (!authenticationSource.includes('new PublicClientApplication') ||
    !authenticationSource.includes('acquireTokenSilent') ||
    !authenticationSource.includes('loginPopup') ||
    !authenticationSource.includes('ssoSilent')) {
  fail('MSAL authentication service is incomplete');
}

console.log('[auth-metadata] SPFx authentication metadata is valid.');
