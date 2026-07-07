import { DefaultButton, Spinner, SearchBox } from '@fluentui/react';
import * as React from 'react';
import styles from '../components/MagApp.module.scss';
import { TimelineResult, TimelineEvent } from '../domain/types';
import { ITimelineService } from '../services/IAppServices';
import { errorText } from '../domain/localization';

export interface TimelinePageProps {
  docId?: string;
  timelineService: ITimelineService;
}

type FilterType = 'all' | 'communication' | 'official';

function getEventLane(event: TimelineEvent): 'communication' | 'official' | 'other' {
  const text = `${event.title} ${event.description || ''} ${event.relation || ''}`.toLowerCase();
  
  const commKeywords = ['פגישה', 'שיחה', 'סמס', 'הודעה', 'מפגש', 'קשר', 'meeting', 'call', 'sms', 'message', 'interaction', 'communication', 'contact'];
  const officialKeywords = ['אישור', 'חתימה', 'ועדה', 'החלטה', 'רגולציה', 'הנחיה', 'מכתב', 'צו', 'כנסת', 'בית משפט', 'regulatory', 'directive', 'decision', 'official', 'approval', 'sign', 'court'];
  
  if (commKeywords.some(kw => text.includes(kw))) {
    return 'communication';
  }
  if (officialKeywords.some(kw => text.includes(kw))) {
    return 'official';
  }
  return 'other';
}

export const TimelinePage: React.FC<TimelinePageProps> = ({ docId, timelineService }) => {
  const [timeline, setTimeline] = React.useState<TimelineResult | undefined>();
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | undefined>();
  
  // Search & Filter State
  const [searchText, setSearchText] = React.useState('');
  const [filterType, setFilterType] = React.useState<FilterType>('all');

  const loadTimeline = React.useCallback((forceRefresh = false): void => {
    if (!docId) return;

    setLoading(true);
    setError(undefined);

    timelineService.getTimeline(docId, { forceRefresh })
      .then((result) => {
        setTimeline(result);
      })
      .catch((loadError) => {
        setError(loadError instanceof Error ? errorText(loadError.message) : 'אירעה שגיאה בטעינת ציר הזמן.');
      })
      .then(() => {
        setLoading(false);
      });
  }, [docId, timelineService]);

  React.useEffect(() => loadTimeline(), [loadTimeline]);

  if (!docId) {
    return <div className={styles.emptyState}>יש להשלים עיבוד תיק לפני פתיחת ציר הזמן.</div>;
  }

  // Filter Events
  const filteredEvents = timeline ? timeline.events.filter(event => {
    // 1. Search text filter
    if (searchText) {
      const query = searchText.toLowerCase();
      const matchText = `${event.title} ${event.description || ''} ${event.participants.join(' ')} ${event.relation || ''}`.toLowerCase();
      if (!matchText.includes(query)) return false;
    }

    // 2. Tab filter
    const lane = getEventLane(event);
    if (filterType === 'communication') {
      return lane === 'communication';
    }
    if (filterType === 'official') {
      return lane === 'official';
    }

    return true;
  }) : [];

  return (
    <section className={styles.panel}>
      <div className={styles.panelHeader}>
        <div>
          <h2>אירועים וקשרים</h2>
          <span>פילוח אירועים, פעולות רשמיות ושיחות בין מעורבים מתוך גרף הידע.</span>
        </div>
        <div className={styles.headerActions}>
          {timeline?.cache?.fromCache && <span className={styles.cacheBadge}>נטען מהמטמון</span>}
          <DefaultButton text="רענן" iconProps={{ iconName: 'Refresh' }} onClick={() => loadTimeline(true)} disabled={loading} />
        </div>
      </div>

      {/* Filter and Search controls */}
      {timeline && (
        <div className={styles.timelineFilters}>
          <SearchBox
            placeholder="חיפוש לפי מעורבים או אירוע..."
            value={searchText}
            onChange={(_, val) => setSearchText(val || '')}
            styles={{ root: { width: 280 } }}
          />
          <div style={{ display: 'flex', gap: '8px' }}>
            <DefaultButton 
              text="הכל" 
              checked={filterType === 'all'} 
              onClick={() => setFilterType('all')} 
            />
            <DefaultButton 
              text="שיחות ומפגשים" 
              checked={filterType === 'communication'} 
              onClick={() => setFilterType('communication')} 
            />
            <DefaultButton 
              text="פעולות רשמיות" 
              checked={filterType === 'official'} 
              onClick={() => setFilterType('official')} 
            />
          </div>
        </div>
      )}

      {loading && <Spinner label="טוען ציר זמן" />}
      {error && <div className={styles.errorBox}>{error}</div>}
      
      {timeline && !loading && filteredEvents.length === 0 && (
        <div className={styles.emptyState}>לא נמצאו אירועים העונים לתנאי הסינון.</div>
      )}

      {timeline && filteredEvents.length > 0 && (
        <div className={styles.timelineList}>
          {filteredEvents.map((event) => {
            const lane = getEventLane(event);

            return (
              <article key={event.id} className={styles.timelineItem}>
                <div className={styles.timelineContent}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px', marginBottom: '12px' }}>
                    <h3 style={{ margin: 0, color: '#1e293b' }}>{event.title}</h3>
                    {lane === 'communication' && (
                      <span className={`${styles.timelineLaneBadge} ${styles.interactionLane}`}>
                        מפגש/תקשורת
                      </span>
                    )}
                    {lane === 'official' && (
                      <span className={`${styles.timelineLaneBadge} ${styles.officialLane}`}>
                        פעולה רשמית
                      </span>
                    )}
                  </div>
                  <dl>
                    <dt>אירוע</dt>
                    <dd>{event.description || event.title}</dd>
                    <dt>גורמים מעורבים</dt>
                    <dd>{event.participants.length ? event.participants.join(' · ') : 'לא זוהו'}</dd>
                    <dt>קשר בגרף</dt>
                    <dd>{event.relation || 'לא ידוע'}</dd>
                    <dt>מקור תומך</dt>
                    <dd>{event.sourceLocation || 'אין מקור תומך זמין'}</dd>
                  </dl>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
};
