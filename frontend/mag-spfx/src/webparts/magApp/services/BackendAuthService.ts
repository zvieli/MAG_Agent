import { DebugLogger } from '../domain/debugLogger';
import {
  EntraAuthenticationError,
  TokenService,
  isEntraAuthenticationError
} from './TokenService';

export interface BackendRequestContext {
  operation: string;
  url: string;
}

export type BackendAuthMode = 'entra' | 'loopback';

const mergeHeaders = (target: Record<string, string>, source?: HeadersInit): void => {
  if (source instanceof Headers) {
    source.forEach((value, key) => {
      target[key] = value;
    });
    return;
  }

  if (Array.isArray(source)) {
    for (const [key, value] of source) {
      target[key] = value;
    }
    return;
  }

  if (source) {
    Object.assign(target, source);
  }
};

const targetHostname = (url: string): string | undefined => {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return undefined;
  }
};

export const isLoopbackApiUrl = (apiBaseUrl: string): boolean => {
  const hostname = targetHostname(apiBaseUrl);
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]' || hostname === '::1';
};

export class BackendAuthService {
  public readonly mode: BackendAuthMode;
  private readonly functionKey: string;
  private readonly tokenService: TokenService;
  private readonly logger?: DebugLogger;

  public constructor(
    apiBaseUrl: string,
    functionKey: string,
    tokenService: TokenService,
    logger?: DebugLogger
  ) {
    this.mode = isLoopbackApiUrl(apiBaseUrl) ? 'loopback' : 'entra';
    this.functionKey = functionKey;
    this.tokenService = tokenService;
    this.logger = logger;
  }

  private authenticationFailure(
    error: unknown,
    context: BackendRequestContext,
    fallbackReason: string
  ): EntraAuthenticationError {
    const authenticationError = isEntraAuthenticationError(error)
      ? error
      : new EntraAuthenticationError({ reason: fallbackReason });
    const details = {
      reason: authenticationError.reason || fallbackReason,
      errorCode: authenticationError.errorCode,
      correlationId: authenticationError.correlationId,
      operation: context.operation,
      targetHost: targetHostname(context.url) || 'invalid-url'
    };

    this.logger?.error('Backend request blocked by Entra authentication', details);
    console.error('[MAG] Security authentication failure; backend request blocked', details);
    return authenticationError;
  }

  public async getHeaders(
    context: BackendRequestContext,
    extraHeaders?: HeadersInit
  ): Promise<Record<string, string>> {
    const headers: Record<string, string> = {};
    mergeHeaders(headers, extraHeaders);

    const normalizedFunctionKey = this.functionKey?.trim();
    if (normalizedFunctionKey) {
      headers['x-functions-key'] = normalizedFunctionKey;
    }

    const requestMode: BackendAuthMode =
      this.mode === 'loopback' && isLoopbackApiUrl(context.url) ? 'loopback' : 'entra';
    if (requestMode === 'loopback') {
      return headers;
    }

    if (!this.tokenService) {
      throw this.authenticationFailure(undefined, context, 'missing-token-service');
    }

    try {
      const token = await this.tokenService.getAccessToken();
      if (!token || !token.trim()) {
        throw new EntraAuthenticationError({ reason: 'empty-token' });
      }
      headers.Authorization = `Bearer ${token.trim()}`;
      return headers;
    } catch (error) {
      throw this.authenticationFailure(error, context, 'token-acquisition-failed');
    }
  }
}

export const requireBackendAuthService = (
  backendAuth: BackendAuthService | undefined,
  serviceName: string
): BackendAuthService => {
  if (backendAuth) return backendAuth;

  const error = new EntraAuthenticationError({ reason: 'missing-backend-auth-service' });
  console.error('[MAG] Security authentication failure; service construction blocked', {
    reason: error.reason,
    service: serviceName
  });
  throw error;
};
