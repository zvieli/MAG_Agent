import { AadTokenProviderFactory } from '@microsoft/sp-http';

export type AppPage = 'upload' | 'graph' | 'report' | 'timeline' | 'chat';

export type RuntimeStatus =
  | 'Pending'
  | 'Running'
  | 'Completed'
  | 'ContinuedAsNew'
  | 'Failed'
  | 'Canceled'
  | 'Terminated'
  | 'Unknown';

export interface PipelineCustomStatus {
  phase?: string;
  message?: string;
  blob_path?: string;
  doc_id?: string;
  trace_id?: string;
  total_chunks?: number;
  completed_chunks?: number;
  current_batch?: number;
  last_error?: string | null;
}

export interface DurableStatus<TOutput = unknown> {
  instanceId: string;
  runtimeStatus: RuntimeStatus;
  createdTime?: string;
  lastUpdatedTime?: string;
  output?: TOutput;
  customStatus?: PipelineCustomStatus | null;
}

export interface DurableStartResponse {
  id?: string;
  statusQueryGetUri?: string;
  sendEventPostUri?: string;
  terminatePostUri?: string;
  rewindPostUri?: string;
  purgeHistoryDeleteUri?: string;
}

export interface PipelineOutput {
  status?: string;
  blob_path?: string;
  final_graph_path?: string | null;
  reused_existing_case?: boolean;
}

export interface PipelinePreflightResult {
  status: 'duplicate' | 'new' | 'error';
  doc_id: string;
  matched_doc_id?: string | null;
  matched_by?: 'partitionKey' | 'source_file' | 'none';
  sample_node?: {
    id: string;
    label: string;
    type: string;
  } | null;
  cache_hit?: boolean;
  error?: string;
  trace_id?: string;
}

export interface PipelineOperation {
  fileName: string;
  instanceId: string;
  statusUrl: string;
  traceId?: string;
  startedAtMs?: number;
  clientStage?: 'preflight' | 'uploading' | 'starting' | 'polling' | 'processing';
  uploadProgress?: number;
  elapsedMs?: number;
  docId?: string;
  status: RuntimeStatus;
  customStatus?: PipelineCustomStatus | null;
  output?: PipelineOutput;
  error?: string;
}

export interface CaseSession {
  docId?: string;
  pipeline?: PipelineOperation;
  completedAt?: string;
}

export interface SPFile {
  name: string;
  serverRelativeUrl: string;
  absoluteUrl: string;
  lastModified?: string;
  size: number;
}

export interface GraphNode {
  id: string;
  label: string;
  type: string;
  description?: string;
  communityId?: string;
  degree?: number;
  properties?: Record<string, string | number | boolean | undefined>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  sourceLabel?: string;
  targetLabel?: string;
  relation: string;
  description?: string;
  confidence?: number;
  sourceLocation?: string;
  properties?: Record<string, string | number | boolean | undefined>;
}

export interface GraphQuery {
  docId: string;
  search?: string;
  rootNodeId?: string;
  depth?: number;
  limit?: number;
}

export interface CacheMetadata {
  fromCache?: boolean;
  cachedAt?: string;
}

export interface GraphQueryResult {
  docId: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  source: 'gremlin' | 'mock';
  metadata: {
    nodeCount: number;
    edgeCount: number;
    nodeTypes: string[];
    generatedAt: string;
  };
  cache?: CacheMetadata;
}

export interface ReportResult {
  docId: string;
  markdown: string;
  source: 'blob' | 'mock';
  updatedAt: string;
  cache?: CacheMetadata;
  error?: string;
}

export interface CommunitySummariesResult {
  docId: string;
  summaries: Record<string, string>;
  hierarchy: Record<string, string>;
  source: 'blob' | 'mock';
  updatedAt: string;
  cache?: CacheMetadata;
  error?: string;
}

export type ProtocolCompletenessStatus =
  | 'present'
  | 'missing'
  | 'conditional_missing'
  | 'not_applicable'
  | 'unknown';

export interface ProtocolCompletenessEvidence {
  source: string;
  signal: string;
  snippet: string;
  strength?: 'strong' | 'weak' | 'none' | string;
  matchType?: 'regex' | 'keyword' | string;
  pattern?: string;
}

export interface ProtocolCompletenessApplicabilityEvidence {
  source: string;
  signal: string;
  snippet: string;
  matchType?: 'regex' | 'keyword' | string;
  pattern?: string;
}

export interface AgentVerificationDetails {
  verifiedByAgent: boolean;
  verbatimQuote?: string;
  sourceLocation?: string;
  explanation?: string;
  verifiedAt?: string;
}

export interface ProtocolCompletenessItem {
  id: string;
  pillar: string;
  label: string;
  description: string;
  severity: 'critical' | 'warning' | 'info' | string;
  status: ProtocolCompletenessStatus;
  reason: string;
  applicability: string;
  applicabilityEvidence?: ProtocolCompletenessApplicabilityEvidence;
  evidenceStrength?: 'strong' | 'weak' | 'none' | string;
  reviewNote?: string;
  evidence: ProtocolCompletenessEvidence[];
  sourceRefs: Array<Record<string, string | number | undefined>>;
  agentVerification?: AgentVerificationDetails;
}


export interface ProtocolCompletenessResult {
  docId: string;
  protocolVersion?: string;
  generatedAt?: string;
  updatedAt?: string;
  source: 'blob' | 'mock' | 'backfill';
  disclaimer?: string;
  summary: {
    totalItems?: number;
    applicableItems?: number;
    present?: number;
    missing?: number;
    conditionalMissing?: number;
    notApplicable?: number;
    unknown?: number;
    criticalMissing?: number;
    criticalReviewRequired?: number;
    coveragePercent?: number;
    overallScore?: number;
    itemsAnalyzed?: number;
    missingItemsCount?: number;
    overallStatus?: string;
  };
  items: ProtocolCompletenessItem[];
  cache?: CacheMetadata;
  error?: string;
}

export interface TimelineEvent {
  id: string;
  title: string;
  date?: string;
  dateLabel: string;
  sortKey?: string;
  participants: string[];
  relation?: string;
  sourceLocation?: string;
  description?: string;
  confidence?: number;
  kind: 'node' | 'edge';
}

export interface TimelineResult {
  docId: string;
  source: 'graph' | 'mock';
  events: TimelineEvent[];
  generatedAt: string;
  cache?: CacheMetadata;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  createdAt: string;
  status?: 'sending' | 'complete' | 'failed';
}

export interface ChatAnswer {
  answer: string;
  state?: any;
}

export interface ChatOperation {
  instanceId: string;
  statusUrl: string;
  traceId?: string;
  startedAtMs?: number;
}

export interface MagAppConfig {
  apiBaseUrl: string;
  functionKey: string;
  useMockData: boolean;
  pollIntervalMs: number;
  enableDebugLogs: boolean;
  sharePointSiteUrl: string;
  sharePointFolderServerRelativeUrl: string;
  aadTokenProviderFactory?: AadTokenProviderFactory;
  backendAppIdUri?: string;
}
