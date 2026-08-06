import { AadTokenProviderFactory } from '@microsoft/sp-http';
import { DebugLogger } from '../domain/debugLogger';

export class TokenService {
  private tokenProviderFactory?: AadTokenProviderFactory;
  private resourceUri: string;
  private logger?: DebugLogger;

  constructor(
    tokenProviderFactory?: AadTokenProviderFactory,
    resourceUri: string = 'api://<backend-app-id>',
    logger?: DebugLogger
  ) {
    this.tokenProviderFactory = tokenProviderFactory;
    this.resourceUri = resourceUri;
    this.logger = logger;
  }

  public async getAccessToken(): Promise<string | undefined> {
    if (!this.tokenProviderFactory) {
      return undefined;
    }
    try {
      const provider = await this.tokenProviderFactory.getTokenProvider();
      const token = await provider.getToken(this.resourceUri);
      return token;
    } catch (error) {
      this.logger?.error('Failed to acquire Entra ID access token', { error, resourceUri: this.resourceUri });
      return undefined;
    }
  }
}
