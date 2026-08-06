import { SPHttpClient, SPHttpClientResponse } from '@microsoft/sp-http';
import { SPFile } from '../domain/types';
import { ISharePointFileService } from './IAppServices';

interface IFileItemResponse {
  Name: string;
  ServerRelativeUrl: string;
  TimeLastModified?: string;
  Length?: string;
}

export const normalizeFolderServerRelativeUrl = (rawUrl: string): string => {
  if (!rawUrl) return '';

  let path = rawUrl.trim();

  if (/^https?:\/\//i.test(path)) {
    try {
      path = new URL(path).pathname;
    } catch {
      // Keep as is if URL parsing fails
    }
  }

  try {
    path = decodeURIComponent(path);
  } catch {
    // Keep as is
  }

  // Strip SharePoint list view suffixes (/Forms/AllItems.aspx, /Forms/AllItems, /Forms/Flat.aspx, etc.)
  path = path.replace(/\/Forms\/[^\/]+$/i, '');

  if (!path.startsWith('/')) {
    path = '/' + path;
  }
  path = path.replace(/\/+$/, '');

  return path;
};

const encodeODataLiteral = (value: string): string =>
  encodeURIComponent(value.replace(/'/g, "''")).replace(/'/g, '%27');

export class SharePointFileService implements ISharePointFileService {
  private readonly spHttpClient: SPHttpClient;
  private readonly webAbsoluteUrl: string;

  public constructor(spHttpClient: SPHttpClient, webAbsoluteUrl: string) {
    this.spHttpClient = spHttpClient;
    this.webAbsoluteUrl = webAbsoluteUrl.replace(/\/+$/, '');
  }

  private restUrl(pathAndQuery: string): string {
    return `${this.webAbsoluteUrl}/_api/${pathAndQuery}`;
  }

  private async readJson<T>(response: SPHttpClientResponse, contextPath?: string): Promise<T> {
    if (!response.ok) {
      const body = await response.text().catch(() => '');
      if (response.status === 403) {
        throw new Error(
          `אין הרשאת גישה לתיקיית SharePoint או שהנתיב אינו תקין (${contextPath || ''}). נסו להזין נתיב תיקייה ראשי ללא סיומת /Forms/AllItems.`
        );
      }
      throw new Error(`בקשת SharePoint נכשלה (${response.status}): ${body || response.statusText}`);
    }
    return response.json() as Promise<T>;
  }

  public async listFiles(folderServerRelativeUrl: string): Promise<SPFile[]> {
    const cleanFolder = normalizeFolderServerRelativeUrl(folderServerRelativeUrl);
    const encodedFolder = encodeODataLiteral(cleanFolder);
    const url = this.restUrl(
      `web/GetFolderByServerRelativeUrl('${encodedFolder}')/Files?$select=Name,ServerRelativeUrl,TimeLastModified,Length`
    );
    const response = await this.spHttpClient.get(url, SPHttpClient.configurations.v1, {
      headers: { Accept: 'application/json;odata=nometadata' }
    });
    const payload = await this.readJson<{ value: IFileItemResponse[] }>(response, cleanFolder);

    return (payload.value || [])
      .filter((file) => file.Name.toLowerCase().endsWith('.pdf'))
      .map((file) => ({
        name: file.Name,
        serverRelativeUrl: file.ServerRelativeUrl,
        absoluteUrl: this.toAbsoluteUrl(file.ServerRelativeUrl),
        lastModified: file.TimeLastModified,
        size: Number(file.Length || 0)
      }))
      .sort((left, right) => left.name.localeCompare(right.name, 'he'));
  }

  public async downloadFile(fileServerRelativeUrl: string, fileName: string): Promise<File> {
    const encodedFile = encodeODataLiteral(fileServerRelativeUrl);
    const url = this.restUrl(`web/GetFileByServerRelativeUrl('${encodedFile}')/$value`);
    const response = await this.spHttpClient.get(url, SPHttpClient.configurations.v1);
    if (!response.ok) {
      const body = await response.text().catch(() => '');
      throw new Error(`הורדת הקובץ מ-SharePoint נכשלה (${response.status}): ${body || response.statusText}`);
    }
    const blob = await response.blob();
    return new File([blob], fileName, { type: blob.type || 'application/pdf' });
  }

  private toAbsoluteUrl(serverRelativeUrl: string): string {
    try {
      return `${new URL(this.webAbsoluteUrl).origin}${serverRelativeUrl}`;
    } catch (error) {
      return serverRelativeUrl;
    }
  }
}
