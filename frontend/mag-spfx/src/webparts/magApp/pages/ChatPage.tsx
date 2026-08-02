import { PrimaryButton, DefaultButton, Spinner, TextField } from '@fluentui/react';
import * as React from 'react';
import { isTerminalStatus, makeId } from '../domain/guards';
import { ChatMessage } from '../domain/types';
import { IChatService } from '../services/IAppServices';
import styles from '../components/MagApp.module.scss';
import { errorText } from '../domain/localization';
import { MarkdownView } from '../components/MarkdownView';

export interface ChatPageProps {
  docId?: string;
  chatService: IChatService;
  pollIntervalMs: number;
}

interface StoredChatState {
  messages: ChatMessage[];
  draft: string;
}

const chatStorageKey = (docId: string): string => `mag:chat:${docId}`;
const stateStorageKey = (docId: string): string => `mag:state:${docId}`;

const chatBackupKey = (docId: string): string => `mag:chat_backup:${docId}`;
const stateBackupKey = (docId: string): string => `mag:state_backup:${docId}`;

const restoredMessages = (messages: ChatMessage[]): ChatMessage[] =>
  messages.map((message) =>
    message.status === 'sending'
      ? {
          ...message,
          content: 'השאילתה הופסקה בעת המעבר בין דפים. יש לשלוח את השאלה מחדש.',
          status: 'failed'
        }
      : message
  );

