import * as React from 'react';
import styles from './MagApp.module.scss';
import { AppPage, CaseSession } from '../domain/types';

export interface PageShellProps {
  activePage: AppPage;
  session: CaseSession;
  onNavigate: (page: AppPage) => void;
  children: React.ReactNode;
}

const navItems: { key: AppPage; label: string; icon: string; requiresCase?: boolean }[] = [
  { key: 'upload', label: 'העלאת קבצים', icon: 'Upload' },
  { key: 'graph', label: 'גרף ידע', icon: 'Relationship', requiresCase: true },
  { key: 'report', label: 'דו"ח ניתוח', icon: 'AnalyticsReport', requiresCase: true },
  { key: 'chat', label: 'שיחה עם הסוכן', icon: 'Chat', requiresCase: true }
];

export const PageShell: React.FC<PageShellProps> = ({ activePage, session, onNavigate, children }) => (
  <section className={styles.appShell} dir="rtl" lang="he">
    <header className={styles.header}>
      <div>
        <h1 style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
           מערכת ניתוח תיקי חקירה
          
        </h1>
        <p>
          {session.docId ? (
            <>
              תיק פעיל: <strong className={styles.ltrValue} style={{ color: '#0f766e' }}>{session.docId}</strong>
            </>
          ) : (
            'יש לבחור מסמך PDF מ-SharePoint להפעלת ניתוח תיק חקירה.'
          )}
        </p>
      </div>
    </header>
    <nav className={styles.nav} aria-label="דפי מערכת ניתוח תיקי חקירה">
      {navItems.map((item) => {
        const disabled = Boolean(item.requiresCase && !session.docId);
        return (
          <button
            key={item.key}
            type="button"
            className={`${styles.navButton} ${activePage === item.key ? styles.navButtonActive : ''}`}
            disabled={disabled}
            onClick={() => onNavigate(item.key)}
            title={disabled ? 'זמין לאחר השלמת העיבוד' : item.label}
          >
            <i className={`ms-Icon ms-Icon--${item.icon}`} aria-hidden="true" />
            <span>{item.label}</span>
          </button>
        );
      })}
    </nav>
    <main className={styles.main}>
      {(session.pipeline?.output?.review_required || session.pipeline?.customStatus?.review_required) && (() => {
        const blockedRanges =
          session.pipeline?.output?.blocked_page_ranges ||
          session.pipeline?.customStatus?.blocked_page_ranges ||
          [];
        const failedRanges =
          session.pipeline?.output?.failed_page_ranges ||
          session.pipeline?.customStatus?.failed_page_ranges ||
          [];
        const failedCount =
          session.pipeline?.output?.failed_count ??
          session.pipeline?.customStatus?.failed_chunks ??
          0;
        const coverageRatio =
          session.pipeline?.output?.coverage_ratio ??
          session.pipeline?.customStatus?.coverage_ratio ??
          0;
        const traceId =
          session.pipeline?.output?.trace_id ||
          session.pipeline?.traceId ||
          'לא זמין';

        return (
          <div className={styles.partialCoverageWarning} role="status" aria-live="polite">
            <strong>
              {session.pipeline?.output?.status === 'content_blocked'
                ? '⚠️ כל התוכן השמיש נחסם ולא נוצר גרף.'
                : '⚠️ הגרף חלקי ונדרשת בדיקה.'}
            </strong>{' '}
            {blockedRanges.length > 0 && (
              <>טווחי מקור שנחסמו ע"י מדיניות: {JSON.stringify(blockedRanges)}. </>
            )}
            {failedCount > 0 && (
              <>
                <strong>קטעים שנכשלו בחילוץ אוטומטי: {failedCount}</strong>
                {failedRanges.length > 0 && <> (עמודים: {JSON.stringify(failedRanges)})</>}.{' '}
                יש לבדוק חומר זה ידנית.{' '}
              </>
            )}
            {blockedRanges.length === 0 && failedCount === 0 && (
              <>הושמטו טווחי מקור.{' '}</>
            )}
            כיסוי משוקלל לפי טוקנים: {Math.round(100 * coverageRatio)}%.{' '}
            אין לפרש ממצא חסר כהיעדר מהמסמך המלא. מזהה מעקב:{' '}
            <span className={styles.ltrValue}>{traceId}</span>
          </div>
        );
      })()}
      {children}
    </main>
  </section>
);
