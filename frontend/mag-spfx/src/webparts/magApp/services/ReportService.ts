import { CommunitySummariesResult, ProtocolCompletenessResult, ReportResult } from '../domain/types';
import { browserCache } from '../domain/browserCache';
import { IReportService } from './IAppServices';
import { apiUrl, authHeaders, readJson } from './http';
import { TokenService } from './TokenService';

const reportTtlMs = 30 * 60 * 1000;

export class ReportService implements IReportService {
  private readonly apiBaseUrl: string;
  private readonly functionKey: string;
  private readonly tokenService?: TokenService;

  public constructor(apiBaseUrl: string, functionKey: string, tokenService?: TokenService) {
    this.apiBaseUrl = apiBaseUrl;
    this.functionKey = functionKey;
    this.tokenService = tokenService;
  }

  public async getReport(docId: string, options?: { forceRefresh?: boolean }): Promise<ReportResult> {
    const cacheKey = `report:${docId}`;
    if (!options?.forceRefresh) {
      const cached = await browserCache.get<ReportResult>(cacheKey);
      if (cached) {
        return {
          ...cached.value,
          cache: { fromCache: true, cachedAt: new Date(cached.createdAt).toISOString() }
        };
      }
    }

    try {
      const token = await this.tokenService?.getAccessToken();
      const result = await fetch(apiUrl(this.apiBaseUrl, `/api/cases/${encodeURIComponent(docId)}/report`), {
        headers: authHeaders(this.functionKey, token)
      }).then(readJson<ReportResult>);
      await browserCache.set(cacheKey, result, reportTtlMs);
      return { ...result, cache: { fromCache: false } };
    } catch (err) {
      const fallback: ReportResult = {
        docId,
        markdown: '',
        source: 'blob',
        updatedAt: new Date().toISOString(),
        error: err instanceof Error ? err.message : 'GRAPH_REPORT.md not found'
      };
      return { ...fallback, cache: { fromCache: false } };
    }
  }

  public async getCommunitySummaries(docId: string, options?: { forceRefresh?: boolean }): Promise<CommunitySummariesResult> {
    const cacheKey = `community_summaries:${docId}`;
    if (!options?.forceRefresh) {
      const cached = await browserCache.get<CommunitySummariesResult>(cacheKey);
      if (cached) {
        return {
          ...cached.value,
          cache: { fromCache: true, cachedAt: new Date(cached.createdAt).toISOString() }
        };
      }
    }

    try {
      const token = await this.tokenService?.getAccessToken();
      const result = await fetch(apiUrl(this.apiBaseUrl, `/api/cases/${encodeURIComponent(docId)}/communities`), {
        headers: authHeaders(this.functionKey, token)
      }).then(readJson<CommunitySummariesResult>);
      await browserCache.set(cacheKey, result, reportTtlMs);
      return { ...result, cache: { fromCache: false } };
    } catch (err) {
      const fallback: CommunitySummariesResult = {
        docId,
        summaries: {},
        hierarchy: {},
        source: 'blob',
        updatedAt: new Date().toISOString(),
        error: err instanceof Error ? err.message : 'community_summaries.json not found'
      };
      return { ...fallback, cache: { fromCache: false } };
    }
  }

  public async getProtocolCompleteness(docId: string, options?: { forceRefresh?: boolean }): Promise<ProtocolCompletenessResult> {
    const cacheKey = `protocol_completeness:${docId}`;
    if (!options?.forceRefresh) {
      const cached = await browserCache.get<ProtocolCompletenessResult>(cacheKey);
      if (cached) {
        return {
          ...cached.value,
          cache: { fromCache: true, cachedAt: new Date(cached.createdAt).toISOString() }
        };
      }
    }

    try {
      const token = await this.tokenService?.getAccessToken();
      const result = await fetch(apiUrl(this.apiBaseUrl, `/api/cases/${encodeURIComponent(docId)}/completeness`), {
        headers: authHeaders(this.functionKey, token)
      }).then(readJson<ProtocolCompletenessResult>);
      await browserCache.set(cacheKey, result, reportTtlMs);
      return { ...result, cache: { fromCache: false } };
    } catch (err) {
      const fallback: ProtocolCompletenessResult = {
        docId,
        source: 'blob',
        updatedAt: new Date().toISOString(),
        summary: { overallScore: 0, itemsAnalyzed: 0, missingItemsCount: 0, overallStatus: 'COMPLETE' },
        items: [],
        error: err instanceof Error ? err.message : 'protocol_completeness.json not found'
      };
      return { ...fallback, cache: { fromCache: false } };
    }
  }

  public async verifyProtocolCompletenessWithAgent(docId: string): Promise<string> {
    const token = await this.tokenService?.getAccessToken();
    const response = await fetch(apiUrl(this.apiBaseUrl, `/api/cases/${encodeURIComponent(docId)}/completeness/verify`), {
      method: 'POST',
      headers: authHeaders(this.functionKey, token)
    });
    if (!response.ok) {
      throw new Error(`Verification request failed: ${response.statusText}`);
    }
    const data = await response.json();
    await browserCache.set(`protocol_completeness:${docId}`, null, 0);
    return data.statusQueryGetUri || data.status_uri || '';
  }

  public async checkVerificationStatus(statusUrl: string): Promise<{ runtimeStatus: string }> {
    const token = await this.tokenService?.getAccessToken();
    const response = await fetch(statusUrl, {
      headers: authHeaders(this.functionKey, token)
    });
    if (!response.ok) {
      throw new Error(`Status query failed: ${response.statusText}`);
    }
    return response.json();
  }
}
