import { requestErrorText } from '../src/webparts/magApp/domain/localization';
import { findMagAppBundleAsset } from '../src/webparts/magApp/domain/runtimeFingerprint';
import { BackendAuthService, isLoopbackApiUrl } from '../src/webparts/magApp/services/BackendAuthService';
import { ChatService } from '../src/webparts/magApp/services/ChatService';
import { GraphService } from '../src/webparts/magApp/services/GraphService';
import { PipelineService } from '../src/webparts/magApp/services/PipelineService';
import { ReportService } from '../src/webparts/magApp/services/ReportService';
import {
  ENTRA_AUTHENTICATION_FAILED,
  EntraAuthenticationError,
  IAuthenticationService,
  MsalAuthenticationService,
  getAuthenticationService,
  isEntraAuthenticationError,
} from '../src/webparts/magApp/auth/AuthenticationService';
import { AuthRuntimeConfiguration, loadAuthConfiguration } from '../src/webparts/magApp/auth/authConfig';

const logger = {
  enabled: true,
  createTraceId: jest.fn(() => 'trace-1'),
  info: jest.fn(),
  group: jest.fn(),
  error: jest.fn()
};

const cloudUrl = 'https://function.example';
const tenantId = '11111111-2222-4333-8444-555555555555';
const backendScope = 'api://aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee/user_impersonation';
const redirectUri = 'https://sharepoint.example/_forms/spfxsinglesignon.aspx';
const authConfig = {
  tenantId,
  clientId: '99999999-8888-4777-8666-555555555555',
  backendScope,
  redirectUri,
  authority: `https://login.microsoftonline.com/${tenantId}`,
  msal: {}
} as AuthRuntimeConfiguration;
const account = { tenantId, username: 'user@example.com', homeAccountId: 'home-1' } as any;

const authenticationWith = (getAccessToken: jest.Mock): IAuthenticationService => ({
  state: { status: 'ready' },
  initialize: jest.fn().mockResolvedValue(undefined),
  signIn: jest.fn().mockResolvedValue(undefined),
  subscribe: jest.fn(() => jest.fn()),
  getAccessToken
});

const msalClient = (overrides: Record<string, unknown> = {}): any => ({
  initialize: jest.fn().mockResolvedValue(undefined),
  handleRedirectPromise: jest.fn().mockResolvedValue(null),
  getAllAccounts: jest.fn(() => []),
  getActiveAccount: jest.fn(() => null),
  setActiveAccount: jest.fn(),
  ssoSilent: jest.fn(),
  loginPopup: jest.fn(),
  acquireTokenSilent: jest.fn(),
  ...overrides
});

const duplicateResponse = (): Response => ({
  ok: true,
  json: jest.fn().mockResolvedValue({
    status: 'duplicate',
    doc_id: 'case',
    matched_doc_id: 'case-existing'
  }),
  url: `${cloudUrl}/api/pipeline-preflight`,
  headers: { get: jest.fn() }
} as unknown as Response);

