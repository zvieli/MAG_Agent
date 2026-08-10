import { requestErrorText } from '../src/webparts/magApp/domain/localization';
import { BackendAuthService, isLoopbackApiUrl } from '../src/webparts/magApp/services/BackendAuthService';
import { ChatService } from '../src/webparts/magApp/services/ChatService';
import { GraphService } from '../src/webparts/magApp/services/GraphService';
import { PipelineService } from '../src/webparts/magApp/services/PipelineService';
import { ReportService } from '../src/webparts/magApp/services/ReportService';
import {
  DEFAULT_BACKEND_CLIENT_ID,
  ENTRA_AUTHENTICATION_FAILED,
  EntraAuthenticationError,
  TokenService,
  isEntraAuthenticationError,
  normalizeBackendClientId
} from '../src/webparts/magApp/services/TokenService';

const logger = {
  enabled: true,
  createTraceId: jest.fn(() => 'trace-1'),
  info: jest.fn(),
  group: jest.fn(),
  error: jest.fn()
};

const cloudUrl = 'https://function.example';

const tokenServiceWith = (getAccessToken: jest.Mock): TokenService => ({
  getAccessToken
} as unknown as TokenService);

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

  it('passes the bare backend client ID unchanged to getToken', async () => {
    const getToken = jest.fn().mockResolvedValue('access-token');
    const factory = {
      getTokenProvider: jest.fn().mockResolvedValue({ getToken })
    };
    const service = new TokenService(factory as any, DEFAULT_BACKEND_CLIENT_ID, logger);

    await expect(service.getAccessToken()).resolves.toBe('access-token');
    expect(getToken).toHaveBeenCalledTimes(1);
    expect(getToken).toHaveBeenCalledWith(DEFAULT_BACKEND_CLIENT_ID);
  });

  it.each([
    ['026e9069-414c-4df4-8513-bdeb9ff301d2', DEFAULT_BACKEND_CLIENT_ID],
    ['api://026e9069-414c-4df4-8513-bdeb9ff301d2', DEFAULT_BACKEND_CLIENT_ID],
    ['api://026e9069-414c-4df4-8513-bdeb9ff301d2/user_impersonation', DEFAULT_BACKEND_CLIENT_ID],
    ['api://026e9069-414c-4df4-8513-bdeb9ff301d2/.default', DEFAULT_BACKEND_CLIENT_ID],
    ['api://026e9069-414c-4df4-8513-bdeb9ff301d2/user_impersonation/.default', DEFAULT_BACKEND_CLIENT_ID]
  ])('normalizes legacy backend value %s', (configured, expected) => {
    expect(normalizeBackendClientId(configured)).toBe(expected);
  });

  it('rejects invalid configuration before asking SPFx for a provider', async () => {
    const factory = { getTokenProvider: jest.fn() };
    const service = new TokenService(factory as any, 'api://not-a-client-id/user_impersonation', logger);

    await expect(service.getAccessToken()).rejects.toMatchObject({
      code: ENTRA_AUTHENTICATION_FAILED,
      reason: 'invalid-backend-client-id'
    });
    expect(factory.getTokenProvider).not.toHaveBeenCalled();
  });

  it('sends cloud preflight only after adding the Bearer token', async () => {
    const tokenService = tokenServiceWith(jest.fn().mockResolvedValue('access-token'));
    const backendAuth = new BackendAuthService(cloudUrl, tokenService, logger);
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
    ['provider rejection', jest.fn().mockRejectedValue(new EntraAuthenticationError({ reason: 'token-acquisition-failed' }))],
    ['empty token', jest.fn().mockResolvedValue(undefined)]
  ])('blocks preflight with zero network calls on %s', async (_label, getAccessToken) => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    const backendAuth = new BackendAuthService(cloudUrl, tokenServiceWith(getAccessToken), logger);
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
      tokenServiceWith(jest.fn().mockRejectedValue(authError)),
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
    const backendAuth = new BackendAuthService(url, tokenServiceWith(getAccessToken), logger);
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
      tokenServiceWith(getAccessToken),
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
    const tokenService = tokenServiceWith(jest.fn());
    expect(new BackendAuthService(url, tokenService, logger).mode).toBe('entra');
    expect(isLoopbackApiUrl(url)).toBe(false);
  });

  it('does not allow a caller-supplied Authorization header to bypass token acquisition', async () => {
    jest.spyOn(console, 'error').mockImplementation(() => undefined);
    const authError = new EntraAuthenticationError({ reason: 'token-acquisition-failed' });
    const backendAuth = new BackendAuthService(
      cloudUrl,
      tokenServiceWith(jest.fn().mockRejectedValue(authError)),
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
      tokenServiceWith(jest.fn().mockRejectedValue(authError)),
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
      tokenServiceWith(jest.fn().mockResolvedValue('xhr-access-token')),
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
    const getToken = jest.fn().mockRejectedValue(
      new Error(`AADSTS65001: secret-token-value correlation_id: ${correlationId}`)
    );
    const factory = { getTokenProvider: jest.fn().mockResolvedValue({ getToken }) };
    const tokenService = new TokenService(factory as any, DEFAULT_BACKEND_CLIENT_ID, logger);
    const backendAuth = new BackendAuthService(cloudUrl, tokenService, logger);
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
