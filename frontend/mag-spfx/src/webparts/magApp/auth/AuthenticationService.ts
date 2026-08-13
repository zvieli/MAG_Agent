import {
  AccountInfo,
  AuthenticationResult,
  InteractionRequiredAuthError,
  IPublicClientApplication,
  PublicClientApplication
} from '@azure/msal-browser';
import { DebugLogger } from '../domain/debugLogger';
import {
  AuthRuntimeConfiguration,
  loadAuthConfiguration
} from './authConfig';

export type AuthenticationStatus = 'initializing' | 'ready' | 'interaction-required' | 'error';

export interface AuthenticationState {
  status: AuthenticationStatus;
  reason?: string;
  message?: string;
}

export type AuthenticationStateListener = (state: AuthenticationState) => void;

export interface IAuthenticationService {
  readonly state: AuthenticationState;
  initialize(): Promise<void>;
  signIn(): Promise<void>;
  getAccessToken(): Promise<string>;
  subscribe(listener: AuthenticationStateListener): () => void;
}

export const ENTRA_AUTHENTICATION_FAILED = 'ENTRA_AUTHENTICATION_FAILED';

export interface EntraErrorDetails {
  reason?: string;
  errorCode?: string;
  correlationId?: string;
  status?: number;
}

interface SanitizedMsalError extends EntraErrorDetails {
  msalCode?: string;
}

const safeGuid = (value: unknown): string | undefined =>
  typeof value === 'string' && /^[0-9a-f-]{36}$/i.test(value) ? value : undefined;

export const extractEntraErrorDetails = (error: unknown): SanitizedMsalError => {
  const record = error && typeof error === 'object' ? error as Record<string, unknown> : undefined;
  const values = [
    typeof error === 'string' ? error : undefined,
    record?.errorCode,
    record?.code,
    record?.subError,
    record?.message
  ].filter((value): value is string => typeof value === 'string');
  const combined = values.join(' ');
  const errorCode = combined.match(/\bAADSTS\d+\b/i)?.[0]?.toUpperCase();
  const correlationId =
    safeGuid(record?.correlationId) ||
    safeGuid(record?.correlation_id) ||
    safeGuid(combined.match(/correlation(?:_|\s|-)?id\s*[:=]\s*([0-9a-f-]{36})/i)?.[1]);
  const msalCode = typeof record?.errorCode === 'string'
    ? record.errorCode.toLowerCase()
    : typeof record?.code === 'string'
      ? record.code.toLowerCase()
      : undefined;

  return { errorCode, correlationId, msalCode };
};

const authenticationMessage = (reason?: string): string => {
  switch (reason) {
    case 'interaction-required':
      return 'נדרשת התחברות ל־Entra ID כדי להמשיך למערכת.';
    case 'popup-cancelled':
      return 'ההתחברות בוטלה. אפשר לנסות שוב.';
    case 'popup-blocked':
      return 'הדפדפן חסם את חלון ההתחברות. יש לאפשר חלונות קופצים ולנסות שוב.';
    case 'invalid-configuration':
      return 'תצורת Entra ID אינה תקינה. יש לפנות למנהל המערכת.';
    case 'empty-token':
      return 'Entra ID לא החזיר הרשאת גישה תקינה. יש לנסות להתחבר מחדש.';
    case 'silent-acquisition-failed':
      return 'לא ניתן היה להשלים אימות שקט מול Entra ID. יש לנסות להתחבר מחדש.';
    case 'popup-failed':
      return 'חלון ההתחברות לא הושלם. אפשר לנסות שוב.';
    default:
      return 'לא ניתן להשלים את האימות מול Entra ID.';
  }
};

export class EntraAuthenticationError extends Error {
  public readonly code = ENTRA_AUTHENTICATION_FAILED;
  public readonly reason?: string;
  public readonly errorCode?: string;
  public readonly correlationId?: string;
  public readonly status?: number;

  public constructor(details: EntraErrorDetails = {}) {
    super(authenticationMessage(details.reason));
    this.name = 'EntraAuthenticationError';
    Object.setPrototypeOf(this, EntraAuthenticationError.prototype);
    this.reason = details.reason;
    this.errorCode = details.errorCode;
    this.correlationId = details.correlationId;
    this.status = details.status;
  }
}