describe('SPFx Entra authentication', () => {
  let originalFetch: typeof window.fetch;

  beforeEach(() => {
    originalFetch = window.fetch;
    jest.clearAllMocks();
    window.localStorage.clear();
  });

  afterEach(() => {
    window.fetch = originalFetch;
    jest.restoreAllMocks();
  });

  it('fingerprints the most recently loaded debug or ship bundle', () => {
    expect(findMagAppBundleAsset([
      'https://publiccdn.sharepointonline.com/mag-app-web-part_66834f419d1079b546ad.js',
      'https://localhost:4321/dist/mag-app-web-part.js'
    ])).toBe('mag-app-web-part.js');
    expect(findMagAppBundleAsset([
      'https://publiccdn.sharepointonline.com/mag-app-web-part_eadc7fc94fd4022de255.js'
    ])).toBe('mag-app-web-part_eadc7fc94fd4022de255.js');
    expect(findMagAppBundleAsset([
      'https://localhost:4321/dist/mag-app-web-part.js.map',
      'not-a-url'
    ])).toBeUndefined();
  });

  it('selects a cached account from the configured tenant without creating silent SSO traffic', async () => {
    const client = msalClient({ getAllAccounts: jest.fn(() => [account]) });
    const service = new MsalAuthenticationService(client, authConfig, account.username, logger);

    await service.initialize();

    expect(service.state.status).toBe('ready');
    expect(client.setActiveAccount).toHaveBeenCalledWith(account);
    expect(client.ssoSilent).not.toHaveBeenCalled();
  });

  it('uses the SharePoint login hint for silent SSO when no tenant account is cached', async () => {
    const client = msalClient({ ssoSilent: jest.fn().mockResolvedValue({ account }) });
    const service = new MsalAuthenticationService(client, authConfig, account.username, logger);

    await service.initialize();

    expect(client.ssoSilent).toHaveBeenCalledWith(expect.objectContaining({
      loginHint: account.username,
      scopes: [backendScope],
      redirectUri
    }));
    expect(service.state.status).toBe('ready');
  });

  it('publishes interaction-required when silent SSO requires user interaction', async () => {
    const client = msalClient({
      ssoSilent: jest.fn().mockRejectedValue({ errorCode: 'login_required' })
    });
    const service = new MsalAuthenticationService(client, authConfig, account.username, logger);

    await service.initialize();

    expect(service.state).toMatchObject({ status: 'interaction-required', reason: 'interaction-required' });
  });

  it('deduplicates concurrent initialization on a single MSAL instance', async () => {
    const client = msalClient({ getAllAccounts: jest.fn(() => [account]) });
    const service = new MsalAuthenticationService(client, authConfig, account.username, logger);

    await Promise.all([service.initialize(), service.initialize(), service.initialize()]);

    expect(client.initialize).toHaveBeenCalledTimes(1);
    expect(client.handleRedirectPromise).toHaveBeenCalledTimes(1);
  });

  it('returns one shared authentication service for concurrent Web Part consumers', () => {
    const environment = {
      MAG_ENTRA_TENANT_ID: authConfig.tenantId,
      MAG_ENTRA_CLIENT_ID: authConfig.clientId,
      MAG_ENTRA_AUTHORITY: authConfig.authority,
      MAG_ENTRA_BACKEND_SCOPE: authConfig.backendScope,
      MAG_ENTRA_REDIRECT_URI: authConfig.redirectUri
    };
    Object.assign(process.env, environment);
    const clientFactory = jest.fn(() => msalClient());

    expect(loadAuthConfiguration()).toMatchObject({
      tenantId: authConfig.tenantId,
      clientId: authConfig.clientId,
      backendScope: authConfig.backendScope,
      redirectUri: authConfig.redirectUri
    });
    expect(getAuthenticationService(account.username, logger, clientFactory)).toBe(
      getAuthenticationService('second@example.com', logger, clientFactory)
    );
    expect(clientFactory).toHaveBeenCalledTimes(1);
  });

  it('moves to error when silent SSO returns an account from another tenant', async () => {
    const foreignAccount = { ...account, tenantId: '22222222-3333-4444-8555-666666666666' };
    const client = msalClient({ ssoSilent: jest.fn().mockResolvedValue({ account: foreignAccount }) });
    const service = new MsalAuthenticationService(client, authConfig, account.username, logger);

    await service.initialize();

    expect(service.state).toMatchObject({ status: 'error', reason: 'invalid-configuration' });
  });

  it('completes popup sign-in and makes the returned account active', async () => {
    const client = msalClient({ loginPopup: jest.fn().mockResolvedValue({ account }) });
    const service = new MsalAuthenticationService(client, authConfig, account.username, logger);

    await service.signIn();

    expect(client.loginPopup).toHaveBeenCalledWith(expect.objectContaining({ scopes: [backendScope], redirectUri }));
    expect(client.setActiveAccount).toHaveBeenCalledWith(account);
    expect(service.state.status).toBe('ready');
  });

  it.each([
    ['user_cancelled', 'popup-cancelled', false],
    ['popup_window_error', 'popup-blocked', false],
    ['popup_window_error', 'popup-blocked', true]
  ])('keeps popup error %s retryable and sanitized', async (errorCode, reason, synchronous) => {
    const loginPopup = synchronous
      ? jest.fn(() => { throw { errorCode }; })
      : jest.fn().mockRejectedValue({ errorCode });
    const client = msalClient({ loginPopup });
    const service = new MsalAuthenticationService(client, authConfig, account.username, logger);

    await expect(service.signIn()).rejects.toMatchObject({ reason });
    expect(service.state).toMatchObject({ status: 'interaction-required', reason });
  });

  it('uses only acquireTokenSilent with the configured delegated scope', async () => {
    const client = msalClient({
      getAllAccounts: jest.fn(() => [account]),
      getActiveAccount: jest.fn(() => account),
      acquireTokenSilent: jest.fn().mockResolvedValue({ accessToken: 'access-token' })
    });
    const service = new MsalAuthenticationService(client, authConfig, account.username, logger);

    await expect(service.getAccessToken()).resolves.toBe('access-token');
    expect(client.acquireTokenSilent).toHaveBeenCalledWith(expect.objectContaining({
      account,
      scopes: [backendScope],
      redirectUri
    }));
    expect(client.loginPopup).not.toHaveBeenCalled();
  });

  it.each([
    ['empty', 'empty-token'],
    ['entra', 'silent-acquisition-failed'],
    ['interaction', 'interaction-required']
  ])('classifies silent token failure %s without falling back to interaction', async (failure, reason) => {
    const acquireTokenSilent = failure === 'empty'
      ? jest.fn().mockResolvedValue({ accessToken: '   ' })
      : jest.fn().mockRejectedValue({
        errorCode: failure === 'interaction' ? 'consent_required' : 'temporarily_unavailable'
      });
    const client = msalClient({
      getAllAccounts: jest.fn(() => [account]),
      getActiveAccount: jest.fn(() => account),
      acquireTokenSilent
    });
    const service = new MsalAuthenticationService(client, authConfig, account.username, logger);

    await expect(service.getAccessToken()).rejects.toMatchObject({ reason });
    expect(client.loginPopup).not.toHaveBeenCalled();
  });

  it('sends cloud preflight only after adding the Bearer token', async () => {
    const authentication = authenticationWith(jest.fn().mockResolvedValue('access-token'));
    const backendAuth = new BackendAuthService(cloudUrl, authentication, logger);
    const service = new PipelineService(cloudUrl, backendAuth, logger);
    const fetchSpy = jest.fn().mockResolvedValue(duplicateResponse());
    window.fetch = fetchSpy;

    await service.uploadAndStart(new File(['pdf'], 'case.pdf', { type: 'application/pdf' }));

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const options = fetchSpy.mock.calls[0][1] as RequestInit;
    const headers = options.headers as Headers;
    expect(headers.get('Authorization')).toBe('Bearer access-token');
    expect(headers.has('x-functions-key')).toBe(false);
    expect(headers.get('x-mag-trace-id')).toBe('trace-1');
  });

  it.each([
    [401, 'backend-token-audience-or-issuer-rejected'],
    [403, 'backend-scope-or-client-application-rejected']
  ])('maps fetch status %s to a sanitized Entra authentication error', async (status, reason) => {
    const backendAuth = new BackendAuthService(
      cloudUrl,
      authenticationWith(jest.fn().mockResolvedValue('access-token')),
      logger
    );
    const responseText = jest.fn();
    window.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status,
      url: `${cloudUrl}/api/pipeline-preflight`,
      text: responseText,
      headers: {
        get: jest.fn((name: string) => name === 'x-ms-request-id' ? 'request-123' : null)
      }
    });

    await expect(backendAuth.fetch({
      operation: 'pipeline-preflight',
      url: `${cloudUrl}/api/pipeline-preflight`
    })).rejects.toMatchObject({
      code: ENTRA_AUTHENTICATION_FAILED,
      status,
      reason,
      correlationId: 'request-123'
    });

    expect(responseText).not.toHaveBeenCalled();
    expect(logger.error).toHaveBeenCalledWith('Backend authentication response rejected', {
      status,
      reason,
      correlationId: 'request-123',
      operation: 'pipeline-preflight',
      targetHost: 'function.example'
    });
  });

  it('maps an XHR 403 to the same fail-closed Entra error without exposing its body', async () => {
    const backendAuth = new BackendAuthService(
      cloudUrl,
      authenticationWith(jest.fn().mockResolvedValue('access-token')),
      logger
    );
    const service = new PipelineService(cloudUrl, backendAuth, logger);
    window.fetch = jest.fn().mockResolvedValue({
      ...duplicateResponse(),
      json: jest.fn().mockResolvedValue({ status: 'new', doc_id: 'case' }),
      status: 200
    });

    const originalXhr = window.XMLHttpRequest;
    const xhr: any = {
      upload: {},
      status: 403,
      responseText: 'sensitive platform response body',
      open: jest.fn(),
      setRequestHeader: jest.fn(),
      getResponseHeader: jest.fn((name: string) => name === 'x-correlation-id' ? 'xhr-request-456' : null),
      send: jest.fn(function (this: any) {
        this.onload();
      })
    };
    (window as any).XMLHttpRequest = jest.fn(() => xhr);

    try {
      const promise = service.uploadAndStart(new File(['pdf'], 'case.pdf', { type: 'application/pdf' }));
      await expect(promise).rejects.toMatchObject({
        code: ENTRA_AUTHENTICATION_FAILED,
        status: 403,
        reason: 'backend-scope-or-client-application-rejected',
        correlationId: 'xhr-request-456'
      });
      expect(JSON.stringify(logger.error.mock.calls)).not.toContain('sensitive platform response body');
    } finally {
      window.XMLHttpRequest = originalXhr;
    }
  });

  it.each([
    ['provider rejection', jest.fn().mockRejectedValue(new EntraAuthenticationError({ reason: 'token-acquisition-failed' }))],
    ['empty token', jest.fn().mockResolvedValue(undefined)]
  ])('blocks preflight with zero network calls on %s', async (_label, getAccessToken) => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    const backendAuth = new BackendAuthService(cloudUrl, authenticationWith(getAccessToken), logger);
    const service = new PipelineService(cloudUrl, backendAuth, logger);
    const fetchSpy = jest.fn();
    window.fetch = fetchSpy;

    await expect(
      service.uploadAndStart(new File(['pdf'], 'case.pdf', { type: 'application/pdf' }))
    ).rejects.toMatchObject({ code: ENTRA_AUTHENTICATION_FAILED });
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(consoleError).toHaveBeenCalledWith(
      '[MAG] Security authentication failure; backend request blocked',
      expect.objectContaining({ operation: 'pipeline-preflight', targetHost: 'function.example' })
    );
  });

  it('blocks service construction when the authentication boundary is missing', () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => undefined);

    expect(() => new PipelineService(cloudUrl, undefined as any, logger)).toThrow(EntraAuthenticationError);
    expect(consoleError).toHaveBeenCalledWith(
      '[MAG] Security authentication failure; service construction blocked',
      expect.objectContaining({ reason: 'missing-backend-auth-service', service: 'PipelineService' })
    );
  });

  it('does not let the preflight 404 fallback swallow authentication failures', async () => {
    jest.spyOn(console, 'error').mockImplementation(() => undefined);
    const authError = new EntraAuthenticationError({ reason: 'token-acquisition-failed' });
    const backendAuth = new BackendAuthService(
      cloudUrl,
      authenticationWith(jest.fn().mockRejectedValue(authError)),
      logger
    );
    const service = new PipelineService(cloudUrl, backendAuth, logger);
    const fetchSpy = jest.fn();
    window.fetch = fetchSpy;

    await expect(
      service.uploadAndStart(new File(['pdf'], 'case.pdf', { type: 'application/pdf' }))
    ).rejects.toBe(authError);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('fails closed before fetch in graph, report, and chat services', async () => {
    const authError = new EntraAuthenticationError({ reason: 'token-acquisition-failed' });
    const backendAuth = {
      mode: 'entra',
      fetch: jest.fn().mockRejectedValue(authError)
    } as unknown as BackendAuthService;
    const fetchSpy = jest.fn();
    window.fetch = fetchSpy;

    await expect(new GraphService(cloudUrl, backendAuth, logger).getGraph(
      { docId: 'auth-test-graph' },
      { forceRefresh: true }
    )).rejects.toBe(authError);
    await expect(new ReportService(cloudUrl, backendAuth).getReport(
      'auth-test-report',
      { forceRefresh: true }
    )).rejects.toBe(authError);
    await expect(new ChatService(cloudUrl, backendAuth, logger).ask(
      'auth-test-chat',
      'unique auth test question'
    )).rejects.toBe(authError);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it.each([
    'http://localhost:7071',
    'https://127.0.0.1:7071',
    'http://[::1]:7071'
  ])('allows tokenless local development for loopback URL %s', async (url) => {
    const getAccessToken = jest.fn().mockRejectedValue(new Error('must not be called'));
    const backendAuth = new BackendAuthService(url, authenticationWith(getAccessToken), logger);
    const fetchSpy = jest.fn().mockResolvedValue({ ok: true });
    window.fetch = fetchSpy;

    await expect(backendAuth.fetch(
      { operation: 'local-test', url: `${url}/api/ping` },
      { headers: { 'x-mag-trace-id': 'local-trace' } }
    )).resolves.toMatchObject({ ok: true });
    expect(getAccessToken).not.toHaveBeenCalled();
    expect(isLoopbackApiUrl(url)).toBe(true);
    const headers = fetchSpy.mock.calls[0][1].headers as Headers;
    expect(headers.get('Authorization')).toBeNull();
    expect(headers.has('x-functions-key')).toBe(false);
    expect(headers.get('x-mag-trace-id')).toBe('local-trace');
  });

  it('does not extend loopback compatibility to an external status URL', async () => {
    const getAccessToken = jest.fn().mockResolvedValue('external-token');
    const backendAuth = new BackendAuthService(
      'http://localhost:7071',
      authenticationWith(getAccessToken),
      logger
    );
    const fetchSpy = jest.fn().mockResolvedValue({ ok: true });
    window.fetch = fetchSpy;

    await expect(backendAuth.fetch({
      operation: 'external-status-test',
      url: 'https://function.example/api/status'
    })).resolves.toMatchObject({ ok: true });
    expect(getAccessToken).toHaveBeenCalledTimes(1);
    const headers = fetchSpy.mock.calls[0][1].headers as Headers;
    expect(headers.get('Authorization')).toBe('Bearer external-token');
  });

  it.each([
    'not-a-url',
    'https://localhost.example',
    'https://127.0.0.1.example',
    'https://localhost@function.example/api/ping',
    'https://function.azurewebsites.net',
    'file://localhost/tmp/api'
  ])('treats %s as a cloud-protected URL', (url) => {
    const authentication = authenticationWith(jest.fn());
    expect(new BackendAuthService(url, authentication, logger).mode).toBe('entra');
    expect(isLoopbackApiUrl(url)).toBe(false);
  });

  it('does not allow a caller-supplied Authorization header to bypass token acquisition', async () => {
    jest.spyOn(console, 'error').mockImplementation(() => undefined);
    const authError = new EntraAuthenticationError({ reason: 'token-acquisition-failed' });
    const backendAuth = new BackendAuthService(
      cloudUrl,
      authenticationWith(jest.fn().mockRejectedValue(authError)),
      logger
    );
    const fetchSpy = jest.fn();
    window.fetch = fetchSpy;

    await expect(backendAuth.fetch(
      { operation: 'caller-authorization-test', url: `${cloudUrl}/api/ping` },
      { headers: { authorization: 'Bearer caller-supplied-token' } }
    )).rejects.toBe(authError);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('does not construct or open an XHR when cloud token acquisition fails', async () => {
    jest.spyOn(console, 'error').mockImplementation(() => undefined);
    const authError = new EntraAuthenticationError({ reason: 'token-acquisition-failed' });
    const backendAuth = new BackendAuthService(
      cloudUrl,
      authenticationWith(jest.fn().mockRejectedValue(authError)),
      logger
    );
    const originalXhr = window.XMLHttpRequest;
    const xhrConstructor = jest.fn();
    (window as any).XMLHttpRequest = xhrConstructor;

    try {
      await expect(backendAuth.openAuthenticatedXhr(
        { operation: 'pipeline-start', url: `${cloudUrl}/api/pipelines` },
        'POST'
      )).rejects.toBe(authError);
      expect(xhrConstructor).not.toHaveBeenCalled();
    } finally {
      window.XMLHttpRequest = originalXhr;
    }
  });

  it('opens cloud XHR only after applying Entra and operational headers', async () => {
    const backendAuth = new BackendAuthService(
      cloudUrl,
      authenticationWith(jest.fn().mockResolvedValue('xhr-access-token')),
      logger
    );
    const originalXhr = window.XMLHttpRequest;
    const open = jest.fn();
    const setRequestHeader = jest.fn();
    const xhr = { open, setRequestHeader } as unknown as XMLHttpRequest;
    const xhrConstructor = jest.fn(() => xhr);
    (window as any).XMLHttpRequest = xhrConstructor;

    try {
      await expect(backendAuth.openAuthenticatedXhr(
        { operation: 'pipeline-start', url: `${cloudUrl}/api/pipelines` },
        'POST',
        { 'x-mag-trace-id': 'trace-xhr' }
      )).resolves.toBe(xhr);
      expect(xhrConstructor).toHaveBeenCalledTimes(1);
      expect(open).toHaveBeenCalledWith('POST', `${cloudUrl}/api/pipelines`);
      expect(setRequestHeader).toHaveBeenCalledWith('authorization', 'Bearer xhr-access-token');
      expect(setRequestHeader).not.toHaveBeenCalledWith('x-functions-key', expect.anything());
      expect(setRequestHeader).toHaveBeenCalledWith('x-mag-trace-id', 'trace-xhr');
    } finally {
      window.XMLHttpRequest = originalXhr;
    }
  });

  it('logs and displays only sanitized authentication diagnostics', async () => {
    const correlationId = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';
    const client = msalClient({
      getAllAccounts: jest.fn(() => [account]),
      getActiveAccount: jest.fn(() => account),
      acquireTokenSilent: jest.fn().mockRejectedValue(
        new Error(`AADSTS65001: secret-token-value correlation_id: ${correlationId}`)
      )
    });
    const authentication = new MsalAuthenticationService(client, authConfig, account.username, logger);
    const backendAuth = new BackendAuthService(cloudUrl, authentication, logger);
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => undefined);

    let caught: unknown;
    try {
      await backendAuth.fetch({ operation: 'security-test', url: `${cloudUrl}/api/ping` });
    } catch (error) {
      caught = error;
    }

    expect(isEntraAuthenticationError(caught)).toBe(true);
    expect(caught).toMatchObject({ errorCode: 'AADSTS65001', correlationId });
    expect(requestErrorText(caught, 'fallback')).toContain('שגיאת אבטחה ואימות');
    expect(JSON.stringify(consoleError.mock.calls)).not.toContain('secret-token-value');
    expect(JSON.stringify(logger.error.mock.calls)).not.toContain('secret-token-value');
  });
});
