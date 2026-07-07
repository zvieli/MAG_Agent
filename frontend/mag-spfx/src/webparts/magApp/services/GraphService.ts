import { GraphQuery, GraphQueryResult } from '../domain/types';
import { browserCache } from '../domain/browserCache';
import { DebugLogger } from '../domain/debugLogger';
import { displayName, looksTechnicalId } from '../domain/displayNames';
import { IGraphService } from './IAppServices';
import { apiUrl, authHeaders, readJson } from './http';

const graphTtlMs = 30 * 60 * 1000;

const graphCacheKey = (query: GraphQuery): string =>
  `graph:${query.docId}:${query.search || ''}:${query.rootNodeId || ''}:${query.depth || ''}:${query.limit || ''}`;

const hasGraphData = (graph: GraphQueryResult): boolean => graph.nodes.length > 0 || graph.edges.length > 0;

const legacyEncodedDocId = (docId: string): string | undefined => {
  if (!docId || docId.indexOf('%') >= 0) return undefined;

  const encoded = encodeURIComponent(docId);
  return encoded !== docId ? encoded : undefined;
};

const labelKeys = ['label', 'name', 'display_name', 'title'];

const textValue = (value: unknown): string | undefined => {
  if (typeof value === 'string') return value.trim() || undefined;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return undefined;
};

const preferredLabel = (...values: Array<unknown>): string | undefined => {
  const candidates = values.map(textValue).filter((value): value is string => Boolean(value));
  return candidates.find((value) => !looksTechnicalId(value)) || candidates[0];
};

const propertyLabel = (properties: Record<string, string | number | boolean | undefined> | undefined): string | undefined => {
  if (!properties) return undefined;
  return preferredLabel(...labelKeys.map((key) => properties[key]));
};

const normalizedGraph = (graph: GraphQueryResult, logger?: DebugLogger): GraphQueryResult => {
  const labelById = new Map<string, string>();
  let technicalBefore = 0;
  let fixedLabels = 0;

  const rememberLabel = (id: string | undefined, label: string | undefined): void => {
    if (!id || !label || looksTechnicalId(label)) return;
    labelById.set(id, label);
  };

  for (const node of graph.nodes) {
    const label = preferredLabel(propertyLabel(node.properties), node.label);
    rememberLabel(node.id, label);
  }

  for (const edge of graph.edges) {
    rememberLabel(edge.source, edge.sourceLabel);
    rememberLabel(edge.target, edge.targetLabel);
  }

  const nodes = graph.nodes.map((node) => {
    if (looksTechnicalId(node.label)) {
      technicalBefore += 1;
    }

    const resolvedLabel = preferredLabel(labelById.get(node.id), propertyLabel(node.properties), node.label, node.id);
    const label = displayName(resolvedLabel, node.id);
    if (label !== node.label && !looksTechnicalId(label)) {
      fixedLabels += 1;
      labelById.set(node.id, label);
    }

    return { ...node, label };
  });

  const edges = graph.edges.map((edge) => {
    const sourceLabel = preferredLabel(edge.sourceLabel, labelById.get(edge.source), edge.source);
    const targetLabel = preferredLabel(edge.targetLabel, labelById.get(edge.target), edge.target);
    const normalizedSourceLabel = displayName(sourceLabel, edge.source);
    const normalizedTargetLabel = displayName(targetLabel, edge.target);

    if (looksTechnicalId(edge.sourceLabel) || looksTechnicalId(edge.targetLabel)) {
      technicalBefore += 1;
    }
    if ((edge.sourceLabel !== normalizedSourceLabel && !looksTechnicalId(normalizedSourceLabel)) ||
        (edge.targetLabel !== normalizedTargetLabel && !looksTechnicalId(normalizedTargetLabel))) {
      fixedLabels += 1;
    }

    return {
      ...edge,
      sourceLabel: normalizedSourceLabel,
      targetLabel: normalizedTargetLabel
    };
  });

  const unresolved = nodes
    .filter((node) => looksTechnicalId(node.label))
    .slice(0, 8)
    .map((node) => node.id);

  if (technicalBefore > 0 || unresolved.length > 0) {
    logger?.info('Graph label normalization', {
      docId: graph.docId,
      technicalBefore,
      fixedLabels,
      unresolved
    });
  }

  return {
    ...graph,
    nodes,
    edges
  };
};

