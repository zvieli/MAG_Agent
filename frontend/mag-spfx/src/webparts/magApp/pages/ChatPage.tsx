import { PrimaryButton, Spinner, TextField } from '@fluentui/react';
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

const readStoredChat = (docId: string): StoredChatState => {
  try {
    const raw = window.sessionStorage.getItem(chatStorageKey(docId));
    if (!raw) return { messages: [], draft: '' };
    const parsed = JSON.parse(raw) as Partial<StoredChatState>;
    return {
      messages: Array.isArray(parsed.messages) ? restoredMessages(parsed.messages) : [],
      draft: typeof parsed.draft === 'string' ? parsed.draft : ''
    };
  } catch {
    return { messages: [], draft: '' };
  }
};

const writeStoredChat = (docId: string, state: StoredChatState): void => {
  try {
    window.sessionStorage.setItem(chatStorageKey(docId), JSON.stringify(state));
  } catch {
    // Session storage is best effort; the chat remains available in React state.
  }
};

export const ChatPage: React.FC<ChatPageProps> = ({ docId, chatService, pollIntervalMs }) => {
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [draft, setDraft] = React.useState('');
  const [pendingStatusUrl, setPendingStatusUrl] = React.useState<string | undefined>();
  const [pendingMessageId, setPendingMessageId] = React.useState<string | undefined>();
  const [error, setError] = React.useState<string | undefined>();
  const [pendingTraceId, setPendingTraceId] = React.useState<string | undefined>();
  const [pendingStartedAtMs, setPendingStartedAtMs] = React.useState<number | undefined>();
  const hasLoadedSessionRef = React.useRef(false);

  React.useEffect(() => {
    hasLoadedSessionRef.current = false;
    setPendingStatusUrl(undefined);
    setPendingMessageId(undefined);
    setPendingTraceId(undefined);
    setPendingStartedAtMs(undefined);
    setError(undefined);

    if (!docId) {
      setMessages([]);
      setDraft('');
      hasLoadedSessionRef.current = true;
      return;
    }

    const stored = readStoredChat(docId);
    setMessages(stored.messages);
    setDraft(stored.draft);
    hasLoadedSessionRef.current = true;
  }, [docId]);

  React.useEffect(() => {
    if (!docId || !hasLoadedSessionRef.current) return;
    writeStoredChat(docId, { messages, draft });
  }, [docId, draft, messages]);

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

        setMessages((current) =>
          current.map((message) =>
            message.id === pendingMessageId
              ? {
                  ...message,
                  content: status.runtimeStatus === 'Completed' ? status.output?.answer || 'לא התקבלה תשובה.' : 'השאילתה נכשלה.',
                  status: status.runtimeStatus === 'Completed' ? 'complete' : 'failed'
                }
              : message
          )
        );
      } catch (pollError) {
        window.clearInterval(timer);
        setPendingStatusUrl(undefined);
        setPendingMessageId(undefined);
        setPendingTraceId(undefined);
        setPendingStartedAtMs(undefined);
        setError(pollError instanceof Error ? errorText(pollError.message) : 'אירעה שגיאה בבדיקת מצב השאילתה.');
      }
    }, pollIntervalMs);

    return () => window.clearInterval(timer);
  }, [chatService, pendingMessageId, pendingStartedAtMs, pendingStatusUrl, pendingTraceId, pollIntervalMs]);

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
      content: 'מעבד תשובה...',
      createdAt: new Date().toISOString(),
      status: 'sending'
    };

    setDraft('');
    setError(undefined);
    setMessages((current) => [...current, userMessage, assistantMessage]);

    try {
      const operation = await chatService.ask(docId, question);
      setPendingStatusUrl(operation.statusUrl);
      setPendingMessageId(assistantMessage.id);
      setPendingTraceId(operation.traceId);
      setPendingStartedAtMs(operation.startedAtMs);
    } catch (sendError) {
      setError(sendError instanceof Error ? errorText(sendError.message) : 'אירעה שגיאה בהתחלת השאילתה.');
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantMessage.id ? { ...message, content: 'התחלת השאילתה נכשלה.', status: 'failed' } : message
        )
      );
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

  return (
    <section className={styles.panel}>
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
            {message.status === 'sending' && <Spinner />}
          </div>
        ))}
      </div>
      {error && <div className={styles.errorBox}>{error}</div>}
      <div className={styles.chatComposer}>
        <TextField
          multiline
          rows={3}
          value={draft}
          onChange={(_, value) => setDraft(value || '')}
          onKeyDown={onComposerKeyDown}
          placeholder="שאל על גורמים, קשרים, צירי זמן או ראיות."
        />
        <PrimaryButton text="שלח" iconProps={{ iconName: 'Send' }} onClick={send} disabled={!draft.trim() || Boolean(pendingStatusUrl)} />
      </div>
    </section>
  );
};
