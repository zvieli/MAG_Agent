export interface CacheEntry<T> {
  key: string;
  value: T;
  createdAt: number;
  expiresAt: number;
  version: string;
}

const cacheVersion = 'v3';
const dbName = 'mag-spfx-cache';
const storeName = 'entries';
const namespace = `mag:${cacheVersion}:`;

let dbPromise: Promise<IDBDatabase> | undefined;

const namespaced = (key: string): string => `${namespace}${key}`;

const now = (): number => Date.now();

const openDb = (): Promise<IDBDatabase> => {
  if (dbPromise) return dbPromise;

  dbPromise = new Promise((resolve, reject) => {
    if (!window.indexedDB) {
      reject(new Error('IndexedDB unavailable'));
      return;
    }

    const request = window.indexedDB.open(dbName, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore(storeName, { keyPath: 'key' });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('IndexedDB open failed'));
  });

  return dbPromise;
};

const localGet = <T>(key: string): CacheEntry<T> | undefined => {
  const raw = window.localStorage.getItem(key);
  if (!raw) return undefined;
  try {
    return JSON.parse(raw) as CacheEntry<T>;
  } catch {
    window.localStorage.removeItem(key);
    return undefined;
  }
};

const localSet = <T>(entry: CacheEntry<T>): void => {
  try {
    window.localStorage.setItem(entry.key, JSON.stringify(entry));
  } catch {
    // localStorage quota is small. IndexedDB remains the primary cache.
  }
};

export class BrowserCache {
  public async get<T>(key: string): Promise<CacheEntry<T> | undefined> {
    const cacheKey = namespaced(key);
    try {
      const db = await openDb();
      const entry = await new Promise<CacheEntry<T> | undefined>((resolve, reject) => {
        const tx = db.transaction(storeName, 'readonly');
        const request = tx.objectStore(storeName).get(cacheKey);
        request.onsuccess = () => resolve(request.result as CacheEntry<T> | undefined);
        request.onerror = () => reject(request.error || new Error('IndexedDB read failed'));
      });
      return this.validEntry(entry);
    } catch {
      return this.validEntry(localGet<T>(cacheKey));
    }
  }

  public async set<T>(key: string, value: T, ttlMs: number): Promise<void> {
    const entry: CacheEntry<T> = {
      key: namespaced(key),
      value,
      createdAt: now(),
      expiresAt: now() + ttlMs,
      version: cacheVersion
    };

    try {
      const db = await openDb();
      await new Promise<void>((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite');
        const request = tx.objectStore(storeName).put(entry);
        request.onsuccess = () => resolve();
        request.onerror = () => reject(request.error || new Error('IndexedDB write failed'));
      });
    } catch {
      localSet(entry);
    }
  }

  public async remove(key: string): Promise<void> {
    const cacheKey = namespaced(key);
    try {
      const db = await openDb();
      await new Promise<void>((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite');
        const request = tx.objectStore(storeName).delete(cacheKey);
        request.onsuccess = () => resolve();
        request.onerror = () => reject(request.error || new Error('IndexedDB delete failed'));
      });
    } catch {
      window.localStorage.removeItem(cacheKey);
    }
  }

  public async clearByPrefix(prefix: string): Promise<void> {
    const fullPrefix = namespaced(prefix);
    try {
      const db = await openDb();
      await new Promise<void>((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite');
        const store = tx.objectStore(storeName);
        const request = store.openCursor();
        request.onsuccess = () => {
          const cursor = request.result;
          if (cursor) {
            if (String(cursor.key).indexOf(fullPrefix) === 0) {
              cursor.delete();
            }
            cursor.continue();
          } else {
            resolve();
          }
        };
        request.onerror = () => reject(request.error || new Error('IndexedDB cursor failed'));
      });
    } catch {
      Object.keys(window.localStorage)
        .filter((key) => key.indexOf(fullPrefix) === 0)
        .forEach((key) => window.localStorage.removeItem(key));
    }
  }

  private validEntry<T>(entry: CacheEntry<T> | undefined): CacheEntry<T> | undefined {
    if (!entry) return undefined;
    if (entry.expiresAt < now()) {
      this.remove(entry.key.replace(namespace, '')).catch(() => undefined);
      return undefined;
    }
    return entry;
  }
}

export const browserCache = new BrowserCache();
