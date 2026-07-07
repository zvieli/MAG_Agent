import { MagAppConfig } from '../domain/types';
import { createDebugLogger } from '../domain/debugLogger';
import { createMockServices } from '../mocks/MockServices';
import { ChatService } from './ChatService';
import { GraphService } from './GraphService';
import { IAppServices } from './IAppServices';
import { PipelineService } from './PipelineService';
import { ReportService } from './ReportService';
import { TimelineService } from './TimelineService';

export const createServices = (config: MagAppConfig): IAppServices => {
  const logger = createDebugLogger(config.enableDebugLogs);

  if (config.useMockData) {
    return createMockServices();
  }

  const graph = new GraphService(config.apiBaseUrl, config.functionKey, logger);

  return {
    pipeline: new PipelineService(config.apiBaseUrl, config.functionKey, logger),
    graph,
    report: new ReportService(config.apiBaseUrl, config.functionKey),
    timeline: new TimelineService(graph),
    chat: new ChatService(config.apiBaseUrl, config.functionKey, logger)
  };
};
