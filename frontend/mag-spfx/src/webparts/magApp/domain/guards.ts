import { DurableStatus, PipelineOutput, RuntimeStatus } from './types';

const runtimeStatuses: RuntimeStatus[] = [
  'Pending',
  'Running',
  'Completed',
  'ContinuedAsNew',
  'Failed',
  'Canceled',
  'Terminated',
  'Unknown'
];

export const normalizeRuntimeStatus = (status: string | undefined): RuntimeStatus => {
  const normalized = (status || '').split('.').pop() as RuntimeStatus;
  return runtimeStatuses.indexOf(normalized) >= 0 ? normalized : 'Unknown';
};

export const isTerminalStatus = (status: RuntimeStatus): boolean =>
  status === 'Completed' || status === 'Failed' || status === 'Canceled' || status === 'Terminated';

export const isSuccessfulPipeline = (status?: DurableStatus<PipelineOutput>): boolean =>
  status?.runtimeStatus === 'Completed' && status.output?.status === 'completed';

export const deriveDocId = (fileName: string, status?: DurableStatus<PipelineOutput>): string | undefined => {
  if (status?.customStatus?.doc_id) {
    return status.customStatus.doc_id;
  }

  const blobPath = status?.output?.blob_path || status?.customStatus?.blob_path;
  if (blobPath) {
    const rawName = blobPath.split('/').pop() || blobPath;
    return rawName.replace(/\.pdf$/i, '');
  }

  if (fileName) {
    return fileName.replace(/\.pdf$/i, '');
  }

  return undefined;
};

export const normalizeApiBaseUrl = (apiBaseUrl: string): string => {
  return (apiBaseUrl || '').trim().replace(/\/+$/, '');
};

export const makeId = (prefix: string): string =>
  `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