export const isEntraAuthenticationError = (error: unknown): error is EntraAuthenticationError => {
  if (!error || typeof error !== 'object') return false;
  const record = error as Record<string, unknown>;
  return record.code === ENTRA_AUTHENTICATION_FAILED || record.name === 'EntraAuthenticationError';
};

const isInteractionRequired = (error: unknown, details: SanitizedMsalError): boolean => {
  if (error instanceof InteractionRequiredAuthError) return true;
  return [
    'interaction_required',
    'login_required',
    'consent_required',
    'no_account_error',
    'monitor_window_timeout'
  ].indexOf(details.msalCode || '') !== -1;
};

const isConfigurationFailure = (details: SanitizedMsalError): boolean =>
  details.errorCode === 'AADSTS50011' || [
    'redirect_uri_mismatch',
    'invalid_client',
    'client_mismatch',
    'authority_uri_insecure',
    'endpoints_resolution_error'
  ].indexOf(details.msalCode || '') !== -1;

const popupFailureReason = (details: SanitizedMsalError): string => {
  if (['user_cancelled', 'user_canceled'].indexOf(details.msalCode || '') !== -1) return 'popup-cancelled';
  if (['popup_window_error', 'empty_window_error', 'block_iframe_reload'].indexOf(details.msalCode || '') !== -1) {
    return 'popup-blocked';
  }
  if (isConfigurationFailure(details)) return 'invalid-configuration';
  return 'popup-failed';
};

export class MsalAuthenticationService implements IAuthenticationService {
  private currentState: AuthenticationState = { status: 'initializing' };
  private readonly listeners = new Set<AuthenticationStateListener>();
  private initializePromise?: Promise<void>;

  public constructor(
    private readonly client: IPublicClientApplication,
    private readonly config: AuthRuntimeConfiguration,
    private readonly loginHint?: string,
    private readonly logger?: DebugLogger
  ) {}

  public get state(): AuthenticationState {
    return this.currentState;
  }

  public subscribe(listener: AuthenticationStateListener): () => void {
    this.listeners.add(listener);
    listener(this.currentState);
    return () => this.listeners.delete(listener);
  }

  private publish(state: AuthenticationState): void {
    this.currentState = state;
    this.listeners.forEach((listener) => listener(state));
  }

  private logFailure(message: string, error: unknown, reason: string): EntraAuthenticationError {
    const details = extractEntraErrorDetails(error);
    this.logger?.error(message, {
      reason,
      errorCode: details.errorCode,
      correlationId: details.correlationId,
      operation: 'entra-authentication'
    });
    return new EntraAuthenticationError({
      reason,
      errorCode: details.errorCode,
      correlationId: details.correlationId
    });
  }

  private acceptResult(result: AuthenticationResult): void {
    const account = result.account || this.accountFromTenant();
    if (!account || account.tenantId?.toLowerCase() !== this.config.tenantId.toLowerCase()) {
      throw new EntraAuthenticationError({ reason: 'invalid-configuration' });
    }
    this.client.setActiveAccount(account);
    this.publish({ status: 'ready' });
  }

  private accountFromTenant(): AccountInfo | undefined {
    return this.client.getAllAccounts().find(
      (account) => account.tenantId?.toLowerCase() === this.config.tenantId.toLowerCase()
    );
  }

  public initialize(): Promise<void> {
    if (!this.initializePromise) {
      this.initializePromise = this.initializeInternal();
    }
    return this.initializePromise;
  }

  private async initializeInternal(): Promise<void> {
    this.publish({ status: 'initializing' });
    try {
      await this.client.initialize();
      await this.client.handleRedirectPromise();
      const cachedAccount = this.accountFromTenant();
      if (cachedAccount) {
        this.client.setActiveAccount(cachedAccount);
        this.publish({ status: 'ready' });
        return;
      }

      if (!this.loginHint?.trim()) {
        this.publish({
          status: 'interaction-required',
          reason: 'interaction-required',
          message: authenticationMessage('interaction-required')
        });
        return;
      }

      const result = await this.client.ssoSilent({
        authority: this.config.authority,
        scopes: [this.config.backendScope],
        redirectUri: this.config.redirectUri,
        loginHint: this.loginHint.trim()
      });
      this.acceptResult(result);
    } catch (error) {
      const details = extractEntraErrorDetails(error);
      const reason = isEntraAuthenticationError(error)
        ? error.reason || 'silent-acquisition-failed'
        : isInteractionRequired(error, details)
          ? 'interaction-required'
          : isConfigurationFailure(details)
            ? 'invalid-configuration'
            : 'silent-acquisition-failed';
      const status: AuthenticationStatus = reason === 'invalid-configuration' ? 'error' : 'interaction-required';
      this.logFailure('Failed to initialize Entra ID authentication', error, reason);
      this.publish({ status, reason, message: authenticationMessage(reason) });
    }
  }

