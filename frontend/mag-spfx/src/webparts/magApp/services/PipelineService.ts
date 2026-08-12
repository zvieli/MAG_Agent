import { deriveDocId, makeId, normalizeRuntimeStatus } from '../domain/guards';
import { DurableStartResponse, DurableStatus, PipelineOperation, PipelineOutput, PipelinePreflightResult } from '../domain/types';
import { DebugLogger, createDebugLogger } from '../domain/debugLogger';
import { IPipelineService } from './IAppServices';
import { BackendAuthService, requireBackendAuthService } from './BackendAuthService';
import { HttpError, apiUrl, readJson, throwIfBackendAuthenticationRejected } from './http';

export class PipelineService implements IPipelineService {
  private readonly apiBaseUrl: string;
  private readonly backendAuth: BackendAuthService;
  private readonly logger: DebugLogger;

  public constructor(apiBaseUrl: string, backendAuth: BackendAuthService, logger?: DebugLogger) {
    this.apiBaseUrl = apiBaseUrl;
    this.backendAuth = requireBackendAuthService(backendAuth, 'PipelineService');
    this.logger = logger || createDebugLogger(false);
  }

  private pipelineError(status: DurableStatus<PipelineOutput>): string | undefined {
    if (status.customStatus?.last_error) {
      return status.customStatus.last_error;
    }

    if (typeof status.output === 'string') {
      return status.output;
    }

    if (status.output && typeof status.output === 'object' && 'error' in status.output) {
      return String((status.output as Record<string, unknown>).error);
    }

    return undefined;
  }

  private emitProgress(
    operation: PipelineOperation,
    onProgress: ((operation: PipelineOperation) => void) | undefined
  ): void {
    if (onProgress) {
      onProgress({
        ...operation,
        elapsedMs: operation.startedAtMs ? Date.now() - operation.startedAtMs : undefined
      });
    }
  }

  private async runDuplicatePreflight(
    preflightUrl: string,
    traceId: string,
    fileName: string
  ): Promise<PipelinePreflightResult> {
    try {
      const preflight = await this.backendAuth.fetch(
        { operation: 'pipeline-preflight', url: preflightUrl },
        {
          cache: 'no-store',
          headers: {
            'Cache-Control': 'no-cache',
            'x-mag-trace-id': traceId
          }
        }
      ).then((response) =>
        readJson<PipelinePreflightResult>(response, {
          logger: this.logger,
          traceId,
          operation: 'pipeline-preflight',
          method: 'GET',
          url: preflightUrl
        })
      );

      return preflight;
    } catch (error) {
      if (error instanceof HttpError && error.status === 404) {
        const docId = deriveDocId(fileName) || fileName.replace(/\.pdf$/i, '');
        this.logger.info('Pipeline duplicate preflight unavailable; continuing without duplicate shortcut', {
          traceId,
          url: preflightUrl,
          status: error.status,
          docId
        });

        return {
          status: 'new',
          doc_id: docId,
          matched_doc_id: null,
          matched_by: 'none',
          sample_node: null,
          cache_hit: false,
          cache_source: 'none',
          live_query: false,
          data_source_id: null
        };
      }

      throw error;
    }
  }

