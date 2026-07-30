import { makeId } from '../domain/guards';
import {
  ChatAnswer,
  ChatOperation,
  DurableStatus,
  GraphQuery,
  GraphQueryResult,
  PipelineOperation,
  ProtocolCompletenessResult,
  ReportResult,
  CommunitySummariesResult,
  TimelineResult
} from '../domain/types';
import { createTimelineFromGraph } from '../domain/timeline';
import { IAppServices, IChatService, IGraphService, IPipelineService, IReportService, ITimelineService } from '../services/IAppServices';
import { mockCommunitySummaries, mockGraph, mockProtocolCompleteness, mockReport } from './mockData';

class MockPipelineService implements IPipelineService {
  public async uploadAndStart(file: File, onProgress?: (operation: PipelineOperation) => void): Promise<PipelineOperation> {
    const docId = file.name.replace(/\.pdf$/i, '');
    const startedAtMs = Date.now();
    onProgress?.({
      fileName: file.name,
      instanceId: makeId('mock-upload'),
      statusUrl: '',
      status: 'Pending',
      startedAtMs,
      clientStage: 'uploading',
      uploadProgress: 1,
      elapsedMs: 0
    });
    return {
      fileName: file.name,
      instanceId: makeId('pipeline'),
      statusUrl: `mock://pipelines/${docId}`,
      status: 'Running',
      clientStage: 'polling',
      uploadProgress: 1,
      elapsedMs: Date.now() - startedAtMs,
      docId,
      customStatus: {
        phase: 'upload',
        message: 'העלאת הדגמה התקבלה',
        doc_id: docId,
        total_chunks: 12,
        completed_chunks: 0
      }
    };
  }

  public async getStatus(operation: PipelineOperation): Promise<PipelineOperation> {
    const completedChunks = Math.min((operation.customStatus?.completed_chunks || 0) + 4, 12);
    const complete = completedChunks >= 12;

    return {
      ...operation,
      status: complete ? 'Completed' : 'Running',
      clientStage: 'processing',
      uploadProgress: operation.uploadProgress,
      elapsedMs: operation.startedAtMs ? Date.now() - operation.startedAtMs : operation.elapsedMs,
      customStatus: {
        phase: complete ? 'completed' : 'extracting',
        message: complete ? 'העיבוד הושלם' : `מחלץ מקטעי גרף (${completedChunks}/12)`,
        doc_id: operation.docId,
        total_chunks: 12,
        completed_chunks: completedChunks,
        last_error: null
      },
      output: complete
        ? {
            status: 'completed',
            blob_path: `uploads/${operation.fileName}`,
            final_graph_path: `cosmos://${operation.docId}`
          }
        : undefined
    };
  }
}

class MockGraphService implements IGraphService {
  public async getGraph(query: GraphQuery): Promise<GraphQueryResult> {
    return mockGraph(query.docId);
  }
  public async getTimelineGraph(query: GraphQuery): Promise<GraphQueryResult> {
    return mockGraph(query.docId);
  }
}

class MockReportService implements IReportService {
  public async getReport(docId: string): Promise<ReportResult> {
    return mockReport(docId);
  }

  public async getCommunitySummaries(docId: string): Promise<CommunitySummariesResult> {
    return mockCommunitySummaries(docId);
  }

  public async getProtocolCompleteness(docId: string): Promise<ProtocolCompletenessResult> {
    return mockProtocolCompleteness(docId);
  }
}

class MockTimelineService implements ITimelineService {
  public async getTimeline(docId: string): Promise<TimelineResult> {
    return createTimelineFromGraph(mockGraph(docId));
  }
}

class MockChatService implements IChatService {
  public async ask(_docId: string, _question: string): Promise<ChatOperation> {
    return {
      instanceId: makeId('query'),
      statusUrl: 'mock://query'
    };
  }

  public async getStatus(_statusUrl: string): Promise<DurableStatus<ChatAnswer>> {
    return {
      instanceId: makeId('query-status'),
      runtimeStatus: 'Completed',
      output: {
        answer:
          'תשובת הדגמה עבור התיק הפעיל. בסביבת ייצור הסוכן שולח את השאלה עם מזהה התיק ומחזיר תשובה מבוססת מקורות.'
      },
      customStatus: null
    };
  }
}

export const createMockServices = (): Omit<IAppServices, 'sharePointFiles'> => ({
  pipeline: new MockPipelineService(),
  graph: new MockGraphService(),
  report: new MockReportService(),
  timeline: new MockTimelineService(),
  chat: new MockChatService()
});
