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
    <main className={styles.main}>{children}</main>
  </section>
);
