import { AadTokenProviderFactory } from '@microsoft/sp-http';
import { DebugLogger } from '../domain/debugLogger';

export const DEFAULT_BACKEND_CLIENT_ID = '026e9069-414c-4df4-8513-bdeb9ff301d2';

const clientIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Convert persisted App ID URI/scope values from older Web Part versions to the
 * bare Entra application client ID expected by AadTokenProvider.getToken().
 */
export const normalizeBackendClientId = (value?: string): string | undefined => {
  if (!value) return undefined;

  let normalized = value.trim();
  if (!normalized) return undefined;

  normalized = normalized.replace(/^api:\/\//i, '');
  normalized = normalized.replace(/(?:\/(?:user_impersonation|\.default))+\/?$/i, '');
  normalized = normalized.replace(/\/$/, '');

  return clientIdPattern.test(normalized) ? normalized : undefined;
};

interface EntraErrorDetails {
  errorCode?: string;
  correlationId?: string;
}

const extractEntraErrorDetails = (error: unknown): EntraErrorDetails => {
  const record = error && typeof error === 'object' ? error as Record<string, unknown> : undefined;
  const values = [
    typeof error === 'string' ? error : undefined,
    record?.errorCode,
    record?.code,
    record?.correlationId,
    record?.correlation_id,
    record?.message
  ].filter((value): value is string => typeof value === 'string');
  const combined = values.join(' ');
  const errorCode = combined.match(/\bAADSTS\d+\b/i)?.[0]?.toUpperCase();
  const correlationId =
    (typeof record?.correlationId === 'string' ? record.correlationId : undefined) ||
    (typeof record?.correlation_id === 'string' ? record.correlation_id : undefined) ||
    combined.match(/correlation(?:_|\s|-)?id\s*[:=]\s*([0-9a-f-]{36})/i)?.[1];

  return {
    errorCode,
    correlationId: correlationId && /^[0-9a-f-]{36}$/i.test(correlationId) ? correlationId : undefined
  };
};

export class EntraAuthenticationError extends Error {
  public readonly errorCode?: string;
  public readonly correlationId?: string;

  public constructor(details: EntraErrorDetails = {}) {
    const diagnostic = [details.errorCode, details.correlationId && `correlation ${details.correlationId}`]
      .filter(Boolean)
      .join(', ');
    super(
      `לא ניתן לקבל הרשאת Entra ID עבור שירות המערכת. יש לרענן את הדף או לפנות למנהל המערכת${diagnostic ? ` (${diagnostic})` : ''}.`
    );
    this.name = 'EntraAuthenticationError';
    this.errorCode = details.errorCode;
    this.correlationId = details.correlationId;
  }
}

export class TokenService {
  private readonly tokenProviderFactory?: AadTokenProviderFactory;
  private readonly configuredBackendClientId?: string;
  private readonly backendClientId?: string;
  private readonly logger?: DebugLogger;

  public constructor(
    tokenProviderFactory?: AadTokenProviderFactory,
    backendClientId: string = DEFAULT_BACKEND_CLIENT_ID,
    logger?: DebugLogger
  ) {
    this.tokenProviderFactory = tokenProviderFactory;
    this.configuredBackendClientId = backendClientId?.trim();
    this.backendClientId = normalizeBackendClientId(backendClientId);
    this.logger = logger;
  }

  public async getAccessToken(): Promise<string> {
    if (!this.tokenProviderFactory) {
      this.logger?.error('Entra ID authentication is unavailable', { reason: 'missing-token-provider-factory' });
      throw new EntraAuthenticationError();
    }

    if (!this.backendClientId) {
      this.logger?.error('Entra ID backend client ID is invalid', {
        reason: this.configuredBackendClientId ? 'invalid-backend-client-id' : 'missing-backend-client-id'
      });
      throw new EntraAuthenticationError();
    }

    try {
      const provider = await this.tokenProviderFactory.getTokenProvider();
      const token = await provider.getToken(this.backendClientId);
      if (!token || !token.trim()) {
        throw new Error('empty-token');
      }
      return token;
    } catch (error) {
      const details = extractEntraErrorDetails(error);
      this.logger?.error('Failed to acquire Entra ID access token', {
        backendClientId: this.backendClientId,
        errorCode: details.errorCode,
        correlationId: details.correlationId
      });
      throw new EntraAuthenticationError(details);
    }
  }
}