  private uploadWithProgress(
    url: string,
    file: File,
    extraHeaders: HeadersInit,
    traceId: string,
    startedAtMs: number,
    onProgress: ((operation: PipelineOperation) => void) | undefined
  ): Promise<DurableStartResponse> {
    return this.backendAuth.openAuthenticatedXhr(
      { operation: 'pipeline-start', url },
      'POST',
      extraHeaders
    ).then((xhr) => new Promise((resolve, reject) => {

      xhr.upload.onprogress = (event: ProgressEvent<EventTarget>) => {
        if (!event.lengthComputable) return;
        this.emitProgress(
          {
            fileName: file.name,
            instanceId: traceId,
            statusUrl: '',
            traceId,
            startedAtMs,
            clientStage: 'uploading',
            uploadProgress: Math.max(0, Math.min(event.loaded / event.total, 1)),
            status: 'Pending'
          },
          onProgress
        );
      };

      xhr.onerror = () => reject(new Error('אירעה שגיאת רשת בזמן העלאת הקובץ.'));
      xhr.onload = () => {
        try {
          throwIfBackendAuthenticationRejected(xhr.status, {
            logger: this.logger,
            traceId,
            operation: 'pipeline-start',
            url,
            correlationId:
              xhr.getResponseHeader('x-ms-request-id') ||
              xhr.getResponseHeader('request-id') ||
              xhr.getResponseHeader('x-correlation-id') ||
              undefined
          });
        } catch (error) {
          reject(error);
          return;
        }

        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error(xhr.responseText || `שגיאת HTTP ${xhr.status}`));
          return;
        }

        try {
          resolve(JSON.parse(xhr.responseText) as DurableStartResponse);
        } catch (error) {
          reject(error);
        }
      };

      xhr.send(file);
    }));
  }

  public async uploadAndStart(file: File, onProgress?: (operation: PipelineOperation) => void): Promise<PipelineOperation> {
    const traceId = this.logger.createTraceId('pipeline');
    const startedAtMs = Date.now();
    const preflightUrl = apiUrl(this.apiBaseUrl, `/api/pipeline-preflight?filename=${encodeURIComponent(file.name)}`);
    const url = apiUrl(this.apiBaseUrl, `/api/pipelines?filename=${encodeURIComponent(file.name)}`);

    this.emitProgress(
      {
        fileName: file.name,
        instanceId: traceId,
        statusUrl: '',
        traceId,
        startedAtMs,
        clientStage: 'preflight',
        uploadProgress: 0,
        status: 'Pending'
      },
      onProgress
    );

    this.logger.group('Pipeline duplicate preflight preparing', {
      traceId,
      method: 'GET',
      url: preflightUrl,
      fileName: file.name
    });

    const preflight = await this.runDuplicatePreflight(preflightUrl, traceId, file.name);

    this.logger.group('Pipeline duplicate preflight response', {
      traceId,
      status: preflight.status,
      docId: preflight.doc_id,
      matchedDocId: preflight.matched_doc_id,
      matchedBy: preflight.matched_by,
      cacheHit: preflight.cache_hit,
      cacheSource: preflight.cache_source,
      liveQuery: preflight.live_query,
      dataSourceId: preflight.data_source_id,
      warning: preflight.warning,
      sampleNode: preflight.sample_node
    });

    if (preflight.status === 'duplicate' && preflight.matched_doc_id) {
      const matchedDocId = preflight.matched_doc_id;
      return {
        fileName: file.name,
        instanceId: makeId('duplicate'),
        statusUrl: `duplicate://${encodeURIComponent(matchedDocId)}`,
        traceId,
        startedAtMs,
        clientStage: 'processing',
        uploadProgress: 1,
        elapsedMs: Date.now() - startedAtMs,
        status: 'Completed',
        docId: matchedDocId,
        customStatus: {
          phase: 'duplicate',
          message: 'תיק קיים נמצא במאגר. העיבוד לא הופעל מחדש.',
          doc_id: matchedDocId,
          total_chunks: 0,
          completed_chunks: 0,
          last_error: null
        },
        output: {
          status: 'completed',
          blob_path: `cosmos://${matchedDocId}`,
          final_graph_path: `cosmos://${matchedDocId}`,
          reused_existing_case: true
        }
      };
    }

    if (preflight.status === 'error') {
      throw new Error(preflight.error || 'בדיקת כפילות התיק נכשלה.');
    }

    this.logger.group('Pipeline start preparing', {
      traceId,
      method: 'POST',
      url,
      fileName: file.name,
      fileSize: file.size,
      fileType: file.type || 'unknown'
    });

    this.emitProgress(
      {
        fileName: file.name,
        instanceId: traceId,
        statusUrl: '',
        traceId,
        startedAtMs,
        clientStage: 'uploading',
        uploadProgress: 0,
        status: 'Pending'
      },
      onProgress
    );

    const start = await this.uploadWithProgress(
      url,
      file,
      {
        'Content-Disposition': `attachment; filename="${encodeURIComponent(file.name)}"`,
        'Content-Type': 'application/pdf',
        'x-mag-file-name': encodeURIComponent(file.name),
        'x-mag-trace-id': traceId
      },
      traceId,
      startedAtMs,
      onProgress
    );

    const duplicateStart = start as DurableStartResponse & PipelinePreflightResult;
    if (duplicateStart.status === 'duplicate' && duplicateStart.matched_doc_id) {
      const matchedDocId = duplicateStart.matched_doc_id;
      return {
        fileName: file.name,
        instanceId: makeId('duplicate'),
        statusUrl: `duplicate://${encodeURIComponent(matchedDocId)}`,
        traceId,
        startedAtMs,
        clientStage: 'processing',
        uploadProgress: 1,
        elapsedMs: Date.now() - startedAtMs,
        status: 'Completed',
        docId: matchedDocId,
        customStatus: {
          phase: 'duplicate',
          message: 'תיק קיים נמצא במאגר. העיבוד לא הופעל מחדש.',
          doc_id: matchedDocId,
          total_chunks: 0,
          completed_chunks: 0,
          last_error: null
        },
        output: {
          status: 'completed',
          blob_path: `cosmos://${matchedDocId}`,
          final_graph_path: `cosmos://${matchedDocId}`,
          reused_existing_case: true
        }
      };
    }

    if (!start.id || !start.statusQueryGetUri) {
      throw new Error('תגובת פתיחת התהליך לא כללה פרטי מעקב.');
    }

    this.emitProgress(
      {
        fileName: file.name,
        instanceId: start.id,
        statusUrl: apiUrl(this.apiBaseUrl, `/api/pipelines/${encodeURIComponent(start.id)}`),
        traceId,
        startedAtMs,
        clientStage: 'starting',
        uploadProgress: 1,
        status: 'Pending'
      },
      onProgress
    );

    const statusUrl = apiUrl(this.apiBaseUrl, `/api/pipelines/${encodeURIComponent(start.id)}`);
    this.logger.group('Pipeline start response', {
      traceId,
      instanceId: start.id,
      statusUrl,
      elapsedMs: Date.now() - startedAtMs
    });

    return {
      fileName: file.name,
      instanceId: start.id,
      statusUrl,
      traceId,
      startedAtMs,
      clientStage: 'polling',
      uploadProgress: 1,
      elapsedMs: Date.now() - startedAtMs,
      status: 'Pending',
      docId: undefined
    };
  }

  public async getStatus(operation: PipelineOperation): Promise<PipelineOperation> {
    const traceId = operation.traceId || operation.instanceId;
    const pollStartedAtMs = Date.now();
    this.logger.info('Pipeline status poll preparing', {
      traceId,
      instanceId: operation.instanceId,
      statusUrl: operation.statusUrl
    });

    const status = await this.backendAuth.fetch(
      { operation: 'pipeline-status', url: operation.statusUrl },
      { headers: { 'x-mag-trace-id': traceId } }
    ).then((response) =>
      readJson<DurableStatus<PipelineOutput>>(response, {
        logger: this.logger,
        traceId,
        operation: 'pipeline-status',
        method: 'GET',
        url: operation.statusUrl
      })
    );
    const runtimeStatus = normalizeRuntimeStatus(status.runtimeStatus);

    this.logger.group('Pipeline status poll response', {
      traceId,
      instanceId: operation.instanceId,
      rawRuntimeStatus: status.runtimeStatus,
      runtimeStatus,
      phase: status.customStatus?.phase,
      completedChunks: status.customStatus?.completed_chunks,
      totalChunks: status.customStatus?.total_chunks,
      docId: status.customStatus?.doc_id,
      blobPath: status.output?.blob_path || status.customStatus?.blob_path,
      outputStatus: status.output?.status,
      lastError: status.customStatus?.last_error,
      output: status.output,
      elapsedMs: Date.now() - pollStartedAtMs,
      totalElapsedMs: operation.startedAtMs ? Date.now() - operation.startedAtMs : undefined
    });

    return {
      ...operation,
      status: runtimeStatus,
      clientStage: runtimeStatus === 'Completed' ? 'processing' : 'processing',
      uploadProgress: operation.uploadProgress,
      elapsedMs: operation.startedAtMs ? Date.now() - operation.startedAtMs : operation.elapsedMs,
      docId: deriveDocId(operation.fileName, status) || operation.docId,
      customStatus: status.customStatus,
      output: status.output,
      error: this.pipelineError(status)
    };
  }
}
