export interface DebugLogger {
  readonly enabled: boolean;
  createTraceId(prefix: string): string;
  info(label: string, data?: unknown): void;
  group(label: string, data?: unknown): void;
  error(label: string, data?: unknown): void;
}

const sensitiveKeys = ['apikey', 'code', 'functionkey', 'key', 'sig', 'signature', 'token', 'x-functions-key'];

const redactUrl = (value: string): string => {
  try {
    const url = new URL(value, window.location.origin);
    for (const key of sensitiveKeys) {
      if (url.searchParams.has(key)) {
        url.searchParams.set(key, '[redacted]');
      }
    }

    return url.toString();
  } catch {
    return value;
  }
};

const sanitize = (value: unknown): unknown => {
  if (typeof value === 'string') {
    return value.startsWith('http://') || value.startsWith('https://') ? redactUrl(value) : value;
  }

  if (Array.isArray(value)) {
    return value.map(sanitize);
  }

  if (value && typeof value === 'object') {
    const sanitized: Record<string, unknown> = {};
    const record = value as Record<string, unknown>;
    for (const key of Object.keys(record)) {
      const entry = record[key];
      sanitized[key] = sensitiveKeys.indexOf(key.toLowerCase()) >= 0 ? '[redacted]' : sanitize(entry);
    }
    return sanitized;
  }

  return value;
};

class BrowserDebugLogger implements DebugLogger {
  public constructor(public readonly enabled: boolean) {}

  public createTraceId(prefix: string): string {
    return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  }

  public info(label: string, data?: unknown): void {
    if (!this.enabled) return;
    console.info(`[MAG] ${label}`, sanitize(data));
  }

  public group(label: string, data?: unknown): void {
    if (!this.enabled) return;
    console.groupCollapsed(`[MAG] ${label}`);
    if (data !== undefined) {
      console.info(sanitize(data));
    }
    console.groupEnd();
  }

  public error(label: string, data?: unknown): void {
    if (!this.enabled) return;
    console.error(`[MAG] ${label}`, sanitize(data));
  }
}

export const createDebugLogger = (enabled: boolean): DebugLogger => new BrowserDebugLogger(enabled);
