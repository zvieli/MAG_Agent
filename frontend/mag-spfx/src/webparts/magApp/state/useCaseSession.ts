import { useCallback, useState } from 'react';
import { browserCache } from '../domain/browserCache';
import { isSuccessfulPipeline } from '../domain/guards';
import { CaseSession, PipelineOperation } from '../domain/types';

export interface CaseSessionState {
  session: CaseSession;
  setPipeline: (pipeline: PipelineOperation) => void;
  clearSession: () => void;
}

export const useCaseSession = (): CaseSessionState => {
  const [session, setSession] = useState<CaseSession>({});

  const setPipeline = useCallback((pipeline: PipelineOperation): void => {
    const completed = isSuccessfulPipeline({
      instanceId: pipeline.instanceId,
      runtimeStatus: pipeline.status,
      customStatus: pipeline.customStatus,
      output: pipeline.output
    });

    if (completed && pipeline.docId) {
      browserCache.clearByPrefix(`graph:${pipeline.docId}`).catch(() => undefined);
      browserCache.clearByPrefix(`report:${pipeline.docId}`).catch(() => undefined);
      browserCache.clearByPrefix(`community_summaries:${pipeline.docId}`).catch(() => undefined);
      browserCache.clearByPrefix(`timeline:${pipeline.docId}`).catch(() => undefined);
      browserCache.clearByPrefix(`protocol_completeness:${pipeline.docId}`).catch(() => undefined);
      browserCache.clearByPrefix(`chat:${pipeline.docId}:`).catch(() => undefined);
    }

    setSession({
      docId: completed ? pipeline.docId : undefined,
      pipeline,
      completedAt: completed ? new Date().toISOString() : undefined
    });
  }, []);

  const clearSession = useCallback((): void => setSession({}), []);

  return {
    session,
    setPipeline,
    clearSession
  };
};
