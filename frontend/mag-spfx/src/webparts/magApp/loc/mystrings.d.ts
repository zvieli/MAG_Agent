declare interface IMagAppWebPartStrings {
  PropertyPaneDescription: string;
  ApiBaseUrlFieldLabel: string;
  UseMockDataFieldLabel: string;
  PollIntervalFieldLabel: string;
  EnableDebugLogsFieldLabel: string;
}

declare module 'MagAppWebPartStrings' {
  const strings: IMagAppWebPartStrings;
  export = strings;
}
