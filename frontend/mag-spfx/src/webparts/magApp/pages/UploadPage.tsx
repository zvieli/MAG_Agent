import { DefaultButton, PrimaryButton, ProgressIndicator } from '@fluentui/react';
import * as React from 'react';
import styles from '../components/MagApp.module.scss';
import { isSuccessfulPipeline, isTerminalStatus } from '../domain/guards';
import { PipelineOperation } from '../domain/types';
import { IPipelineService } from '../services/IAppServices';
import { StatusBadge } from '../components/StatusBadge';
import { errorText, pipelinePhaseLabel, runtimeStatusLabel, technicalValue } from '../domain/localization';

export interface UploadPageProps {
  pipelineService: IPipelineService;
  pollIntervalMs: number;
  currentPipeline?: PipelineOperation;
  onPipelineChange: (pipeline: PipelineOperation) => void;
  onComplete: () => void;
}

export const UploadPage: React.FC<UploadPageProps> = ({
  pipelineService,
  pollIntervalMs,
  currentPipeline,
  onPipelineChange,
  onComplete
}) => {
  const [files, setFiles] = React.useState<File[]>([]);
  const [operations, setOperations] = React.useState<PipelineOperation[]>(currentPipeline ? [currentPipeline] : []);
  const [error, setError] = React.useState<string | undefined>();
  const [isStarting, setIsStarting] = React.useState(false);
  const completedNavigationRef = React.useRef(false);

  const clientStageLabel = (operation: PipelineOperation | undefined): string => {
    switch (operation?.clientStage) {
      case 'preflight':
        return 'בודק אם התיק כבר קיים';
      case 'uploading':
        return 'מעלה קובץ';
      case 'starting':
        return 'פותח עיבוד';
      case 'polling':
        return 'ממתין לתוצאות';
      case 'processing':
        return 'מעבד מסמך';
      default:
        return pipelinePhaseLabel(operation?.customStatus?.phase, operation?.status);
    }
  };

  const elapsedLabel = (elapsedMs: number | undefined): string => {
    if (!elapsedMs) return '0 שניות';
    const seconds = Math.floor(elapsedMs / 1000);
    if (seconds < 60) return `${seconds.toLocaleString('he-IL')} שניות`;
    const minutes = Math.floor(seconds / 60);
    const remaining = seconds % 60;
    return `${minutes.toLocaleString('he-IL')} דק׳ ${remaining.toLocaleString('he-IL')} שנ׳`;
  };

  React.useEffect(() => {
    if (currentPipeline && operations.length === 0) {
      setOperations([currentPipeline]);
    }
  }, [currentPipeline, operations.length]);

  React.useEffect(() => {
    const running = operations.filter((operation) => !isTerminalStatus(operation.status) && operation.statusUrl);
    if (!running.length) {
      return;
    }

    const timer = window.setInterval(async () => {
      try {
        const updatedRunning = await Promise.all(running.map((operation) => pipelineService.getStatus(operation)));
        const updatedById = new Map(updatedRunning.map((operation) => [operation.instanceId, operation]));
        const nextOperations = operations.map((operation) => updatedById.get(operation.instanceId) || operation);
        const completed = nextOperations.find((operation) => operation.status === 'Completed' && operation.output?.status === 'completed');
        const failed = nextOperations.find((operation) => operation.status === 'Failed');
        const representative = completed || nextOperations[0];

        setOperations(nextOperations);
        if (failed) {
          setError(failed.error ? `העיבוד נכשל: ${failed.error}` : 'העיבוד נכשל. יש לבדוק את יומני Azure עבור מופע התהליך.');
        }
        if (representative) {
          onPipelineChange(representative);
        }

        if (completed && !completedNavigationRef.current) {
          completedNavigationRef.current = true;
          window.clearInterval(timer);
          onComplete();
        }
      } catch (pollError) {
        setError(pollError instanceof Error ? errorText(pollError.message) : 'אירעה שגיאה בבדיקת מצב העיבוד.');
        window.clearInterval(timer);
      }
    }, pollIntervalMs);

    return () => window.clearInterval(timer);
  }, [operations, onComplete, onPipelineChange, pipelineService, pollIntervalMs]);

  const onFileInput = (event: React.ChangeEvent<HTMLInputElement>): void => {
    const selected = Array.from(event.target.files || []);
    setFiles(selected);
    setError(undefined);
  };

  const startPipeline = async (): Promise<void> => {
    if (!files.length) {
      setError('יש לבחור לפחות קובץ PDF אחד.');
      return;
    }

    const invalid = files.find((file) => !file.name.toLowerCase().endsWith('.pdf'));
    if (invalid) {
      setError('המערכת תומכת בשלב זה בקובצי PDF בלבד.');
      return;
    }

    setIsStarting(true);
    setError(undefined);

    try {
      completedNavigationRef.current = false;
      const started: PipelineOperation[] = [];
      for (const file of files) {
        const operation = await pipelineService.uploadAndStart(file, (progressOperation) => {
          setOperations((current) => {
            const existingIndex = current.findIndex((item) => item.fileName === progressOperation.fileName);
            if (existingIndex === -1) {
              return [...current, progressOperation];
            }
            return current.map((item, index) => (index === existingIndex ? { ...item, ...progressOperation } : item));
          });
          onPipelineChange(progressOperation);
        });
        started.push(operation);
      }
      setOperations(started);
      onPipelineChange(started[0]);
      const immediatelyCompleted = started.find((operation) => operation.status === 'Completed' && operation.output?.status === 'completed');
      if (immediatelyCompleted && !completedNavigationRef.current) {
        completedNavigationRef.current = true;
        onPipelineChange(immediatelyCompleted);
        onComplete();
      }
    } catch (startError) {
      setError(startError instanceof Error ? errorText(startError.message) : 'אירעה שגיאה בהתחלת העיבוד.');
    } finally {
      setIsStarting(false);
    }
  };

  const active = operations[0] || currentPipeline;
  const phase = clientStageLabel(active);
  const total = active?.customStatus?.total_chunks || 0;
  const completed = active?.customStatus?.completed_chunks || 0;
  const progress = active?.uploadProgress !== undefined ? active.uploadProgress : total > 0 ? completed / total : undefined;
  const isComplete = isSuccessfulPipeline(
    active
      ? {
          instanceId: active.instanceId,
          runtimeStatus: active.status,
          customStatus: active.customStatus,
          output: active.output
        }
      : undefined
  );

  const isAnyRunning = operations.some((op) => !isTerminalStatus(op.status));

  return (
    <div className={styles.pageGrid}>
      <section className={styles.panel}>
        <div className={styles.panelHeader}>
          <h2>העלאת מסמכים</h2>
          <StatusBadge label={isComplete ? 'מוכן' : phase} tone={isComplete ? 'success' : active ? 'running' : 'neutral'} />
        </div>
        <label className={styles.dropZone}>
          <i className="ms-Icon ms-Icon--CloudUpload" aria-hidden="true" />
          <span>בחר קובצי PDF</span>
          <input type="file" accept="application/pdf,.pdf" multiple onChange={onFileInput} />
        </label>
        {files.length > 0 && (
          <div className={styles.fileList}>
            {files.map((file) => (
              <div key={`${file.name}-${file.size}`} className={styles.fileRow}>
                <span className={styles.ltrValue}>{file.name}</span>
                <small>{Math.round(file.size / 1024).toLocaleString('he-IL')} ק"ב</small>
              </div>
            ))}
          </div>
        )}
        <div className={styles.actionRow}>
          <PrimaryButton text={isStarting ? 'מתחיל עיבוד...' : 'התחל עיבוד'} iconProps={{ iconName: 'Play' }} onClick={startPipeline} disabled={isStarting || isAnyRunning || !files.length} />
          <DefaultButton text="נקה" iconProps={{ iconName: 'Clear' }} onClick={() => setFiles([])} disabled={isStarting || isAnyRunning} />
        </div>
        {operations.length > 0 && (
          <div className={styles.pipelineBox}>
            {operations.map((operation) => (
              <div key={operation.instanceId} className={styles.operationCard}>
                <div className={styles.metaRow}>
                  <span className={styles.ltrValue}>{operation.fileName}</span>
                  <strong>{operation.output?.reused_existing_case ? 'תיק קיים' : runtimeStatusLabel(operation.status)}</strong>
                </div>
                <div className={styles.metaRow}>
                  <span>מזהה תיק</span>
                  <strong className={styles.ltrValue}>{technicalValue(operation.docId)}</strong>
                </div>
                <div className={styles.metaRow}>
                  <span>שלב</span>
                  <strong>{clientStageLabel(operation)}</strong>
                </div>
                <div className={styles.metaRow}>
                  <span>זמן שחלף</span>
                  <strong>{elapsedLabel(operation.elapsedMs)}</strong>
                </div>
                {operation.uploadProgress !== undefined && (
                  <ProgressIndicator
                    label="התקדמות העלאה"
                    percentComplete={operation.uploadProgress}
                    description={`${Math.round(operation.uploadProgress * 100).toLocaleString('he-IL')}%`}
                  />
                )}
                {operation.output?.reused_existing_case && (
                  <div className={styles.metaRow}>
                    <span>פעולה</span>
                    <strong>העיבוד דולג ונפתח התיק הקיים</strong>
                  </div>
                )}
              </div>
            ))}
            <ProgressIndicator label={phase} percentComplete={progress} />
          </div>
        )}
        {error && <div className={styles.errorBox}>{error}</div>}
      </section>

      <aside className={styles.sidePanel}>
        <h3>אופן העיבוד</h3>
        <p>המערכת שולחת כל קובץ PDF כבקשת עיבוד נפרדת ועוקבת אחר התקדמות התור.</p>
        <p>לאחר השלמה מוצלחת של תיק, נפתחות יכולות גרף הידע, הדו"ח והשיחה עם הסוכן.</p>
      </aside>
    </div>
  );
};
