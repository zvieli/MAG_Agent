import { CommunitySummariesResult, ProtocolCompletenessResult, ReportResult } from '../domain/types';
import { browserCache } from '../domain/browserCache';
import { IReportService } from './IAppServices';
import { apiUrl, authHeaders, readJson } from './http';

const reportTtlMs = 30 * 60 * 1000;

export class ReportService implements IReportService {
  private readonly apiBaseUrl: string;
  private readonly functionKey: string;

  public constructor(apiBaseUrl: string, functionKey: string) {
    this.apiBaseUrl = apiBaseUrl;
    this.functionKey = functionKey;
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

    const result = await fetch(apiUrl(this.apiBaseUrl, `/api/cases/${encodeURIComponent(docId)}/report`), {
      headers: authHeaders(this.functionKey)
    }).then(readJson<ReportResult>);
    await browserCache.set(cacheKey, result, reportTtlMs);
    return { ...result, cache: { fromCache: false } };
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

    const result = await fetch(apiUrl(this.apiBaseUrl, `/api/cases/${encodeURIComponent(docId)}/communities`), {
      headers: authHeaders(this.functionKey)
    }).then(readJson<CommunitySummariesResult>);
    await browserCache.set(cacheKey, result, reportTtlMs);
    return { ...result, cache: { fromCache: false } };
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

    const result = await fetch(apiUrl(this.apiBaseUrl, `/api/cases/${encodeURIComponent(docId)}/completeness`), {
      headers: authHeaders(this.functionKey)
    }).then(readJson<ProtocolCompletenessResult>);
    await browserCache.set(cacheKey, result, reportTtlMs);
    return { ...result, cache: { fromCache: false } };
  }

  public async verifyProtocolCompletenessWithAgent(docId: string): Promise<string> {
    const response = await fetch(apiUrl(this.apiBaseUrl, `/api/cases/${encodeURIComponent(docId)}/completeness/verify`), {
      method: 'POST',
      headers: authHeaders(this.functionKey)
    });
    if (!response.ok) {
      throw new Error(`Verification request failed: ${response.statusText}`);
    }
    const data = await response.json();
    return data.statusQueryGetUri || data.status_uri || '';
  }

  public async checkVerificationStatus(statusUrl: string): Promise<{ runtimeStatus: string }> {
    const response = await fetch(statusUrl);
    if (!response.ok) {
      throw new Error(`Status query failed: ${response.statusText}`);
    }
    return response.json();
  }
}

