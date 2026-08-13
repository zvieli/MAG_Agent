import { DebugLogger } from '../domain/debugLogger';
import {
  EntraAuthenticationError,
  IAuthenticationService,
  isEntraAuthenticationError
} from '../auth/AuthenticationService';
import { throwIfBackendAuthenticationRejected } from './http';

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
  let parsed: URL;
  try {
    parsed = new URL(apiBaseUrl);
  } catch {
    return false;
  }

  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false;
  const hostname = parsed.hostname.toLowerCase();
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]' || hostname === '::1';
};

export class BackendAuthService {
  public readonly mode: BackendAuthMode;
  private readonly authenticationService: IAuthenticationService;
  private readonly logger?: DebugLogger;

  public constructor(
    apiBaseUrl: string,
    authenticationService: IAuthenticationService,
    logger?: DebugLogger
  ) {
    this.mode = isLoopbackApiUrl(apiBaseUrl) ? 'loopback' : 'entra';
    this.authenticationService = authenticationService;
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
      correlationId: authenticationError.correlationId,
      operation: context.operation,
      targetHost: targetHostname(context.url) || 'invalid-url'
    };

    this.logger?.error('Backend request blocked by Entra authentication', details);
    console.error('[MAG] Security authentication failure; backend request blocked', details);
    return authenticationError;
  }

  private requestMode(url: string): BackendAuthMode {
    return isLoopbackApiUrl(url) ? 'loopback' : 'entra';
  }

  private assertRequestIsAuthorized(
    context: BackendRequestContext,
    mode: BackendAuthMode,
    headers: Headers
  ): void {
    if (mode === 'loopback') return;

    const authorization = headers.get('Authorization')?.trim();
    if (!authorization || !/^Bearer\s+\S+$/i.test(authorization)) {
      throw this.authenticationFailure(undefined, context, 'missing-bearer-before-send');
    }
  }

  private async buildHeaders(
    context: BackendRequestContext,
    extraHeaders?: HeadersInit
  ): Promise<{ headers: Headers; mode: BackendAuthMode }> {
    const headers = new Headers();
    const callerHeaders: Record<string, string> = {};
    mergeHeaders(callerHeaders, extraHeaders);
    Object.keys(callerHeaders).forEach((key) => headers.set(key, callerHeaders[key]));
    headers.delete('Authorization');

    const mode = this.requestMode(context.url);

    if (mode === 'entra') {
      if (!this.authenticationService) {
        throw this.authenticationFailure(undefined, context, 'missing-authentication-service');
      }

      try {
        const token = await this.authenticationService.getAccessToken();
        if (!token || !token.trim()) {
          throw new EntraAuthenticationError({ reason: 'empty-token' });
        }
        headers.set('Authorization', `Bearer ${token.trim()}`);
      } catch (error) {
        throw this.authenticationFailure(error, context, 'token-acquisition-failed');
      }
    }

    this.assertRequestIsAuthorized(context, mode, headers);
    return { headers, mode };
  }

  private logAuthorizedRequest(context: BackendRequestContext): void {
    this.logger?.info('Backend request authorized for transport', {
      operation: context.operation,
      targetHost: targetHostname(context.url) || 'invalid-url'
    });
  }

  public async fetch(context: BackendRequestContext, init: RequestInit = {}): Promise<Response> {
    const { headers, mode } = await this.buildHeaders(context, init.headers);
    this.assertRequestIsAuthorized(context, mode, headers);
    this.logAuthorizedRequest(context);
    const response = await window.fetch(context.url, { ...init, headers });
    throwIfBackendAuthenticationRejected(response.status, {
      logger: this.logger,
      operation: context.operation,
      url: context.url,
      correlationId:
        response.headers?.get('x-ms-request-id') ||
        response.headers?.get('request-id') ||
        response.headers?.get('x-correlation-id') ||
        undefined
    });
    return response;
  }

  public async openAuthenticatedXhr(
    context: BackendRequestContext,
    method: string,
    extraHeaders?: HeadersInit
  ): Promise<XMLHttpRequest> {
    const { headers, mode } = await this.buildHeaders(context, extraHeaders);
    this.assertRequestIsAuthorized(context, mode, headers);

    const xhr = new XMLHttpRequest();
    xhr.open(method, context.url);
    headers.forEach((value, key) => xhr.setRequestHeader(key, value));
    this.logAuthorizedRequest(context);
    return xhr;
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
