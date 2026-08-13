import * as React from 'react';
import { IAuthenticationService, AuthenticationState } from '../auth/AuthenticationService';
import styles from './MagApp.module.scss';

export interface AuthenticationGateProps {
  authentication: IAuthenticationService;
  children: React.ReactNode;
}

export const AuthenticationGate: React.FC<AuthenticationGateProps> = ({ authentication, children }) => {
  const [state, setState] = React.useState<AuthenticationState>(authentication.state);

  React.useEffect(() => authentication.subscribe(setState), [authentication]);

  React.useEffect(() => {
    void authentication.initialize();
  }, [authentication]);

  if (state.status === 'ready') return <>{children}</>;

  const isInitializing = state.status === 'initializing';
  const canRetry = state.status === 'interaction-required';
  const signIn = (): void => {
    void authentication.signIn().catch(() => undefined);
  };

  return (
    <section className={styles.authGate} dir="rtl" aria-live="polite">
      <div className={styles.authCard}>
        <h2>{isInitializing ? 'מתבצע אימות…' : 'נדרשת התחברות למערכת'}</h2>
        <p>
          {state.message || (isInitializing
            ? 'המערכת בודקת אם קיים חיבור פעיל ל־Entra ID.'
            : 'לא ניתן להציג את מסכי המערכת עד להשלמת האימות.')}
        </p>
        {canRetry && (
          <button type="button" className={styles.authButton} onClick={signIn}>
            התחברות למערכת
          </button>
        )}
        {state.status === 'error' && (
          <div className={styles.authMessage} role="alert">
            לא נשלחה בקשה לשירות. יש לבדוק את תצורת הפריסה לפני ניסיון נוסף.
          </div>
        )}
      </div>
    </section>
  );
};

