import { SPHttpClient } from '@microsoft/sp-http';
import { MagAppConfig } from '../domain/types';
import { createDebugLogger } from '../domain/debugLogger';
import { createMockServices } from '../mocks/MockServices';
import { ChatService } from './ChatService';
import { GraphService } from './GraphService';
import { IAppServices } from './IAppServices';
import { PipelineService } from './PipelineService';
import { ReportService } from './ReportService';
import { TimelineService } from './TimelineService';
import { SharePointFileService } from './SharePointFileService';
import { TokenService } from './TokenService';

export const createServices = (config: MagAppConfig, spHttpClient: SPHttpClient): IAppServices => {
  const logger = createDebugLogger(config.enableDebugLogs);
  const sharePointFiles = new SharePointFileService(spHttpClient, config.sharePointSiteUrl);

  if (config.useMockData) {
    return { ...createMockServices(), sharePointFiles };
  }

  const tokenService = new TokenService(
    config.aadTokenProviderFactory,
    config.backendAppIdUri,
    logger
  );

  const graph = new GraphService(config.apiBaseUrl, config.functionKey, tokenService, logger);

  return {
    pipeline: new PipelineService(config.apiBaseUrl, config.functionKey, tokenService, logger),
    graph,
    report: new ReportService(config.apiBaseUrl, config.functionKey, tokenService),
    timeline: new TimelineService(graph),
    chat: new ChatService(config.apiBaseUrl, config.functionKey, tokenService, logger),
    sharePointFiles
  };
};
