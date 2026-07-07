import { GraphEdge, GraphNode, GraphQueryResult, TimelineEvent, TimelineResult } from './types';
import { nodeTypeLabel, relationLabel } from './localization';
import { displayName } from './displayNames';

const eventTypes = new Set(['EVENT', 'אירוע', 'CONTEXT', 'context']);
const dateKeys = ['event_date', 'timestamp', 'occurrence_date', 'date'];

const twoDigits = (value: string): string => (value.length === 1 ? `0${value}` : value);

const propertyValue = (
  properties: Record<string, string | number | boolean | undefined> | undefined,
  key: string
): string | undefined => {
  const value = properties?.[key];
  return value === undefined || value === null ? undefined : String(value);
};

const firstValue = (
  properties: Record<string, string | number | boolean | undefined> | undefined,
  keys: string[]
): string | undefined => {
  for (const key of keys) {
    const value = propertyValue(properties, key);
    if (value && value !== 'unknown') return value;
  }
  return undefined;
};

const normalizeDate = (rawValue: string | undefined): string | undefined => {
  if (!rawValue) return undefined;
  const text = rawValue.trim();

  const iso = text.match(/\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b/);
  if (iso) {
    return `${iso[1]}-${twoDigits(iso[2])}-${twoDigits(iso[3])}`;
  }

  const local = text.match(/\b(\d{1,2})[-_/.](\d{1,2})[-_/.](\d{2,4})\b/);
  if (local) {
    const year = local[3].length === 2 ? `20${local[3]}` : local[3];
    return `${year}-${twoDigits(local[2])}-${twoDigits(local[1])}`;
  }

  return undefined;
};

const displayDate = (normalized: string | undefined): string => {
  if (!normalized) return 'תאריך לא ידוע';
  const [year, month, day] = normalized.split('-');
  return `${day}.${month}.${year}`;
};

const nodeIsEvent = (node: GraphNode): boolean => {
  const semanticClass = propertyValue(node.properties, 'meta_semantic_class') || '';
  return eventTypes.has(node.type) || eventTypes.has(node.label) || semanticClass.toLowerCase() === 'event';
};

const nodeDate = (node: GraphNode): string | undefined => {
  return normalizeDate(firstValue(node.properties, dateKeys));
};

const edgeDate = (edge: GraphEdge): string | undefined => {
  return normalizeDate(firstValue(edge.properties, dateKeys));
};

const compareEvents = (left: TimelineEvent, right: TimelineEvent): number => {
  if (left.sortKey && right.sortKey) return left.sortKey.localeCompare(right.sortKey);
  if (left.sortKey) return -1;
  if (right.sortKey) return 1;
  return left.title.localeCompare(right.title, 'he');
};

export const createTimelineFromGraph = (graph: GraphQueryResult): TimelineResult => {
  const nodesById = new Map<string, GraphNode>();
  graph.nodes.forEach((node) => nodesById.set(node.id, node));

  const events: TimelineEvent[] = [];

  graph.nodes.filter(nodeIsEvent).forEach((node) => {
    const normalized = nodeDate(node);
    if (!normalized) return;

    const relatedEdges = graph.edges.filter((edge) => edge.source === node.id || edge.target === node.id);
    const participants = relatedEdges
      .map((edge) => (edge.source === node.id ? nodesById.get(edge.target) : nodesById.get(edge.source)))
      .filter((participant): participant is GraphNode => Boolean(participant))
      .map((participant) => participant.label);

    events.push({
      id: `node-${node.id}`,
      title: node.label,
      date: normalized,
      dateLabel: displayDate(normalized),
      sortKey: normalized,
      participants,
      relation: nodeTypeLabel(node.type),
      sourceLocation: propertyValue(node.properties, 'source_location'),
      description: node.description,
      kind: 'node'
    });
  });

  graph.edges.forEach((edge) => {
    const normalized = edgeDate(edge);
    if (!normalized) return;

    const source = nodesById.get(edge.source);
    const target = nodesById.get(edge.target);
    const sourceLabel = displayName(edge.sourceLabel || source?.label, edge.source);
    const targetLabel = displayName(edge.targetLabel || target?.label, edge.target);

    events.push({
      id: `edge-${edge.id}`,
      title: `${sourceLabel} ← ${targetLabel}`,
      date: normalized,
      dateLabel: displayDate(normalized),
      sortKey: normalized,
      participants: [sourceLabel, targetLabel],
      relation: relationLabel(edge.relation),
      sourceLocation: edge.sourceLocation || propertyValue(edge.properties, 'source_location'),
      description: edge.description || propertyValue(edge.properties, 'description') || propertyValue(edge.properties, 'forensic_summary'),
      confidence: edge.confidence,
      kind: 'edge'
    });
  });

  return {
    docId: graph.docId,
    source: graph.source === 'mock' ? 'mock' : 'graph',
    events: events.sort(compareEvents),
    generatedAt: new Date().toISOString()
  };
};
