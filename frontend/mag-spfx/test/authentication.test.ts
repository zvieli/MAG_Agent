import { PipelineService } from '../src/webparts/magApp/services/PipelineService';
import {
  DEFAULT_BACKEND_CLIENT_ID,
  EntraAuthenticationError,
  TokenService,
  normalizeBackendClientId
} from '../src/webparts/magApp/services/TokenService';

const logger = {
  enabled: true,
  createTraceId: jest.fn(() => 'trace-1'),
  info: jest.fn(),
  group: jest.fn(),
  error: jest.fn()
};

describe('SPFx Entra authentication', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('passes the bare backend client ID unchanged to getToken', async () => {
    const getToken = jest.fn().mockResolvedValue('access-token');
    const factory = {
      getTokenProvider: jest.fn().mockResolvedValue({ getToken })
    };
    const service = new TokenService(factory as any, DEFAULT_BACKEND_CLIENT_ID, logger);

    await expect(service.getAccessToken()).resolves.toBe('access-token');
    expect(getToken).toHaveBeenCalledTimes(1);
    expect(getToken).toHaveBeenCalledWith('026e9069-414c-4df4-8513-bdeb9ff301d2');
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

    await expect(service.getAccessToken()).rejects.toBeInstanceOf(EntraAuthenticationError);
    expect(factory.getTokenProvider).not.toHaveBeenCalled();
  });

  it('makes no fallback request and logs only sanitized Entra diagnostics', async () => {
    const correlationId = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';
    const getToken = jest.fn().mockRejectedValue(
      new Error(`AADSTS65001: secret-token-value correlation_id: ${correlationId}`)
    );
    const factory = {
      getTokenProvider: jest.fn().mockResolvedValue({ getToken })
    };
    const service = new TokenService(factory as any, DEFAULT_BACKEND_CLIENT_ID, logger);

    await expect(service.getAccessToken()).rejects.toMatchObject({
      errorCode: 'AADSTS65001',
      correlationId
    });
    expect(getToken).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(logger.error.mock.calls)).not.toContain('secret-token-value');
  });

  it('does not send a backend request when token acquisition fails', async () => {
    const tokenService = {
      getAccessToken: jest.fn().mockRejectedValue(new EntraAuthenticationError())
    } as unknown as TokenService;
    const service = new PipelineService('https://function.example', '', tokenService, logger);
    const fetchSpy = jest.fn();
    const originalFetch = window.fetch;
    window.fetch = fetchSpy;
    const file = new File(['pdf'], 'case.pdf', { type: 'application/pdf' });

    await expect(service.uploadAndStart(file)).rejects.toBeInstanceOf(EntraAuthenticationError);
    expect(fetchSpy).not.toHaveBeenCalled();
    window.fetch = originalFetch;
  });
});
