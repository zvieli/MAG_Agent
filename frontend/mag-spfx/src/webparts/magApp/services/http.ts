import { normalizeApiBaseUrl } from '../domain/guards';
import { DebugLogger } from '../domain/debugLogger';

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

export const authHeaders = (
  functionKey?: string,
  bearerTokenOrExtraHeaders?: string | HeadersInit,
  extraHeaders?: HeadersInit
): HeadersInit => {
  const headers: Record<string, string> = {};

  if (functionKey) {
    const normalizedKey = functionKey.trim();
    if (normalizedKey) {
      headers['x-functions-key'] = normalizedKey;
    }
  }

  let bearerToken: string | undefined;
  let actualExtraHeaders: HeadersInit | undefined;

  if (typeof bearerTokenOrExtraHeaders === 'string') {
    bearerToken = bearerTokenOrExtraHeaders;
    actualExtraHeaders = extraHeaders;
  } else if (bearerTokenOrExtraHeaders) {
    actualExtraHeaders = bearerTokenOrExtraHeaders;
  }

  if (bearerToken && bearerToken.trim()) {
    headers['Authorization'] = `Bearer ${bearerToken.trim()}`;
  }

  if (actualExtraHeaders instanceof Headers) {
    actualExtraHeaders.forEach((value, key) => {
      headers[key] = value;
    });
  } else if (Array.isArray(actualExtraHeaders)) {
    for (const [key, value] of actualExtraHeaders) {
      headers[key] = value;
    }
  } else if (actualExtraHeaders) {
    Object.assign(headers, actualExtraHeaders);
  }

  return headers;
};

export interface ReadJsonContext {
  logger?: DebugLogger;
  traceId?: string;
  method?: string;
  url?: string;
  operation?: string;
}

const isHtmlResponse = (body: string, contentType: string | null): boolean =>
  contentType?.includes('text/html') === true || /^\s*<!doctype html/i.test(body) || /^\s*<html/i.test(body);

const responseErrorMessage = async (response: Response): Promise<string> => {
  const body = await response.text();

  if (response.status === 403 && (isHtmlResponse(body, response.headers.get('content-type')) || body.includes('blocked your access'))) {
    return 'הגישה לשירות הענן נחסמה (403). יש לוודא שכתובת ה-IP המקומית מאושרת בהגדרות הגישה של שירות הפונקציות.';
  }

  if (response.status === 401) {
    return 'הבקשה נדחתה בגלל הרשאה חסרה או מפתח פונקציה לא תקין (401).';
  }

  if (isHtmlResponse(body, response.headers.get('content-type'))) {
    return `שירות הענן החזיר שגיאת HTTP ${response.status}.`;
  }

  return body || response.statusText || `שגיאת HTTP ${response.status}`;
};

export const readJson = async <T>(response: Response, context?: ReadJsonContext): Promise<T> => {
  if (!response.ok) {
    const message = await responseErrorMessage(response);
    context?.logger?.error('HTTP request failed', {
      traceId: context.traceId,
      operation: context.operation,
      method: context.method,
      url: context.url || response.url,
      status: response.status,
      message
    });
    throw new HttpError(message, response.status);
  }

  return response.json() as Promise<T>;
};
