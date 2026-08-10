import { CommunitySummariesResult, ProtocolCompletenessResult, ReportResult } from '../domain/types';
import { browserCache } from '../domain/browserCache';
import { IReportService } from './IAppServices';
import { BackendAuthService, requireBackendAuthService } from './BackendAuthService';
import { isEntraAuthenticationError } from './TokenService';
import { apiUrl, readJson } from './http';

const reportTtlMs = 30 * 60 * 1000;

export class ReportService implements IReportService {
  private readonly apiBaseUrl: string;
  private readonly backendAuth: BackendAuthService;

  public constructor(apiBaseUrl: string, backendAuth: BackendAuthService) {
    this.apiBaseUrl = apiBaseUrl;
    this.backendAuth = requireBackendAuthService(backendAuth, 'ReportService');
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
      const url = apiUrl(this.apiBaseUrl, `/api/cases/${encodeURIComponent(docId)}/report`);
      const result = await this.backendAuth.fetch({ operation: 'report-load', url }).then(readJson<ReportResult>);
      await browserCache.set(cacheKey, result, reportTtlMs);
      return { ...result, cache: { fromCache: false } };
    } catch (err) {
      if (isEntraAuthenticationError(err)) throw err;
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
      const url = apiUrl(this.apiBaseUrl, `/api/cases/${encodeURIComponent(docId)}/communities`);
      const result = await this.backendAuth.fetch({ operation: 'community-summaries-load', url }).then(readJson<CommunitySummariesResult>);
      await browserCache.set(cacheKey, result, reportTtlMs);
      return { ...result, cache: { fromCache: false } };
    } catch (err) {
      if (isEntraAuthenticationError(err)) throw err;
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
      const url = apiUrl(this.apiBaseUrl, `/api/cases/${encodeURIComponent(docId)}/completeness`);
      const result = await this.backendAuth.fetch({ operation: 'protocol-completeness-load', url }).then(readJson<ProtocolCompletenessResult>);
      await browserCache.set(cacheKey, result, reportTtlMs);
      return { ...result, cache: { fromCache: false } };
    } catch (err) {
      if (isEntraAuthenticationError(err)) throw err;
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
    const url = apiUrl(this.apiBaseUrl, `/api/cases/${encodeURIComponent(docId)}/completeness/verify`);
    const response = await this.backendAuth.fetch(
      { operation: 'protocol-completeness-verify', url },
      { method: 'POST' }
    );
    if (!response.ok) {
      throw new Error(`Verification request failed: ${response.statusText}`);
    }
    const data = await response.json();
    await browserCache.set(`protocol_completeness:${docId}`, null, 0);
    return data.statusQueryGetUri || data.status_uri || '';
  }

  public async checkVerificationStatus(statusUrl: string): Promise<{ runtimeStatus: string }> {
    const response = await this.backendAuth.fetch({ operation: 'protocol-verification-status', url: statusUrl });
    if (!response.ok) {
      throw new Error(`Status query failed: ${response.statusText}`);
    }
    return response.json();
  }
}
