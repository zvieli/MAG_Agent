import { createTimelineFromGraph } from '../domain/timeline';
import { TimelineResult } from '../domain/types';
import { browserCache } from '../domain/browserCache';
import { IGraphService, ITimelineService } from './IAppServices';

const timelineTtlMs = 30 * 60 * 1000;

export class TimelineService implements ITimelineService {
  private readonly graphService: IGraphService;

  public constructor(graphService: IGraphService) {
    this.graphService = graphService;
  }

  public async getTimeline(docId: string, options?: { forceRefresh?: boolean }): Promise<TimelineResult> {
    const cacheKey = `timeline:${docId}`;
    if (!options?.forceRefresh) {
      const cached = await browserCache.get<TimelineResult>(cacheKey);
      if (cached) {
        return {
          ...cached.value,
          cache: { fromCache: true, cachedAt: new Date(cached.createdAt).toISOString() }
        };
      }
    }

    const graph = await this.graphService.getTimelineGraph({ docId, limit: 1000 }, options);
    const result = createTimelineFromGraph(graph);
    await browserCache.set(cacheKey, result, timelineTtlMs);
    return { ...result, cache: { fromCache: false } };
  }
}