const readStoredChat = (docId: string): StoredChatState | null => {
  try {
    const raw = window.sessionStorage.getItem(chatStorageKey(docId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredChatState>;
    return {
      messages: Array.isArray(parsed.messages) ? restoredMessages(parsed.messages) : [],
      draft: typeof parsed.draft === 'string' ? parsed.draft : ''
    };
  } catch {
    return null;
  }
};

const readBackupChat = (docId: string): StoredChatState | null => {
  try {
    const raw = window.localStorage.getItem(chatBackupKey(docId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredChatState>;
    return {
      messages: Array.isArray(parsed.messages) ? restoredMessages(parsed.messages) : [],
      draft: typeof parsed.draft === 'string' ? parsed.draft : ''
    };
  } catch {
    return null;
  }
};

const writeStoredChat = (docId: string, state: StoredChatState): void => {
  try {
    window.sessionStorage.setItem(chatStorageKey(docId), JSON.stringify(state));
    window.localStorage.setItem(chatBackupKey(docId), JSON.stringify(state));
  } catch {
    // Storage is best effort
  }
};

export const ChatPage: React.FC<ChatPageProps> = ({ docId, chatService, pollIntervalMs }) => {
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [draft, setDraft] = React.useState('');
  const [activeState, setActiveState] = React.useState<any | null>(null);
  const [pendingStatusUrl, setPendingStatusUrl] = React.useState<string | undefined>();
  const [pendingMessageId, setPendingMessageId] = React.useState<string | undefined>();
  const [error, setError] = React.useState<string | undefined>();
  const [pendingTraceId, setPendingTraceId] = React.useState<string | undefined>();
  const [pendingStartedAtMs, setPendingStartedAtMs] = React.useState<number | undefined>();
  
  // Resiliency & Timer States
  const [sessionRecovered, setSessionRecovered] = React.useState(false);
  const [elapsedSeconds, setElapsedSeconds] = React.useState(0);
  
  // HITL State
  const [isEditingFact, setIsEditingFact] = React.useState(false);
  const [editedFactText, setEditedFactText] = React.useState('');
  
  const hasLoadedSessionRef = React.useRef(false);

  // 1. Load chat messages & serialized graph state (with LocalStorage recovery fallback)
  React.useEffect(() => {
    hasLoadedSessionRef.current = false;
    setPendingStatusUrl(undefined);
    setPendingMessageId(undefined);
    setPendingTraceId(undefined);
    setPendingStartedAtMs(undefined);
    setError(undefined);
    setIsEditingFact(false);
    setEditedFactText('');
    setSessionRecovered(false);
    setElapsedSeconds(0);

    if (!docId) {
      setMessages([]);
      setDraft('');
      setActiveState(null);
      hasLoadedSessionRef.current = true;
      return;
    }

    // Try reading active tab sessionStorage
    let chatState = readStoredChat(docId);
    let isRecovered = false;

    if (!chatState) {
      // Fallback: Recover from local localStorage backup (Investigation Longevity)
      chatState = readBackupChat(docId);
      if (chatState && chatState.messages.length > 0) {
        isRecovered = true;
      }
    }

    if (chatState) {
      setMessages(chatState.messages);
      setDraft(chatState.draft);
    } else {
      setMessages([]);
      setDraft('');
    }

    // Load active state
    let stateRaw = window.sessionStorage.getItem(stateStorageKey(docId));
    if (!stateRaw) {
      stateRaw = window.localStorage.getItem(stateBackupKey(docId));
    }

    if (stateRaw) {
      try {
        const stateObj = JSON.parse(stateRaw);
        setActiveState(stateObj);
        if (stateObj.pending_fact_approval) {
          setEditedFactText(stateObj.pending_fact_approval.fact);
        }
      } catch {
        setActiveState(null);
      }
    } else {
      setActiveState(null);
    }

    if (isRecovered) {
      setSessionRecovered(true);
    }
    
    hasLoadedSessionRef.current = true;
  }, [docId]);

  // 2. Persist chat history and serialized state changes to sessionStorage and localStorage backup
  React.useEffect(() => {
    if (!docId || !hasLoadedSessionRef.current) return;
    writeStoredChat(docId, { messages, draft });
  }, [docId, draft, messages]);

  React.useEffect(() => {
    if (!docId || !hasLoadedSessionRef.current) return;
    if (activeState) {
      window.sessionStorage.setItem(stateStorageKey(docId), JSON.stringify(activeState));
      window.localStorage.setItem(stateBackupKey(docId), JSON.stringify(activeState));
    } else {
      window.sessionStorage.removeItem(stateStorageKey(docId));
      window.localStorage.removeItem(stateBackupKey(docId));
    }
  }, [docId, activeState]);

  // 3. Dynamic HITL Live clock calculations based on extraction timestamp
  React.useEffect(() => {
    const timestampStr = activeState?.pending_fact_approval?.audit_trace?.timestamp;
    if (!timestampStr) {
      setElapsedSeconds(0);
      return;
    }

    const startMs = new Date(timestampStr).getTime();
    if (isNaN(startMs)) {
      setElapsedSeconds(0);
      return;
    }

    // Set initial elapsed
    setElapsedSeconds(Math.max(0, Math.floor((Date.now() - startMs) / 1000)));

    const timer = window.setInterval(() => {
      setElapsedSeconds(Math.max(0, Math.floor((Date.now() - startMs) / 1000)));
    }, 1000);

    return () => window.clearInterval(timer);
  }, [activeState?.pending_fact_approval]);

  // Error Handler with 403 / 401 Session Refresh Detection
  const handleNetworkError = (err: any, assistantMsgId?: string): void => {
    if (err && (err.status === 403 || err.status === 401)) {
      setError('שגיאת הרשאה או פג תוקף חיבור (403/401). מבצע רענון לעמוד לצורך אימות מחדש...');
      window.setTimeout(() => {
        window.location.reload();
      }, 2000);
      return;
    }
    setError(err instanceof Error ? errorText(err.message) : 'אירעה שגיאה בתקשורת עם השרת.');
    if (assistantMsgId) {
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantMsgId ? { ...message, content: 'השאילתה נכשלה.', status: 'failed' } : message
        )
      );
    }
  };

  // 4. Status Polling Loop
  React.useEffect(() => {
    if (!pendingStatusUrl || !pendingMessageId) return;

    const timer = window.setInterval(async () => {
      try {
        const status = await chatService.getStatus(pendingStatusUrl, pendingTraceId, pendingStartedAtMs);
        if (!isTerminalStatus(status.runtimeStatus)) return;

        window.clearInterval(timer);
        setPendingStatusUrl(undefined);
        setPendingMessageId(undefined);
        setPendingTraceId(undefined);
        setPendingStartedAtMs(undefined);

        if (status.runtimeStatus === 'Completed') {
          const stateObj = status.output?.state;
          if (stateObj) {
            setActiveState(stateObj);
            
            // Render final report if complete
            if (stateObj.final_answer) {
              setMessages((current) =>
                current.map((message) =>
                  message.id === pendingMessageId
                    ? {
                        ...message,
                        content: stateObj.final_answer,
                        status: 'complete'
                      }
                    : message
                )
              );
            } else if (stateObj.pending_fact_approval) {
              // Pause flow for user review
              setMessages((current) =>
                current.map((message) =>
                  message.id === pendingMessageId
                    ? {
                        ...message,
                        content: '*מערכת:* זוהתה עובדה חדשה הדורשת אישור משפטי. אנא עיין בפרטים בלוח המעקב למטה.',
                        status: 'complete'
                      }
                    : message
                )
              );
              setEditedFactText(stateObj.pending_fact_approval.fact);
            } else {
              setMessages((current) =>
                current.map((message) =>
                  message.id === pendingMessageId
                    ? {
                        ...message,
                        content: 'העיבוד הסתיים ללא ממצאים חדשים.',
                        status: 'complete'
                      }
                    : message
                )
              );
            }
          } else {
            // Legacy / fallback response compatibility
            setMessages((current) =>
              current.map((message) =>
                message.id === pendingMessageId
                  ? {
                      ...message,
                      content: status.output?.answer || 'לא התקבלה תשובה מהשרת.',
                      status: 'complete'
                    }
                  : message
              )
            );
          }
        } else {
          setMessages((current) =>
            current.map((message) =>
              message.id === pendingMessageId ? { ...message, content: 'השאילתה נכשלה.', status: 'failed' } : message
            )
          );
        }
      } catch (pollError) {
        window.clearInterval(timer);
        setPendingStatusUrl(undefined);
        setPendingMessageId(undefined);
        setPendingTraceId(undefined);
        setPendingStartedAtMs(undefined);
        handleNetworkError(pollError, pendingMessageId);
      }
    }, pollIntervalMs);

    return () => window.clearInterval(timer);
  }, [chatService, pendingMessageId, pendingStartedAtMs, pendingStatusUrl, pendingTraceId, pollIntervalMs]);

  // 5. Send Message (First Turn)
  const send = async (): Promise<void> => {
    const question = draft.trim();
    if (!docId || !question || pendingStatusUrl) return;

    const userMessage: ChatMessage = {
      id: makeId('user'),
      role: 'user',
      content: question,
      createdAt: new Date().toISOString(),
      status: 'complete'
    };
    const assistantMessage: ChatMessage = {
      id: makeId('assistant'),
      role: 'assistant',
      content: 'מתחיל עיבוד חקירתי של השאלה...',
      createdAt: new Date().toISOString(),
      status: 'sending'
    };

    setDraft('');
    setError(undefined);
    setMessages((current) => [...current, userMessage, assistantMessage]);

    try {
      // Clear out old finished state if restarting conversation with a new question
      const cleanState = activeState && activeState.final_answer ? null : activeState;
      const operation = await chatService.ask(docId, question, cleanState);
      setPendingStatusUrl(operation.statusUrl);
      setPendingMessageId(assistantMessage.id);
      setPendingTraceId(operation.traceId);
      setPendingStartedAtMs(operation.startedAtMs);
    } catch (sendError) {
      handleNetworkError(sendError, assistantMessage.id);
    }
  };

  // 6. Submit Human-in-the-Loop Feedback (Approve / Reject / Edit)
  const submitHitlFeedback = async (feedbackType: 'approved' | 'rejected' | 'edited'): Promise<void> => {
    if (!docId || !activeState || !activeState.pending_fact_approval || pendingStatusUrl) return;

    let feedbackVal = '';
    if (feedbackType === 'approved') {
      feedbackVal = 'approved';
    } else if (feedbackType === 'rejected') {
      feedbackVal = 'rejected';
    } else {
      feedbackVal = editedFactText.trim();
      if (!feedbackVal) return;
    }

    // Update state client-side
    const updatedState = {
      ...activeState,
      user_feedback: feedbackVal
    };

    const assistantMessage: ChatMessage = {
      id: makeId('assistant'),
      role: 'assistant',
      content: `מעבד משוב משתמש: "${feedbackVal === 'approved' ? 'מאושר' : feedbackVal === 'rejected' ? 'נדחה' : 'נערך: ' + feedbackVal}"...`,
      createdAt: new Date().toISOString(),
      status: 'sending'
    };

    setError(undefined);
    setIsEditingFact(false);
    setMessages((current) => [...current, assistantMessage]);

    // Optimistically update memory state
    setActiveState(updatedState);

    try {
      // Resend full state to continue execution flow
      const operation = await chatService.ask(docId, '', updatedState);
      setPendingStatusUrl(operation.statusUrl);
      setPendingMessageId(assistantMessage.id);
      setPendingTraceId(operation.traceId);
      setPendingStartedAtMs(operation.startedAtMs);
    } catch (sendError) {
      handleNetworkError(sendError, assistantMessage.id);
    }
  };

  const onComposerKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement | HTMLInputElement>): void => {
    if (event.key !== 'Enter' || event.shiftKey) {
      return;
    }
    event.preventDefault();
    send().catch(() => undefined);
  };

  if (!docId) {
    return <div className={styles.emptyState}>יש להשלים עיבוד תיק לפני פתיחת שיחה עם הסוכן.</div>;
  }

  // Detect pending fact approval
  const pendingFact = activeState?.pending_fact_approval;

  // Format Elapsed Time string
  const formatElapsedTime = (sec: number): string => {
    const mins = Math.floor(sec / 60);
    const secs = sec % 60;
    return `${mins} דקות ו-${secs} שניות`;
  };

  // Warning when timer is active and elapsed time > 15 minutes (900 seconds)
  const isTimeoutWarning = elapsedSeconds > 900;

  return (
    <section className={styles.panel}>
      {sessionRecovered && (
        <div style={{
          backgroundColor: '#e0f2fe',
          border: '1px solid #7dd3fc',
          color: '#0369a1',
          padding: '12px',
          borderRadius: '6px',
          marginBottom: '16px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          fontSize: '13.5px'
        }}>
          <span>♻️ שוחזר סשן שיחה אחרון שנשמר במחשב מקומית (LocalStorage) לטובת עמידות החקירה.</span>
          <DefaultButton 
            text="סגור" 
            onClick={() => setSessionRecovered(false)}
            styles={{ root: { minWidth: 'auto', height: '28px', padding: '0 8px', fontSize: '12px' } }}
          />
        </div>
      )}

      <div className={styles.panelHeader}>
        <h2>שיחה עם הסוכן</h2>
        <span>מזהה תיק: <span className={styles.ltrValue}>{docId}</span></span>
      </div>
      
      <div className={styles.chatTranscript}>
        {messages.length === 0 && <p className={styles.emptyText}>שאל שאלה חקירתית על התיק הפעיל.</p>}
        {messages.map((message) => (
          <div key={message.id} className={`${styles.chatBubble} ${message.role === 'user' ? styles.chatUser : styles.chatAssistant}`}>
            <strong>{message.role === 'user' ? 'את/ה' : 'הסוכן'}</strong>
            <MarkdownView markdown={message.content} />
            {message.status === 'sending' && <Spinner label="מחשב שלבים..." />}
          </div>
        ))}
      </div>

      {/* Human-in-the-Loop Review Panel */}
      {pendingFact && (
        <div style={{
          margin: '20px 0',
          padding: '16px',
          backgroundColor: isTimeoutWarning ? '#fef2f2' : '#fffbeb',
          border: isTimeoutWarning ? '1px solid #fecaca' : '1px solid #fcd34d',
          borderRadius: '8px',
          direction: 'rtl',
          textAlign: 'right'
        }}>
          <h3 style={{ margin: '0 0 10px', color: isTimeoutWarning ? '#b91c1c' : '#b45309', display: 'flex', alignItems: 'center', gap: '8px' }}>
            🔔 אישור ממצא משפטי (Human-in-the-Loop)
          </h3>
          <p style={{ margin: '0 0 12px', fontSize: '14px', color: '#4b5563' }}>
            הסוכן הציע להוסיף את העובדה הבאה למסד העובדות הקבילות עבור התיק:
          </p>

          <div style={{ padding: '10px', backgroundColor: '#fff', borderRadius: '4px', border: '1px solid #f3f4f6', marginBottom: '12px' }}>
            <strong>עובדה מוצעת:</strong>
            {isEditingFact ? (
              <TextField
                multiline
                rows={2}
                value={editedFactText}
                onChange={(_, val) => setEditedFactText(val || '')}
                styles={{ root: { marginTop: '6px' } }}
              />
            ) : (
              <p style={{ margin: '4px 0 0', fontWeight: 500 }}>{pendingFact.fact}</p>
            )}
            <p style={{ margin: '8px 0 0', fontSize: '12px', color: '#6b7280' }}>
              <strong>מקור:</strong> {pendingFact.source_id}
            </p>
            {pendingFact.audit_trace?.investigative_task && (
              <p style={{ margin: '4px 0 0', fontSize: '12px', color: '#6b7280' }}>
                <strong>פעילות חקירתית:</strong> {pendingFact.audit_trace.investigative_task}
              </p>
            )}
            {elapsedSeconds > 0 && (
              <p style={{ 
                margin: '8px 0 0', 
                fontSize: '12px', 
                color: isTimeoutWarning ? '#dc2626' : '#b45309',
                fontWeight: isTimeoutWarning ? 600 : 500
              }}>
                ⏱️ זמן שחלף מאז הפקת הממצא: {formatElapsedTime(elapsedSeconds)}
              </p>
            )}
          </div>

          {isTimeoutWarning && (
            <div style={{
              backgroundColor: '#fee2e2',
              border: '1px solid #fca5a5',
              color: '#991b1b',
              padding: '10px',
              borderRadius: '4px',
              marginBottom: '12px',
              fontSize: '13px',
              fontWeight: 500
            }}>
              ⚠️ שים לב: עבר זמן רב מאז הפקת העובדה. מומלץ לקבל החלטה (לאשר, לערוך או לדחות) על מנת למנוע ניתוק ופג תוקף סשן (Timeout) מול השרת.
            </div>
          )}

          <div style={{ display: 'flex', gap: '8px' }}>
            {isEditingFact ? (
              <>
                <PrimaryButton 
                  text="שמור ושלח" 
                  onClick={() => submitHitlFeedback('edited')} 
                  disabled={!editedFactText.trim() || Boolean(pendingStatusUrl)}
                />
                <DefaultButton 
                  text="ביטול" 
                  onClick={() => {
                    setIsEditingFact(false);
                    setEditedFactText(pendingFact.fact);
                  }}
                  disabled={Boolean(pendingStatusUrl)}
                />
              </>
            ) : (
              <>
                <PrimaryButton 
                  text="אשר עובדה" 
                  onClick={() => submitHitlFeedback('approved')} 
                  disabled={Boolean(pendingStatusUrl)}
                  styles={{ root: { backgroundColor: '#10b981', borderColor: '#10b981' } }}
                />
                <DefaultButton 
                  text="ערוך עובדה" 
                  onClick={() => setIsEditingFact(true)}
                  disabled={Boolean(pendingStatusUrl)}
                />
                <DefaultButton 
                  text="דחה עובדה" 
                  onClick={() => submitHitlFeedback('rejected')} 
                  disabled={Boolean(pendingStatusUrl)}
                  styles={{ root: { color: '#ef4444' } }}
                />
              </>
            )}
          </div>
        </div>
      )}

      {error && <div className={styles.errorBox}>{error}</div>}

      <div className={styles.tacticalChipsContainer}>
        <button
          type="button"
          className={styles.tacticalChip}
          onClick={() => setDraft('הצג את גורמי המפתח והדמויות המרכזיות בתיק')}
          disabled={Boolean(pendingFact) || Boolean(pendingStatusUrl)}
        >
          🔍 גורמי מפתח בתיק
        </button>
        <button
          type="button"
          className={styles.tacticalChip}
          onClick={() => setDraft('מהם החוסרים הקריטיים והבדיקות הנדרשות בבקרת שלמות התיק?')}
          disabled={Boolean(pendingFact) || Boolean(pendingStatusUrl)}
        >
          ⚠️ סקור חוסרים בראיות
        </button>
        <button
          type="button"
          className={styles.tacticalChip}
          onClick={() => setDraft('האם זוהו סתירות או קשרים צולבים מיוחדים בין מעורבים בפרשה?')}
          disabled={Boolean(pendingFact) || Boolean(pendingStatusUrl)}
        >
          💡 סתירות וקשרים צולבים
        </button>
      </div>

      <div className={styles.chatComposer}>
        <TextField
          multiline
          rows={3}
          value={draft}
          onChange={(_, value) => setDraft(value || '')}
          onKeyDown={onComposerKeyDown}
          placeholder={pendingFact ? "נא להשיב לממצא המשפטי הממתין לאישור" : "שאל על גורמים, קשרים, צירי זמן או ראיות."}
          disabled={Boolean(pendingFact) || Boolean(pendingStatusUrl)}
        />
        <PrimaryButton 
          text="שלח" 
          iconProps={{ iconName: 'Send' }} 
          onClick={send} 
          disabled={!draft.trim() || Boolean(pendingStatusUrl) || Boolean(pendingFact)} 
        />
      </div>
    </section>
  );
};
