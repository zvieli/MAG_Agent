import { normalizeApiBaseUrl } from '../domain/guards';
import { DebugLogger } from '../domain/debugLogger';
import { EntraAuthenticationError } from './TokenService';

export class HttpError extends Error {
  public readonly status: number;

  public constructor(message: string, status: number) {
    super(message);
    this.name = 'HttpError';
    this.status = status;
  }
}

export const apiUrl = (apiBaseUrl: string, path: string): string => {
  const base = normalizeApiBaseUrl(apiBaseUrl);
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${base}${normalizedPath}`;
};

export interface ReadJsonContext {
  logger?: DebugLogger;
  traceId?: string;
  method?: string;
  url?: string;
  operation?: string;
  correlationId?: string;
}

const isHtmlResponse = (body: string, contentType: string | null): boolean =>
  contentType?.includes('text/html') === true || /^\s*<!doctype html/i.test(body) || /^\s*<html/i.test(body);

const targetHostname = (url?: string): string => {
  if (!url) return 'unavailable';
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return 'invalid-url';
  }
};

const safeCorrelationId = (value?: string | null): string | undefined => {
  if (!value) return undefined;
  const normalized = value.trim();
  return /^[a-z0-9._:-]{1,128}$/i.test(normalized) ? normalized : undefined;
};

export const throwIfBackendAuthenticationRejected = (
  status: number,
  context: ReadJsonContext = {}
): void => {
  if (status !== 401 && status !== 403) return;

  const reason = status === 401
    ? 'backend-token-audience-or-issuer-rejected'
    : 'backend-scope-or-client-application-rejected';
  const correlationId = safeCorrelationId(context.correlationId || context.traceId);
  const details = {
    status,
    reason,
    correlationId,
    operation: context.operation || 'backend-request',
    targetHost: targetHostname(context.url)
  };

  context.logger?.error('Backend authentication response rejected', details);
  throw new EntraAuthenticationError({ status, reason, correlationId });
};

const responseErrorMessage = async (response: Response): Promise<string> => {
  const body = await response.text();

  if (isHtmlResponse(body, response.headers.get('content-type'))) {
    return `שירות הענן החזיר שגיאת HTTP ${response.status}.`;
  }

  return body || response.statusText || `שגיאת HTTP ${response.status}`;
};

export const readJson = async <T>(response: Response, context?: ReadJsonContext): Promise<T> => {
  if (!response.ok) {
    throwIfBackendAuthenticationRejected(response.status, {
      ...context,
      url: context?.url || response.url,
      correlationId:
        context?.correlationId ||
        response.headers.get('x-ms-request-id') ||
        response.headers.get('request-id') ||
        response.headers.get('x-correlation-id') ||
        undefined
    });

    const message = await responseErrorMessage(response);
    context?.logger?.error('HTTP request failed', {
      status: response.status,
      reason: 'backend-http-error',
      correlationId: safeCorrelationId(context.traceId),
      operation: context.operation,
      targetHost: targetHostname(context.url || response.url)
    });
    throw new HttpError(message, response.status);
  }

  return response.json() as Promise<T>;
};
