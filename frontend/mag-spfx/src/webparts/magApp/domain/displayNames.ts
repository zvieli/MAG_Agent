const technicalPrefixes = /^(canonical|suspect|offense|org|actor|event|location)_/i;

export const looksTechnicalId = (value: string | undefined): boolean => {
  if (!value) return false;
  return value.indexOf('_') >= 0 || technicalPrefixes.test(value);
};

export const readableIdentifier = (value: string | undefined): string => {
  if (!value) return '';
  return value
    .replace(technicalPrefixes, '')
    .replace(/_/g, ' ')
    .trim();
};

export const displayName = (value: string | undefined, fallback?: string): string => {
  if (value && !looksTechnicalId(value)) return value;
  if (fallback && !looksTechnicalId(fallback)) return fallback;
  return readableIdentifier(value || fallback) || value || fallback || '';
};
