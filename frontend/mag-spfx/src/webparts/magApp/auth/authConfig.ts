import { Configuration } from '@azure/msal-browser';

export interface AuthRuntimeConfiguration {
  tenantId: string;
  clientId: string;
  backendScope: string;
  redirectUri: string;
  authority: string;
  msal: Configuration;
}

const guidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const readPublicEnvironment = (): Record<string, string | undefined> => ({
  tenantId: process.env.MAG_ENTRA_TENANT_ID,
  clientId: process.env.MAG_ENTRA_CLIENT_ID,
  backendScope: process.env.MAG_ENTRA_BACKEND_SCOPE,
  redirectUri: process.env.MAG_ENTRA_REDIRECT_URI,
  authority: process.env.MAG_ENTRA_AUTHORITY
});

export const loadAuthConfiguration = (): AuthRuntimeConfiguration | undefined => {
  const values = readPublicEnvironment();
  const tenantId = values.tenantId?.trim();
  const clientId = values.clientId?.trim();
  const backendScope = values.backendScope?.trim();
  const redirectUri = values.redirectUri?.trim();
  const authority = values.authority?.trim().replace(/\/+$/, '');

  if (!tenantId || !clientId || !backendScope || !redirectUri || !authority) return undefined;
  if (!guidPattern.test(tenantId) || !guidPattern.test(clientId)) return undefined;
  if (!/^api:\/\/[0-9a-f-]{36}\/[A-Za-z0-9._-]+$/i.test(backendScope)) return undefined;

  let parsedRedirect: URL;
  let parsedAuthority: URL;
  try {
    parsedRedirect = new URL(redirectUri);
    parsedAuthority = new URL(authority);
  } catch {
    return undefined;
  }
  if (parsedRedirect.protocol !== 'https:' || parsedAuthority.protocol !== 'https:') return undefined;

  return {
    tenantId,
    clientId,
    backendScope,
    redirectUri,
    authority,
    msal: {
      auth: {
        clientId,
        authority,
        redirectUri,
        navigateToLoginRequestUrl: false
      },
      cache: {
        cacheLocation: 'sessionStorage'
      }
    }
  };
};