export class GraphService implements IGraphService {
  private readonly apiBaseUrl: string;
  private readonly functionKey: string;
  private readonly logger?: DebugLogger;

  public constructor(apiBaseUrl: string, functionKey: string, logger?: DebugLogger) {
    this.apiBaseUrl = apiBaseUrl;
    this.functionKey = functionKey;
    this.logger = logger;
  }

  public async getGraph(query: GraphQuery, options?: { forceRefresh?: boolean }): Promise<GraphQueryResult> {
    const cacheKey = graphCacheKey(query);
    if (!options?.forceRefresh) {
      const cached = await browserCache.get<GraphQueryResult>(cacheKey);
      if (cached && hasGraphData(cached.value)) {
        const normalizedCached = normalizedGraph(cached.value, this.logger);
        return {
          ...normalizedCached,
          cache: { fromCache: true, cachedAt: new Date(cached.createdAt).toISOString() }
        };
      }
    }

    const graphSuffix = (partitionKey: string): string => {
      const params = new URLSearchParams();
      params.set('pk', partitionKey);
      if (query.search) params.set('search', query.search);
      if (query.rootNodeId) params.set('root', query.rootNodeId);
      if (query.depth) params.set('depth', String(query.depth));
      if (query.limit) params.set('limit', String(query.limit));
      return params.toString() ? `?${params.toString()}` : '';
    };

    const fetchGraph = async (docId: string, partitionKey: string): Promise<GraphQueryResult> =>
      fetch(apiUrl(this.apiBaseUrl, `/api/cases/${encodeURIComponent(docId)}/graph${graphSuffix(partitionKey)}`), {
        headers: authHeaders(this.functionKey)
      }).then(readJson<GraphQueryResult>);

    let result = await fetchGraph(query.docId, query.docId);
    const legacyDocId = legacyEncodedDocId(query.docId);
    if (!hasGraphData(result) && legacyDocId) {
      this.logger?.info('Graph empty for decoded docId; retrying with legacy encoded partition key', {
        docId: query.docId,
        legacyDocId
      });
      result = await fetchGraph(query.docId, legacyDocId);
    }

    const normalized = normalizedGraph(result, this.logger);
    await browserCache.set(cacheKey, normalized, graphTtlMs);
    return { ...normalized, cache: { fromCache: false } };
  }

  public async getTimelineGraph(query: GraphQuery, options?: { forceRefresh?: boolean }): Promise<GraphQueryResult> {
    const cacheKey = `timeline_graph:${query.docId}:${query.limit || 1000}`;
    if (!options?.forceRefresh) {
      const cached = await browserCache.get<GraphQueryResult>(cacheKey);
      if (cached && hasGraphData(cached.value)) {
        return {
          ...cached.value,
          cache: { fromCache: true, cachedAt: new Date(cached.createdAt).toISOString() }
        };
      }
    }

    const timelineSuffix = (partitionKey: string): string => {
      const params = new URLSearchParams();
      params.set('pk', partitionKey);
      if (query.limit) params.set('limit', String(query.limit));
      return params.toString() ? `?${params.toString()}` : '';
    };

    const fetchTimelineGraph = async (docId: string, partitionKey: string): Promise<GraphQueryResult> =>
      fetch(apiUrl(this.apiBaseUrl, `/api/cases/${encodeURIComponent(docId)}/timeline${timelineSuffix(partitionKey)}`), {
        headers: authHeaders(this.functionKey)
      }).then(readJson<GraphQueryResult>);

    let result = await fetchTimelineGraph(query.docId, query.docId);
    const legacyDocId = legacyEncodedDocId(query.docId);
    if (!hasGraphData(result) && legacyDocId) {
      this.logger?.info('Timeline graph empty for decoded docId; retrying with legacy encoded partition key', {
        docId: query.docId,
        legacyDocId
      });
      result = await fetchTimelineGraph(query.docId, legacyDocId);
    }

    await browserCache.set(cacheKey, result, graphTtlMs);
    return { ...result, cache: { fromCache: false } };
  }
}
