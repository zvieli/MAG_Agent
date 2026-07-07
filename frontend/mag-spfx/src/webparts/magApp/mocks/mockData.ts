import { GraphQueryResult, ReportResult } from '../domain/types';

export const mockGraph = (docId: string): GraphQueryResult => ({
  docId,
  source: 'mock',
  nodes: [
    {
      id: 'actor_miriam_levi',
      label: 'מרים לוי',
      type: 'PERSON',
      communityId: '1',
      degree: 4,
      description: 'גורם מרכזי שמופיע במסמך מספר פעמים.'
    },
    {
      id: 'org_north_star',
      label: 'נורת סטאר החזקות',
      type: 'ORGANIZATION',
      communityId: '1',
      degree: 3,
      description: 'ארגון המקושר לאירועי התקשרות והעברות כספיות.'
    },
    {
      id: 'event_transfer_2024',
      label: 'העברה כספית מרץ 2024',
      type: 'EVENT',
      communityId: '2',
      degree: 2,
      description: 'אירוע פיננסי שחולץ ממקור ראייתי.'
    },
    {
      id: 'location_tel_aviv',
      label: 'תל אביב',
      type: 'LOCATION',
      communityId: '2',
      degree: 1
    }
  ],
  edges: [
    {
      id: 'actor_miriam_levi_DIRECTED_org_north_star',
      source: 'actor_miriam_levi',
      target: 'org_north_star',
      sourceLabel: 'מרים לוי',
      targetLabel: 'נורת סטאר החזקות',
      relation: 'DIRECTED',
      description: 'מרים לוי ניהלה את פעילות הארגון בתקופה הרלוונטית.',
      confidence: 0.91,
      sourceLocation: 'מקטע 00003'
    },
    {
      id: 'org_north_star_FINANCIAL_TRANSFER_event_transfer_2024',
      source: 'org_north_star',
      target: 'event_transfer_2024',
      sourceLabel: 'נורת סטאר החזקות',
      targetLabel: 'העברה כספית מרץ 2024',
      relation: 'FINANCIAL_TRANSFER',
      description: 'הארגון נקשר לאירוע העברה כספית במסמך.',
      confidence: 0.82,
      sourceLocation: 'מקטע 00007'
    },
    {
      id: 'event_transfer_2024_OCCURRED_AT_location_tel_aviv',
      source: 'event_transfer_2024',
      target: 'location_tel_aviv',
      sourceLabel: 'העברה כספית מרץ 2024',
      targetLabel: 'תל אביב',
      relation: 'OCCURRED_AT',
      description: 'האירוע התרחש באזור תל אביב לפי מקור הראיה.',
      confidence: 0.74,
      sourceLocation: 'מקטע 00008'
    }
  ],
  metadata: {
    nodeCount: 4,
    edgeCount: 3,
    nodeTypes: ['PERSON', 'ORGANIZATION', 'EVENT', 'LOCATION'],
    generatedAt: new Date().toISOString()
  }
});

export const mockReport = (docId: string): ReportResult => ({
  docId,
  source: 'mock',
  updatedAt: new Date().toISOString(),
  markdown: `# דו"ח גרף ידע: ${docId}

## נתוני גרף

- **סך ישויות:** 4
- **סך קשרים:** 3
- **קהילות שזוהו:** 2

## גורמים מרכזיים

| גורם | מרכזיות | הערות |
| --- | ---: | --- |
| מרים לוי | 0.84 | גורם מרכזי בתיק ההדגמה |
| נורת סטאר החזקות | 0.67 | ארגון המקושר לאירוע העברה |

## תובנות קהילתיות

### קהילה 1

אשכול גורם מרכזי וארגון.

### קהילה 2

אשכול אירוע ומיקום.

## הערת יישום

תוכן הדגמה זה נשמר בזיכרון בלבד מאחורי מתאם הדו"ח. בסביבת ייצור הדו"ח ייטען מתוך Blob Storage דרך נקודת API, ובהמשך ניתן יהיה לפרק מקטעים יציבים לכרטיסים ולתרשימים.`
});

export const mockCommunitySummaries = (docId: string) => ({
  docId,
  source: 'mock' as const,
  updatedAt: new Date().toISOString(),
  summaries: {
    c_1: 'קהילה המדגימה קשר בין גורם מרכזי לארגון.',
    c_2: 'קהילה המדגימה קשר בין אירוע, מיקום וראיה תומכת.'
  },
  hierarchy: {
    c_2: 'c_1'
  }
});
