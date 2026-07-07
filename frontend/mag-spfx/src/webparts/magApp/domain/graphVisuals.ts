import { GraphEdge, GraphNode } from './types';

const communityPalette = [
  '#0f766e',
  '#7c3aed',
  '#b45309',
  '#047857',
  '#be123c',
  '#2563eb',
  '#9333ea',
  '#4d7c0f',
  '#c2410c',
  '#475569'
];

const nodeTypeBorders: Record<string, string> = {
  PERSON: '#0f172a',
  ORGANIZATION: '#854d0e',
  EVENT: '#991b1b',
  LOCATION: '#166534'
};

const nodeTypeBackgrounds: Record<string, string> = {
  PERSON: '#eff6ff',
  ORGANIZATION: '#fff7ed',
  EVENT: '#fff1f2',
  LOCATION: '#f0fdf4'
};

const nodeTypeShortLabels: Record<string, string> = {
  PERSON: 'אדם',
  ORGANIZATION: 'ארגון',
  EVENT: 'אירוע',
  LOCATION: 'מקום'
};

const relationColors: Record<string, string> = {
  DIRECTED: '#6d28d9',
  FINANCIAL_TRANSFER: '#b45309',
  OCCURRED_AT: '#047857'
};

const hashText = (value: string): number => {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) % 9973;
  }
  return hash;
};

export const communityColor = (communityId: string | undefined): string => {
  const key = communityId || '0';
  return communityPalette[hashText(key) % communityPalette.length];
};

export const nodeBorderColor = (type: string): string => nodeTypeBorders[type] || '#334e68';

export const nodeBackgroundColor = (type: string): string => nodeTypeBackgrounds[type] || '#f8fafc';

export const nodeTypeShortLabel = (type: string): string => nodeTypeShortLabels[type] || type;

export const nodeShape = (type: string): string => {
  switch (type) {
    case 'PERSON':
      return 'ellipse';
    case 'ORGANIZATION':
      return 'round-rectangle';
    case 'EVENT':
      return 'diamond';
    case 'LOCATION':
      return 'hexagon';
    default:
      return 'ellipse';
  }
};

export const nodeSize = (degree: number | undefined): number => {
  const boundedDegree = Math.max(0, Math.min(degree || 0, 16));
  return 46 + boundedDegree * 3;
};

export const edgeColor = (relation: string): string => relationColors[relation] || '#718096';

export const edgeWidth = (confidence: number | undefined): number => {
  if (confidence === undefined) return 2;
  return confidence >= 0.85 ? 4 : confidence >= 0.65 ? 3 : 2;
};

export const edgeLineStyle = (confidence: number | undefined): string =>
  confidence !== undefined && confidence < 0.65 ? 'dashed' : 'solid';

export const confidenceLabel = (confidence: number | undefined): string => {
  if (confidence === undefined) return 'לא ידוע';
  if (confidence >= 0.85) return 'גבוהה';
  if (confidence >= 0.65) return 'בינונית';
  return 'נמוכה';
};

export const graphCommunities = (nodes: GraphNode[]): string[] => {
  const communities = new Set<string>();
  for (const node of nodes) {
    communities.add(node.communityId || '0');
  }
  return Array.from(communities).sort();
};

export const connectedNodeCount = (node: GraphNode | undefined, edges: GraphEdge[]): number => {
  if (!node) return 0;
  const connected = new Set<string>();
  for (const edge of edges) {
    if (edge.source === node.id) connected.add(edge.target);
    if (edge.target === node.id) connected.add(edge.source);
  }
  return connected.size;
};
