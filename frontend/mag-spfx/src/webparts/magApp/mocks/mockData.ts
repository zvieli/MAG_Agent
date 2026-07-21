import { GraphQueryResult, ProtocolCompletenessResult, ReportResult } from '../domain/types';

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

export const mockProtocolCompleteness = (docId: string): ProtocolCompletenessResult => ({
  docId,
  source: 'mock',
  protocolVersion: '1.2',
  generatedAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
  disclaimer: 'בדיקת הדגמה: חוסר משמעו שלא אותר סימן בחומר שהועלה, ולא קביעה משפטית.',
  summary: {
    totalItems: 7,
    applicableItems: 6,
    present: 3,
    missing: 1,
    conditionalMissing: 2,
    notApplicable: 1,
    criticalMissing: 1,
    criticalReviewRequired: 1,
    coveragePercent: 60
  },
  items: [
    {
      id: 'opening_initial_report',
      pillar: 'opening',
      label: 'תלונה ראשונית / דו"ח פעולה',
      description: 'מסמך פתיחת האירוע שהוביל לפתיחת החקירה.',
      severity: 'critical',
      status: 'present',
      reason: 'אותר סימן מתאים בתיק שהועלה.',
      applicability: 'always',
      evidenceStrength: 'strong',
      evidence: [{ source: 'raw_text', signal: 'תלונה', snippet: 'נפתחה תלונה ראשונית והועברה להמשך טיפול.' }],
      sourceRefs: [{ guideline: 'client_protocol', section: 'Opening documents' }]
    },
    {
      id: 'investigation_material_list',
      pillar: 'opening',
      label: 'רשימת חומר שנאסף או נרשם',
      description: 'רשימת מסמכים ופעולות חקירה הכוללת זיהוי וסיווג.',
      severity: 'critical',
      status: 'missing',
      reason: 'לא אותרה רשימת חומר חקירה שנאסף או נרשם.',
      applicability: 'always',
      evidence: [],
      sourceRefs: [{ guideline: 'tzr_1_05', pages: '1-4' }]
    },
    {
      id: 'rights_warning_form',
      pillar: 'interrogations',
      label: 'אזהרה וזכויות נחקר',
      description: 'תיעוד אזהרת חשוד וזכות היוועצות עם עורך דין.',
      severity: 'critical',
      status: 'conditional_missing',
      reason: 'לא אותר תיעוד של אזהרת החשוד והקראת זכויות.',
      applicability: 'keyword',
      evidenceStrength: 'none',
      evidence: [],
      sourceRefs: [{ guideline: 'client_protocol', section: 'Warning and reading of rights' }]
    },
    {
      id: 'exhibit_description_or_seizure',
      pillar: 'exhibits',
      label: 'תיאור מוצג או תיעוד תפיסה',
      description: 'תיאור טקסטואלי של מוצג או תיעוד תפיסתו.',
      severity: 'critical',
      status: 'present',
      reason: 'אותר סימן מתאים בתיק שהועלה.',
      applicability: 'keyword',
      evidenceStrength: 'strong',
      evidence: [{ source: 'graph_inventory', signal: 'SEIZED_FROM', snippet: 'SEIZED_FROM phone exhibit', strength: 'strong' }],
      sourceRefs: [{ guideline: 'tzr_3_02', pages: '1-4' }]
    },
    {
      id: 'exhibit_chain_of_custody',
      pillar: 'exhibits',
      label: 'שרשרת מוצג והחזקה',
      description: 'תיעוד שרשרת מוצג, סימון, החזקה או העברה.',
      severity: 'critical',
      status: 'conditional_missing',
      reason: 'אותר מוצג או תיעוד תפיסה, אך לא אותרה שרשרת מוצג מלאה.',
      applicability: 'keyword',
      applicabilityEvidence: {
        source: 'graph_inventory',
        signal: 'SEIZED_FROM',
        snippet: 'SEIZED_FROM phone exhibit',
        matchType: 'keyword'
      },
      evidenceStrength: 'weak',
      reviewNote: 'תיאור מוצג או יחס תפיסה אינם מוכיחים כשלעצמם שרשרת החזקה, סימון והעברה.',
      evidence: [{ source: 'graph_inventory', signal: 'SEIZED_FROM', snippet: 'SEIZED_FROM phone exhibit', strength: 'weak' }],
      sourceRefs: [{ guideline: 'tzr_3_02', pages: '1-4' }]
    },
    {
      id: 'victim_rights_notification',
      pillar: 'victim_rights',
      label: 'יידוע נפגע עבירה וזכויותיו',
      description: 'תיעוד יצירת קשר, יידוע זכויות ועדכונים לנפגע עבירה.',
      severity: 'critical',
      status: 'not_applicable',
      reason: 'תנאי התחולה לא זוהו בתיק שהועלה.',
      applicability: 'condition_not_met',
      evidence: [],
      sourceRefs: [{ guideline: 'tzr_1_13', pages: '1-5' }]
    },
    {
      id: 'case_summary_recommendation',
      pillar: 'summary',
      label: 'סיכום תיק והמלצת יחידה חוקרת',
      description: 'סיכום ראיות, מסקנות והמלצת הגוף החוקר או התביעה.',
      severity: 'critical',
      status: 'present',
      reason: 'אותר סימן מתאים בתיק שהועלה.',
      applicability: 'always',
      evidenceStrength: 'strong',
      evidence: [{ source: 'raw_text', signal: 'סיכום חקירה', snippet: 'סיכום חקירה הועבר לעיון התביעה.' }],
      sourceRefs: [{ guideline: 'client_protocol', section: 'Summary and background' }]
    }
  ]
});
