import { ChatAnswer, ChatOperation, DurableStartResponse, DurableStatus } from '../domain/types';
import { DebugLogger } from '../domain/debugLogger';
import { makeId, normalizeRuntimeStatus } from '../domain/guards';
import { browserCache } from '../domain/browserCache';
import { IChatService } from './IAppServices';
import { apiUrl, authHeaders, readJson } from './http';

export class ChatService implements IChatService {
  private readonly apiBaseUrl: string;
  private readonly functionKey: string;
  private readonly logger: DebugLogger;
  private readonly pendingCacheKeys: Record<string, string> = {};

  public constructor(apiBaseUrl: string, functionKey: string, logger: DebugLogger) {
    this.apiBaseUrl = apiBaseUrl;
    this.functionKey = functionKey;
    this.logger = logger;
  }

  public async ask(docId: string, question: string, state?: any): Promise<ChatOperation> {
    const traceId = this.logger.createTraceId('query');
    const startedAtMs = Date.now();
    const url = apiUrl(this.apiBaseUrl, '/api/query');
    const normalizedQuestion = question.trim().replace(/\s+/g, ' ').toLowerCase();
    const cacheKey = `chat:${docId}:${normalizedQuestion}`;
    const cached = await browserCache.get<ChatAnswer>(cacheKey);

    if (cached) {
      return {
        instanceId: makeId('cached-query'),
        statusUrl: `cache://${encodeURIComponent(cacheKey)}`,
        traceId,
        startedAtMs
      };
    }

    this.logger.group('Query start request', {
      traceId,
      method: 'POST',
      url,
      docId,
      questionLength: question.length,
      questionPreview: question.slice(0, 120)
    });

    const start = await fetch(url, {
      method: 'POST',
      headers: authHeaders(this.functionKey, {
        'Content-Type': 'application/json',
        'x-mag-trace-id': traceId
      }),
      body: JSON.stringify({ doc_id: docId, question, state })
    }).then((response) =>
      readJson<DurableStartResponse>(response, {
        logger: this.logger,
        traceId,
        operation: 'query-start',
        method: 'POST',
        url
      })
    );

    if (!start.id) {
      throw new Error('תגובת פתיחת השאילתה לא כללה פרטי מעקב.');
    }

    const statusUrl = apiUrl(this.apiBaseUrl, `/api/pipelines/${encodeURIComponent(start.id)}`);

    this.logger.group('Query start response', {
      traceId,
      instanceId: start.id,
      statusUrl,
      elapsedMs: Date.now() - startedAtMs
    });

    this.pendingCacheKeys[statusUrl] = cacheKey;

    return {
      instanceId: start.id,
      statusUrl,
      traceId,
      startedAtMs
    };
  }

  public async getStatus(statusUrl: string, traceId?: string, startedAtMs?: number): Promise<DurableStatus<ChatAnswer>> {
    if (statusUrl.indexOf('cache://') === 0) {
      const cacheKey = decodeURIComponent(statusUrl.replace('cache://', ''));
      const cached = await browserCache.get<ChatAnswer>(cacheKey);
      return {
        instanceId: makeId('cached-query-status'),
        runtimeStatus: 'Completed',
        output: cached?.value || { answer: 'לא נמצאה תשובה במטמון.' },
        customStatus: null
      };
    }

    const pollTraceId = traceId || this.logger.createTraceId('query-status');
    const pollStartedAtMs = Date.now();
    this.logger.info('Query status poll request', {
      traceId: pollTraceId,
      statusUrl
    });

    const status = await fetch(statusUrl, {
      headers: authHeaders(this.functionKey, {
        'x-mag-trace-id': pollTraceId
      })
    }).then((response) =>
      readJson<DurableStatus<ChatAnswer>>(response, {
        logger: this.logger,
        traceId: pollTraceId,
        operation: 'query-status',
        method: 'GET',
        url: statusUrl
      })
    );
    const runtimeStatus = normalizeRuntimeStatus(status.runtimeStatus);

    this.logger.group('Query status poll response', {
      traceId: pollTraceId,
      instanceId: status.instanceId,
      rawRuntimeStatus: status.runtimeStatus,
      runtimeStatus,
      answerLength: status.output?.answer?.length,
      hasOutput: Boolean(status.output),
      elapsedMs: Date.now() - pollStartedAtMs,
      totalElapsedMs: startedAtMs ? Date.now() - startedAtMs : undefined
    });

    const result = {
      ...status,
      runtimeStatus
    };

    if (runtimeStatus === 'Completed' && status.output?.answer) {
      const cacheKey = this.pendingCacheKeys[statusUrl];
      if (cacheKey) {
        await browserCache.set(cacheKey, status.output, 24 * 60 * 60 * 1000);
        delete this.pendingCacheKeys[statusUrl];
      }
    }

    return result;
  }
}
