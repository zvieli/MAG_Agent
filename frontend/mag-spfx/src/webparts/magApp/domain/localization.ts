import { RuntimeStatus } from './types';
import { isEntraAuthenticationError } from '../auth/AuthenticationService';

const statusLabels: Record<RuntimeStatus, string> = {
  Pending: 'ממתין',
  Running: 'פועל',
  Completed: 'הושלם',
  ContinuedAsNew: 'הופעל מחדש',
  Failed: 'נכשל',
  Canceled: 'בוטל',
  Terminated: 'הופסק',
  Unknown: 'לא ידוע'
};

const phaseLabels: Record<string, string> = {
  upload: 'העלאה',
  duplicate: 'תיק קיים',
  preflight: 'בדיקת מוכנות',
  chunking: 'פיצול מסמך',
  extracting: 'חילוץ ישויות וקשרים',
  merging: 'איחוד תוצאות',
  indexing: 'שמירה ואינדוקס',
  completed: 'הושלם',
  failed: 'נכשל'
};

export const runtimeStatusLabel = (status: RuntimeStatus | undefined): string =>
  status ? statusLabels[status] || statusLabels.Unknown : statusLabels.Unknown;

export const pipelinePhaseLabel = (phase: string | undefined, fallback?: RuntimeStatus): string => {
  if (phase && phaseLabels[phase]) {
    return phaseLabels[phase];
  }

  return fallback ? runtimeStatusLabel(fallback) : 'ממתין';
};

export const technicalValue = (value: string | undefined, fallback = 'ממתין'): string => value || fallback;

export const errorText = (message: string): string => `אירעה שגיאה: ${message}`;

export const requestErrorText = (error: unknown, fallback: string): string => {
  if (isEntraAuthenticationError(error)) {
    return `שגיאת אבטחה ואימות: ${error.message}`;
  }
  return error instanceof Error ? errorText(error.message) : fallback;
};

export const sourceLabel = (source: string): string => {
  switch (source) {
    case 'gremlin':
      return 'שאילתות גרמלין';
    case 'blob':
      return 'אחסון דוחות';
    case 'mock':
      return 'נתוני דמה';
    default:
      return source;
  }
};

export const nodeTypeLabel = (type: string): string => {
  switch (type) {
    case 'PERSON':
      return 'אדם';
    case 'ORGANIZATION':
      return 'ארגון';
    case 'EVENT':
      return 'אירוע';
    case 'LOCATION':
      return 'מיקום';
    default:
      return type;
  }
};

export const relationLabel = (relation: string): string => {
  switch (relation) {
    case 'DIRECTED':
      return 'ניהל';
    case 'FINANCIAL_TRANSFER':
      return 'העברה כספית';
    case 'OCCURRED_AT':
      return 'התרחש ב';
    default:
      return relation;
  }
};
