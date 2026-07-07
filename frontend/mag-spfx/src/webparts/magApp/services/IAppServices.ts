import {
  ChatAnswer,
  ChatOperation,
  CommunitySummariesResult,
  DurableStatus,
  GraphQuery,
  GraphQueryResult,
  PipelineOperation,
  PipelineOutput,
  ReportResult,
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
}

export interface ITimelineService {
  getTimeline(docId: string, options?: { forceRefresh?: boolean }): Promise<TimelineResult>;
}

export interface IChatService {
  ask(docId: string, question: string): Promise<ChatOperation>;
  getStatus(statusUrl: string, traceId?: string, startedAtMs?: number): Promise<DurableStatus<ChatAnswer>>;
}

export interface IAppServices {
  pipeline: IPipelineService;
  graph: IGraphService;
  report: IReportService;
  timeline: ITimelineService;
  chat: IChatService;
}

export type PipelineStatus = DurableStatus<PipelineOutput>;
