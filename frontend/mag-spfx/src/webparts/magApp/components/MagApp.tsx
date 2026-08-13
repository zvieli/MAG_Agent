import * as React from 'react';
import { SPHttpClient } from '@microsoft/sp-http';
import { AppPage, MagAppConfig } from '../domain/types';
import { createServices } from '../services/createServices';
import { PageShell } from './PageShell';
import { useCaseSession } from '../state/useCaseSession';
import { UploadPage } from '../pages/UploadPage';
import { GraphPage } from '../pages/GraphPage';
import { ReportPage } from '../pages/ReportPage';
import { TimelinePage } from '../pages/TimelinePage';
import { ChatPage } from '../pages/ChatPage';
import { AuthenticationGate } from './AuthenticationGate';
import { isLoopbackApiUrl } from '../services/BackendAuthService';

export interface MagAppProps {
  config: MagAppConfig;
  spHttpClient: SPHttpClient;
}

export const MagApp: React.FC<MagAppProps> = ({ config, spHttpClient }) => {
  const [activePage, setActivePage] = React.useState<AppPage>('upload');
  const services = React.useMemo(() => createServices(config, spHttpClient), [config, spHttpClient]);
  const { session, setPipeline } = useCaseSession();

  const navigate = (page: AppPage): void => {
    if (page !== 'upload' && !session.docId) {
      return;
    }
    setActivePage(page);
  };

  const renderPage = (): React.ReactNode => {
    switch (activePage) {
      case 'graph':
        return <GraphPage docId={session.docId} graphService={services.graph} />;
      case 'report':
        return <ReportPage docId={session.docId} reportService={services.report} />;
      case 'timeline':
        return <TimelinePage docId={session.docId} timelineService={services.timeline} />;
      case 'chat':
        return <ChatPage docId={session.docId} chatService={services.chat} pollIntervalMs={config.pollIntervalMs} />;
      case 'upload':
      default:
        return (
          <UploadPage
            pipelineService={services.pipeline}
            sharePointFileService={services.sharePointFiles}
            sharePointFolderServerRelativeUrl={config.sharePointFolderServerRelativeUrl}
            pollIntervalMs={config.pollIntervalMs}
            currentPipeline={session.pipeline}
            onPipelineChange={setPipeline}
            onComplete={() => setActivePage('graph')}
          />
        );
    }
  };

  const application = (
    <PageShell activePage={activePage} session={session} onNavigate={navigate}>
      {renderPage()}
    </PageShell>
  );

  if (config.useMockData || isLoopbackApiUrl(config.apiBaseUrl)) return application;
  return <AuthenticationGate authentication={services.authentication}>{application}</AuthenticationGate>;
};