  public async signIn(): Promise<void> {
    try {
      const popupPromise = this.client.loginPopup({
        authority: this.config.authority,
        scopes: [this.config.backendScope],
        redirectUri: this.config.redirectUri,
        loginHint: this.loginHint?.trim() || undefined,
        prompt: 'select_account'
      });
      this.publish({ status: 'initializing' });
      this.acceptResult(await popupPromise);
    } catch (error) {
      const details = extractEntraErrorDetails(error);
      const reason = isEntraAuthenticationError(error)
        ? error.reason || 'popup-failed'
        : popupFailureReason(details);
      const status: AuthenticationStatus = reason === 'invalid-configuration' ? 'error' : 'interaction-required';
      const authenticationError = this.logFailure('Interactive Entra ID sign-in failed', error, reason);
      this.publish({ status, reason, message: authenticationError.message });
      throw authenticationError;
    }
  }

  public async getAccessToken(): Promise<string> {
    await this.initialize();
    const account = this.client.getActiveAccount() || this.accountFromTenant();
    if (!account) {
      const error = new EntraAuthenticationError({ reason: 'interaction-required' });
      this.publish({ status: 'interaction-required', reason: error.reason, message: error.message });
      throw error;
    }

    try {
      const result = await this.client.acquireTokenSilent({
        account,
        authority: this.config.authority,
        scopes: [this.config.backendScope],
        redirectUri: this.config.redirectUri
      });
      const token = result.accessToken?.trim();
      if (!token) throw new EntraAuthenticationError({ reason: 'empty-token' });
      this.publish({ status: 'ready' });
      return token;
    } catch (error) {
      if (isEntraAuthenticationError(error)) {
        this.publish({ status: 'interaction-required', reason: error.reason, message: error.message });
        throw error;
      }
      const details = extractEntraErrorDetails(error);
      const reason = isInteractionRequired(error, details)
        ? 'interaction-required'
        : isConfigurationFailure(details)
          ? 'invalid-configuration'
          : 'silent-acquisition-failed';
      const authenticationError = this.logFailure('Silent Entra ID token acquisition failed', error, reason);
      this.publish({
        status: reason === 'invalid-configuration' ? 'error' : 'interaction-required',
        reason,
        message: authenticationError.message
      });
      throw authenticationError;
    }
  }
}

class InvalidConfigurationAuthenticationService implements IAuthenticationService {
  public readonly state: AuthenticationState = {
    status: 'error',
    reason: 'invalid-configuration',
    message: authenticationMessage('invalid-configuration')
  };

  public initialize(): Promise<void> {
    return Promise.resolve();
  }

  public signIn(): Promise<void> {
    return Promise.reject(new EntraAuthenticationError({ reason: 'invalid-configuration' }));
  }

  public getAccessToken(): Promise<string> {
    return Promise.reject(new EntraAuthenticationError({ reason: 'invalid-configuration' }));
  }

  public subscribe(listener: AuthenticationStateListener): () => void {
    listener(this.state);
    return () => undefined;
  }
}

let sharedAuthenticationService: IAuthenticationService | undefined;
type PublicClientFactory = (config: AuthRuntimeConfiguration) => IPublicClientApplication;
const defaultPublicClientFactory: PublicClientFactory = (config) =>
  new PublicClientApplication(config.msal);

export const getAuthenticationService = (
  loginHint?: string,
  logger?: DebugLogger,
  createPublicClient: PublicClientFactory = defaultPublicClientFactory
): IAuthenticationService => {
  if (!sharedAuthenticationService) {
    const config = loadAuthConfiguration();
    sharedAuthenticationService = config
      ? new MsalAuthenticationService(
        createPublicClient(config),
        config,
        loginHint,
        logger
      )
      : new InvalidConfigurationAuthenticationService();
  }
  return sharedAuthenticationService;
};
