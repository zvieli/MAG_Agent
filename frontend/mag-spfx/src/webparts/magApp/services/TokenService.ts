import { AadTokenProviderFactory } from '@microsoft/sp-http';
import { DebugLogger } from '../domain/debugLogger';

const clientIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Convert persisted client IDs and scoped App ID URIs from older Web Part
 * versions to the canonical backend App ID URI expected by getToken().
 */
export const normalizeBackendResource = (value?: string): string | undefined => {
  if (!value) return undefined;

  let normalized = value.trim();
  if (!normalized) return undefined;

  normalized = normalized.replace(/(?:\/(?:user_impersonation|\.default))+\/?$/i, '');
  normalized = normalized.replace(/\/+$/, '');

  if (clientIdPattern.test(normalized)) {
    return `api://${normalized.toLowerCase()}`;
  }

  const appIdUriMatch = normalized.match(/^api:\/\/([0-9a-f-]+)$/i);
  if (!appIdUriMatch || !clientIdPattern.test(appIdUriMatch[1])) return undefined;

  return `api://${appIdUriMatch[1].toLowerCase()}`;
};

export const ENTRA_AUTHENTICATION_FAILED = 'ENTRA_AUTHENTICATION_FAILED';

export interface EntraErrorDetails {
  reason?: string;
  errorCode?: string;
  correlationId?: string;
  status?: number;
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
  public readonly code = ENTRA_AUTHENTICATION_FAILED;
  public readonly reason?: string;
  public readonly errorCode?: string;
  public readonly correlationId?: string;
  public readonly status?: number;

  public constructor(details: EntraErrorDetails = {}) {
    const diagnostic = [details.errorCode, details.correlationId && `correlation ${details.correlationId}`]
      .filter(Boolean)
      .join(', ');
    super(
      `לא ניתן לקבל הרשאת Entra ID עבור שירות המערכת. יש לרענן את הדף או לפנות למנהל המערכת${diagnostic ? ` (${diagnostic})` : ''}.`
    );
    this.name = 'EntraAuthenticationError';
    Object.setPrototypeOf(this, EntraAuthenticationError.prototype);
    this.reason = details.reason;
    this.errorCode = details.errorCode;
    this.correlationId = details.correlationId;
    this.status = details.status;
  }
}

export const isEntraAuthenticationError = (error: unknown): error is EntraAuthenticationError => {
  if (!error || typeof error !== 'object') return false;
  const record = error as Record<string, unknown>;
  return record.code === ENTRA_AUTHENTICATION_FAILED || record.name === 'EntraAuthenticationError';
};

export class TokenService {
  private readonly tokenProviderFactory?: AadTokenProviderFactory;
  private readonly configuredBackendResource?: string;
  private readonly backendResource?: string;
  private readonly logger?: DebugLogger;

  public constructor(
    tokenProviderFactory?: AadTokenProviderFactory,
    backendResource?: string,
    logger?: DebugLogger
  ) {
    this.tokenProviderFactory = tokenProviderFactory;
    this.configuredBackendResource = backendResource?.trim();
    this.backendResource = normalizeBackendResource(backendResource);
    this.logger = logger;
  }

  public async getAccessToken(): Promise<string> {
    if (!this.tokenProviderFactory) {
      this.logger?.error('Entra ID authentication is unavailable', {
        reason: 'missing-token-provider-factory',
        operation: 'token-acquisition'
      });
      throw new EntraAuthenticationError({ reason: 'missing-token-provider-factory' });
    }

    if (!this.backendResource) {
      const reason = this.configuredBackendResource ? 'invalid-backend-resource' : 'missing-backend-resource';
      this.logger?.error('Entra ID backend resource is invalid', {
        reason,
        operation: 'token-acquisition'
      });
      throw new EntraAuthenticationError({ reason });
    }

    try {
      const provider = await this.tokenProviderFactory.getTokenProvider();
      const token = await provider.getToken(this.backendResource);
      if (!token || !token.trim()) {
        throw new Error('empty-token');
      }
      return token;
    } catch (error) {
      const details = {
        reason: error instanceof Error && error.message === 'empty-token' ? 'empty-token' : 'token-acquisition-failed',
        ...extractEntraErrorDetails(error)
      };
      this.logger?.error('Failed to acquire Entra ID access token', {
        reason: details.reason,
        correlationId: details.correlationId,
        operation: 'token-acquisition'
      });
      throw new EntraAuthenticationError(details);
    }
  }
}
