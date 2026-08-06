import { AadTokenProviderFactory } from '@microsoft/sp-http';
import { DebugLogger } from '../domain/debugLogger';

export class TokenService {
  private tokenProviderFactory?: AadTokenProviderFactory;
  private resourceUri?: string;
  private logger?: DebugLogger;

  constructor(
    tokenProviderFactory?: AadTokenProviderFactory,
    resourceUri?: string,
    logger?: DebugLogger
  ) {
    this.tokenProviderFactory = tokenProviderFactory;
    this.resourceUri = resourceUri?.trim();
    this.logger = logger;
  }

  private isValidResourceUri(uri?: string): boolean {
    if (!uri || !uri.trim()) {
      return false;
    }
    const clean = uri.trim();
    if (clean.includes('<') || clean.includes('>') || clean === 'api://' || clean.includes('backend-app-id')) {
      return false;
    }
    return true;
  }

  public async getAccessToken(): Promise<string | undefined> {
    if (!this.tokenProviderFactory) {
      return undefined;
    }

    if (!this.isValidResourceUri(this.resourceUri)) {
      this.logger?.info('Entra ID backendAppIdUri is unconfigured or placeholder; skipping token acquisition');
      return undefined;
    }

    try {
      const provider = await this.tokenProviderFactory.getTokenProvider();
      const token = await provider.getToken(this.resourceUri!);
      return token;
    } catch (error) {
      this.logger?.error('Failed to acquire Entra ID access token', { error, resourceUri: this.resourceUri });
      return undefined;
    }
  }
}
