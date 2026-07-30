import {
  ChatAnswer,
  ChatOperation,
  CommunitySummariesResult,
  DurableStatus,
  GraphQuery,
  GraphQueryResult,
  PipelineOperation,
  PipelineOutput,
  ProtocolCompletenessResult,
  ReportResult,
  SPFile,
  TimelineResult
} from '../domain/types';

export interface IPipelineService {
  uploadAndStart(file: File, onProgress?: (operation: PipelineOperation) => void): Promise<PipelineOperation>;
  getStatus(operation: PipelineOperation): Promise<PipelineOperation>;
}

export interface IGraphService {
  getGraph(query: GraphQuery, options?: { forceRefresh?: boolean }): Promise<GraphQueryResult>;
  getTimelineGraph(query: GraphQuery, options?: { forceRefresh?: boolean }): Promise<GraphQueryResult>;
}

export interface IReportService {
  getReport(docId: string, options?: { forceRefresh?: boolean }): Promise<ReportResult>;
  getCommunitySummaries(docId: string, options?: { forceRefresh?: boolean }): Promise<CommunitySummariesResult>;
  getProtocolCompleteness(docId: string, options?: { forceRefresh?: boolean }): Promise<ProtocolCompletenessResult>;
  verifyProtocolCompletenessWithAgent(docId: string): Promise<string>;
  checkVerificationStatus(statusUrl: string): Promise<{ runtimeStatus: string }>;
}


export interface ITimelineService {
  getTimeline(docId: string, options?: { forceRefresh?: boolean }): Promise<TimelineResult>;
}

export interface IChatService {
  ask(docId: string, question: string, state?: any): Promise<ChatOperation>;
  getStatus(statusUrl: string, traceId?: string, startedAtMs?: number): Promise<DurableStatus<ChatAnswer>>;
}

export interface ISharePointFileService {
  listFiles(folderServerRelativeUrl: string): Promise<SPFile[]>;
  downloadFile(fileServerRelativeUrl: string, fileName: string): Promise<File>;
}

export interface IAppServices {
  pipeline: IPipelineService;
  graph: IGraphService;
  report: IReportService;
  timeline: ITimelineService;
  chat: IChatService;
  sharePointFiles: ISharePointFileService;
}

export type PipelineStatus = DurableStatus<PipelineOutput>;
