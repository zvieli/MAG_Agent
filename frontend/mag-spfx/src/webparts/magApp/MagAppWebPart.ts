import * as React from 'react';
import * as ReactDom from 'react-dom';
import { Version } from '@microsoft/sp-core-library';
import {
  IPropertyPaneConfiguration,
  PropertyPaneSlider,
  PropertyPaneTextField,
  PropertyPaneToggle
} from '@microsoft/sp-property-pane';
import { BaseClientSideWebPart } from '@microsoft/sp-webpart-base';

import * as strings from 'MagAppWebPartStrings';
import { MagApp } from './components/MagApp';
import { MagAppConfig } from './domain/types';
import { findMagAppBundleAsset } from './domain/runtimeFingerprint';
import { DEFAULT_BACKEND_CLIENT_ID } from './services/TokenService';

export interface IMagAppWebPartProps {
  apiBaseUrl: string;
  useMockData: boolean;
  pollIntervalMs: number;
  enableDebugLogs: boolean;
  sharePointSiteUrl: string;
  sharePointFolderServerRelativeUrl: string;
  backendClientId?: string;
  /** @deprecated Read temporarily so existing SharePoint pages keep working. */
  backendAppIdUri?: string;
}

export default class MagAppWebPart extends BaseClientSideWebPart<IMagAppWebPartProps> {
  protected async onInit(): Promise<void> {
    await super.onInit();

    const resourceUrls = window.performance?.getEntriesByType('resource')
      .map((entry) => entry.name) || [];
    const bundleAsset = findMagAppBundleAsset(resourceUrls);

    console.info('[MAG] SPFx runtime fingerprint', {
      componentId: this.manifest.id.toString(),
      componentVersion: this.manifest.version,
      bundleAsset: bundleAsset || 'unavailable'
    });
  }

  public render(): void {
    let isLocalDocker = false;
    let envApiTarget: string | undefined = undefined;
    let envUseMockData: string | undefined = undefined;
    let envPollIntervalMs: string | undefined = undefined;

    try {
      isLocalDocker = process.env.VITE_USE_LOCAL_DOCKER === 'true';
    } catch (e) { /* process is not defined */ }

    try {
      envApiTarget = process.env.VITE_AZURE_API_TARGET || process.env.VITE_API_TARGET;
    } catch (e) { /* process is not defined */ }

    try {
      envUseMockData = process.env.VITE_USE_MOCK_DATA;
    } catch (e) { /* process is not defined */ }

    try {
      envPollIntervalMs = process.env.VITE_POLL_INTERVAL_MS;
    } catch (e) { /* process is not defined */ }

    let apiBaseUrl = this.properties.apiBaseUrl || '';
    if (!apiBaseUrl) {
      if (isLocalDocker) {
        apiBaseUrl = process.env.VITE_API_TARGET || 'http://localhost:7071';
      } else if (envApiTarget) {
        apiBaseUrl = envApiTarget;
      } else {
        apiBaseUrl = 'https://homkeyhakera-lagend-e7cafje6hyhfdyfk.westeurope-01.azurewebsites.net';
      }
    }

    let useMockData = this.properties.useMockData === true;
    let pollIntervalMs = this.properties.pollIntervalMs || 3000;

    if (envUseMockData !== undefined) {
      useMockData = envUseMockData === 'true';
    }

    if (envPollIntervalMs !== undefined) {
      const val = parseInt(envPollIntervalMs, 10);
      if (!isNaN(val)) {
        pollIntervalMs = val;
      }
    }

    const currentWebUrl = this.context.pageContext?.web?.absoluteUrl || 'https://tikshuv.sharepoint.com/sites/MAG_Agent';
    const currentWebRelativeUrl = this.context.pageContext?.web?.serverRelativeUrl || '/sites/MAG_Agent';
    const cleanWebRelativeUrl = currentWebRelativeUrl.endsWith('/') ? currentWebRelativeUrl.slice(0, -1) : currentWebRelativeUrl;

    const config: MagAppConfig = {
      apiBaseUrl,
      useMockData,
      pollIntervalMs,
      enableDebugLogs: this.properties.enableDebugLogs === true,
      sharePointSiteUrl: this.properties.sharePointSiteUrl || currentWebUrl,
      sharePointFolderServerRelativeUrl:
        this.properties.sharePointFolderServerRelativeUrl || `${cleanWebRelativeUrl}/Shared Documents`,
      backendClientId:
        this.properties.backendClientId?.trim() ||
        this.properties.backendAppIdUri?.trim() ||
        DEFAULT_BACKEND_CLIENT_ID,
      aadTokenProviderFactory: this.context.aadTokenProviderFactory
    };

    const element = React.createElement(MagApp, { config, spHttpClient: this.context.spHttpClient });
    ReactDom.render(element, this.domElement);
  }

  protected onDispose(): void {
    ReactDom.unmountComponentAtNode(this.domElement);
  }

  protected get dataVersion(): Version {
    return Version.parse('1.0');
  }

  protected getPropertyPaneConfiguration(): IPropertyPaneConfiguration {
    return {
      pages: [
        {
          header: {
            description: strings.PropertyPaneDescription
          },
          groups: [
            {
              groupName: 'חיבור למערכת',
              groupFields: [
                PropertyPaneTextField('apiBaseUrl', {
                  label: strings.ApiBaseUrlFieldLabel,
                  placeholder: 'https://<function-app>.azurewebsites.net'
                }),
                PropertyPaneTextField('backendClientId', {
                  label: 'Backend App Client ID',
                  placeholder: DEFAULT_BACKEND_CLIENT_ID
                }),
                PropertyPaneToggle('useMockData', {
                  label: strings.UseMockDataFieldLabel,
                  onText: 'נתוני דמה',
                  offText: 'ממשק חי'
                }),
                PropertyPaneSlider('pollIntervalMs', {
                  label: strings.PollIntervalFieldLabel,
                  min: 1000,
                  max: 15000,
                  step: 500
                }),
                PropertyPaneTextField('sharePointSiteUrl', {
                  label: 'כתובת אתר SharePoint',
                  placeholder: 'https://tenant.sharepoint.com/sites/site'
                }),
                PropertyPaneTextField('sharePointFolderServerRelativeUrl', {
                  label: 'נתיב תיקיית PDF ב-SharePoint',
                  placeholder: '/sites/site/Shared Documents/data'
                }),
                PropertyPaneToggle('enableDebugLogs', {
                  label: strings.EnableDebugLogsFieldLabel,
                  onText: 'מופעל',
                  offText: 'כבוי'
                })
              ]
            }
          ]
        }
      ]
    };
  }
}
