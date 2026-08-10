const bundleAssetPattern = /^mag-app-web-part(?:_[0-9a-f]+)?\.js$/i;

export const findMagAppBundleAsset = (resourceUrls: string[]): string | undefined => {
  const matches = resourceUrls
    .map((resourceUrl) => {
      try {
        return new URL(resourceUrl).pathname.split('/').pop();
      } catch {
        return undefined;
      }
    })
    .filter((name): name is string => Boolean(name && bundleAssetPattern.test(name)));

  return matches[matches.length - 1];
};
