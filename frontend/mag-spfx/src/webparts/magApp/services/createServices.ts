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
import { BackendAuthService } from './BackendAuthService';
import { getAuthenticationService } from '../auth/AuthenticationService';

export const createServices = (config: MagAppConfig, spHttpClient: SPHttpClient): IAppServices => {
  const logger = createDebugLogger(config.enableDebugLogs);
  const sharePointFiles = new SharePointFileService(spHttpClient, config.sharePointSiteUrl);
  const authentication = getAuthenticationService(config.sharePointUserLoginHint, logger);

  if (config.useMockData) {
    return { ...createMockServices(), authentication, sharePointFiles };
  }

  const backendAuth = new BackendAuthService(
    config.apiBaseUrl,
    authentication,
    logger
  );

  const graph = new GraphService(config.apiBaseUrl, backendAuth, logger);

  return {
    authentication,
    pipeline: new PipelineService(config.apiBaseUrl, backendAuth, logger),
    graph,
    report: new ReportService(config.apiBaseUrl, backendAuth),
    timeline: new TimelineService(graph),
    chat: new ChatService(config.apiBaseUrl, backendAuth, logger),
    sharePointFiles
  };
};
