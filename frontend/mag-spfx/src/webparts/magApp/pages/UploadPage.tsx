import { DefaultButton, PrimaryButton, ProgressIndicator, SearchBox } from '@fluentui/react';
import * as React from 'react';
import styles from '../components/MagApp.module.scss';
import { StatusBadge } from '../components/StatusBadge';
import { isSuccessfulPipeline, isTerminalStatus } from '../domain/guards';
import { errorText, pipelinePhaseLabel, runtimeStatusLabel, technicalValue } from '../domain/localization';
import { PipelineOperation, SPFile } from '../domain/types';
import { IPipelineService, ISharePointFileService } from '../services/IAppServices';
import { normalizeFolderServerRelativeUrl } from '../services/SharePointFileService';

const MAX_PDF_SIZE_BYTES = 50 * 1024 * 1024;

export interface UploadPageProps {
  pipelineService: IPipelineService;
  sharePointFileService: ISharePointFileService;
  sharePointFolderServerRelativeUrl: string;
  pollIntervalMs: number;
  currentPipeline?: PipelineOperation;
  onPipelineChange: (pipeline: PipelineOperation) => void;
  onComplete: () => void;
}

export const UploadPage: React.FC<UploadPageProps> = ({
  pipelineService,
  sharePointFileService,
  sharePointFolderServerRelativeUrl,
  pollIntervalMs,
  currentPipeline,
  onPipelineChange,
  onComplete
}) => {
  const [sharePointFiles, setSharePointFiles] = React.useState<SPFile[]>([]);
  const [selectedFileUrl, setSelectedFileUrl] = React.useState<string>();
  const [searchQuery, setSearchQuery] = React.useState('');
  const [operations, setOperations] = React.useState<PipelineOperation[]>(currentPipeline ? [currentPipeline] : []);
  const [error, setError] = React.useState<string>();
  const [isLoadingFiles, setIsLoadingFiles] = React.useState(false);
  const [isStarting, setIsStarting] = React.useState(false);
  const [isDownloading, setIsDownloading] = React.useState(false);
  const completedNavigationRef = React.useRef(false);

  const selectedFile = sharePointFiles.find((file) => file.serverRelativeUrl === selectedFileUrl);

  const loadSharePointFiles = React.useCallback(async (): Promise<void> => {
    setIsLoadingFiles(true);
    setError(undefined);
    try {
      const files = await sharePointFileService.listFiles(sharePointFolderServerRelativeUrl);
      setSharePointFiles(files);
      setSelectedFileUrl((current) =>
        current && files.some((file) => file.serverRelativeUrl === current) ? current : undefined
      );
    } catch (loadError) {
      setSharePointFiles([]);
      setSelectedFileUrl(undefined);
      setError(
        loadError instanceof Error
          ? errorText(loadError.message)
          : 'לא ניתן לטעון את קובצי ה-PDF מ-SharePoint.'
      );
    } finally {
      setIsLoadingFiles(false);
    }
  }, [sharePointFileService, sharePointFolderServerRelativeUrl]);

  React.useEffect(() => {
    void loadSharePointFiles();
  }, [loadSharePointFiles]);

  React.useEffect(() => {
    if (currentPipeline && operations.length === 0) {
      setOperations([currentPipeline]);
    }
  }, [currentPipeline, operations.length]);

  React.useEffect(() => {
    const running = operations.filter((operation) => !isTerminalStatus(operation.status) && operation.statusUrl);
    if (!running.length) return;

    const timer = window.setInterval(async () => {
      try {
        const updatedRunning = await Promise.all(running.map((operation) => pipelineService.getStatus(operation)));
        const updatedById = new Map(updatedRunning.map((operation) => [operation.instanceId, operation]));
        const nextOperations = operations.map((operation) => updatedById.get(operation.instanceId) || operation);
        const completed = nextOperations.find(
          (operation) =>
            operation.status === 'Completed' &&
            (operation.output?.status === 'completed' || operation.output?.status === 'completed_partial')
        );
        const failed = nextOperations.find((operation) => operation.status === 'Failed');
        const representative = completed || nextOperations[0];

        setOperations(nextOperations);
        if (failed) {
          setError(failed.error ? `העיבוד נכשל: ${failed.error}` : 'עיבוד המסמך נכשל.');
        }
        if (representative) onPipelineChange(representative);

        if (completed && !completedNavigationRef.current) {
          completedNavigationRef.current = true;
          window.clearInterval(timer);
          onComplete();
        }
      } catch (pollError) {
        setError(
          pollError instanceof Error ? errorText(pollError.message) : 'אירעה שגיאה בבדיקת מצב העיבוד.'
        );
        window.clearInterval(timer);
      }
    }, pollIntervalMs);

    return () => window.clearInterval(timer);
  }, [operations, onComplete, onPipelineChange, pipelineService, pollIntervalMs]);

  const startPipeline = async (): Promise<void> => {
    if (!selectedFile) {
      setError('יש לבחור קובץ PDF מ-SharePoint.');
      return;
    }
    if (selectedFile.size > MAX_PDF_SIZE_BYTES) {
      setError('הקובץ גדול מ-50 MB ולא ניתן לעבד אותו.');
      return;
    }

    setIsStarting(true);
    setIsDownloading(true);
    setError(undefined);
    completedNavigationRef.current = false;

    try {
      const file = await sharePointFileService.downloadFile(selectedFile.serverRelativeUrl, selectedFile.name);
      setIsDownloading(false);
      const operation = await pipelineService.uploadAndStart(file, (progressOperation) => {
        setOperations([progressOperation]);
        onPipelineChange(progressOperation);
      });
      setOperations([operation]);
      onPipelineChange(operation);

      if (
        operation.status === 'Completed' &&
        (operation.output?.status === 'completed' || operation.output?.status === 'completed_partial')
      ) {
        completedNavigationRef.current = true;
        onComplete();
      }
    } catch (startError) {
      setError(startError instanceof Error ? errorText(startError.message) : 'אירעה שגיאה בהתחלת העיבוד.');
    } finally {
      setIsDownloading(false);
      setIsStarting(false);
    }
  };

  const active = operations[0] || currentPipeline;
  const clientStageLabel = (operation: PipelineOperation | undefined): string => {
    switch (operation?.clientStage) {
      case 'preflight':
        return 'בודק אם התיק כבר קיים';
      case 'uploading':
        return 'מעלה קובץ לעיבוד';
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
  const phase = isDownloading ? 'מוריד מ-SharePoint' : clientStageLabel(active);
  const total = active?.customStatus?.total_chunks || 0;
  const completed = active?.customStatus?.completed_chunks || 0;
  const progress =
    active?.uploadProgress !== undefined ? active.uploadProgress : total > 0 ? completed / total : undefined;
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
  const isContentBlocked = active?.status === 'Completed' && active.output?.status === 'content_blocked';
  const isAnyRunning = operations.some((operation) => !isTerminalStatus(operation.status));
  const formatSize = (size: number): string => `${Math.max(1, Math.round(size / 1024)).toLocaleString('he-IL')} KB`;
  const formatModified = (value: string | undefined): string =>
    value ? new Date(value).toLocaleString('he-IL') : 'לא ידוע';
  const elapsedLabel = (elapsedMs: number | undefined): string => {
    if (!elapsedMs) return '0 שניות';
    const seconds = Math.floor(elapsedMs / 1000);
    if (seconds < 60) return `${seconds.toLocaleString('he-IL')} שניות`;
    const minutes = Math.floor(seconds / 60);
    return `${minutes.toLocaleString('he-IL')} דק׳ ${(seconds % 60).toLocaleString('he-IL')} שנ׳`;
  };

  return (
    <div className={styles.pageGrid}>
      <div className={styles.infoBanner}>
        💡 <strong>אופן העיבוד:</strong> בחר קובץ PDF מתיקיית SharePoint ולחץ על "התחל עיבוד". הקובץ יורד באופן מאובטח בהרשאות המשתמש ולאחר מכן עובר בדיקת כפילויות ותהליך עיבוד חכם.
      </div>

      <section className={styles.panel}>
        <div className={styles.panelHeader}>
          <h2>בחירת מסמך מ-SharePoint</h2>
          <StatusBadge
            label={isComplete ? 'מוכן' : isContentBlocked ? 'לא נוצר גרף' : phase}
            tone={isComplete ? 'success' : isContentBlocked ? 'neutral' : active ? 'running' : 'neutral'}
          />
        </div>

        <div className={styles.sharePointHeader}>
          <div>
            <strong>קובצי PDF זמינים</strong>
            <small className={styles.sharePointPath}>{normalizeFolderServerRelativeUrl(sharePointFolderServerRelativeUrl)}</small>
          </div>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <SearchBox
              placeholder="סינון קבצים..."
              value={searchQuery}
              onChange={(_, value) => setSearchQuery(value || '')}
              styles={{ root: { width: 200 } }}
            />
            <DefaultButton
              text={isLoadingFiles ? 'טוען...' : 'רענן'}
              iconProps={{ iconName: 'Refresh' }}
              onClick={() => void loadSharePointFiles()}
              disabled={isLoadingFiles || isStarting || isAnyRunning}
            />
          </div>
        </div>

        <div className={styles.fileList} role="radiogroup" aria-label="קובצי PDF ב-SharePoint">
          {isLoadingFiles && <div className={styles.sharePointMessage}>טוען קבצים מ-SharePoint...</div>}
          {!isLoadingFiles && sharePointFiles.length === 0 && (
            <div className={styles.sharePointMessage}>לא נמצאו קובצי PDF בתיקייה.</div>
          )}
          {!isLoadingFiles &&
            sharePointFiles
              .filter((file) => !searchQuery || file.name.toLowerCase().includes(searchQuery.toLowerCase()))
              .map((file) => {
                const selected = file.serverRelativeUrl === selectedFileUrl;
                return (
                  <label
                    key={file.serverRelativeUrl}
                    className={`${styles.sharePointFileRow} ${selected ? styles.selectedSharePointFile : ''}`}
                  >
                    <input
                      type="radio"
                      name="sharePointPdf"
                      value={file.serverRelativeUrl}
                      checked={selected}
                      onChange={() => {
                        setSelectedFileUrl(file.serverRelativeUrl);
                        setError(undefined);
                      }}
                      disabled={isStarting || isAnyRunning}
                    />
                    <span className={styles.sharePointFileDetails}>
                      <strong className={styles.ltrValue}>{file.name}</strong>
                      <small>
                        {formatSize(file.size)} · עודכן {formatModified(file.lastModified)}
                      </small>
                    </span>
                  </label>
                );
              })}
        </div>

        <div className={styles.actionRow}>
          <PrimaryButton
            text={isDownloading ? 'מוריד מ-SharePoint...' : isStarting ? 'מתחיל עיבוד...' : 'התחל עיבוד'}
            iconProps={{ iconName: 'Play' }}
            onClick={() => void startPipeline()}
            disabled={isLoadingFiles || isStarting || isAnyRunning || !selectedFile}
          />
          <DefaultButton
            text="נקה בחירה"
            iconProps={{ iconName: 'Clear' }}
            onClick={() => setSelectedFileUrl(undefined)}
            disabled={isStarting || isAnyRunning || !selectedFile}
          />
        </div>

        {operations.length > 0 && (
          <div className={styles.pipelineBox}>
            <div className={styles.stepperBox}>
              <div className={`${styles.stepperItem} ${isDownloading ? styles.stepperActive : styles.stepperDone}`}>
                <span>1. הורדה מ-SharePoint</span>
              </div>
              <div className={`${styles.stepperItem} ${active?.clientStage === 'preflight' ? styles.stepperActive : isComplete || active?.clientStage !== 'starting' ? styles.stepperDone : ''}`}>
                <span>2. בדיקת כפילות</span>
              </div>
              <div className={`${styles.stepperItem} ${active?.clientStage === 'processing' || active?.clientStage === 'polling' ? styles.stepperActive : isComplete ? styles.stepperDone : ''}`}>
                <span>3. יצירת גרף ידע</span>
              </div>
              <div className={`${styles.stepperItem} ${isComplete ? styles.stepperDone : ''}`}>
                <span>4. תיק מוכן לפעילות</span>
              </div>
            </div>

            {operations.map((operation) => (
              <div key={operation.instanceId} className={styles.operationCard} style={{ marginTop: '12px' }}>
                <div className={styles.metaRow}>
                  <span className={styles.ltrValue}>{operation.fileName}</span>
                  <strong>
                    {operation.output?.reused_existing_case ? 'תיק קיים' : runtimeStatusLabel(operation.status)}
                  </strong>
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
                    <strong>נמצא גרף קיים לתיק</strong>
                  </div>
                )}
              </div>
            ))}
            <ProgressIndicator label={phase} percentComplete={progress} styles={{ root: { marginTop: '8px' } }} />
          </div>
        )}
        {error && <div className={styles.errorBox}>{error}</div>}
      </section>
    </div>
  );
};
