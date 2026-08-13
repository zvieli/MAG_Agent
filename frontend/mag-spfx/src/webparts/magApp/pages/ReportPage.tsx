import { DefaultButton, PrimaryButton, Spinner, Pivot, PivotItem } from '@fluentui/react';
import * as React from 'react';
import { MarkdownView } from '../components/MarkdownView';
import styles from '../components/MagApp.module.scss';
import { CommunitySummariesResult, ProtocolCompletenessItem, ProtocolCompletenessResult, ReportResult } from '../domain/types';
import { IReportService } from '../services/IAppServices';
import { requestErrorText, sourceLabel } from '../domain/localization';
import { isEntraAuthenticationError } from '../auth/AuthenticationService';


export interface ReportPageProps {
  docId?: string;
  reportService: IReportService;
}

const optionalNonAuthenticationResult = async <T,>(request: Promise<T>): Promise<T | undefined> => {
  try {
    return await request;
  } catch (error) {
    if (isEntraAuthenticationError(error)) throw error;
    return undefined;
  }
};

interface ParsedStats {
  nodes: number;
  edges: number;
  communities: number;
}

interface KeyActor {
  name: string;
  degree: string;
  betweenness: string;
}

interface GodNode {
  name: string;
  degree: string;
  role: string;
  evidence: string;
}

interface Connection {
  source: string;
  target: string;
  details: string;
}

function parseReportMarkdown(md: string) {
  const stats: ParsedStats = { nodes: 0, edges: 0, communities: 0 };
  const keyActors: KeyActor[] = [];
  const godNodes: GodNode[] = [];
  const connections: Connection[] = [];

  // Parse stats
  const nodesMatch = md.match(/Total Entities \(Nodes\):\s*(\d+)/i);
  if (nodesMatch) stats.nodes = parseInt(nodesMatch[1], 10);
  const edgesMatch = md.match(/Total Relations \(Edges\):\s*(\d+)/i);
  if (edgesMatch) stats.edges = parseInt(edgesMatch[1], 10);
  const commsMatch = md.match(/Identified Communities:\s*(\d+)/i);
  if (commsMatch) stats.communities = parseInt(commsMatch[1], 10);

  // Parse Key Actors Table
  const actorSection = md.match(/High Centrality Entities \(Key Actors\)\s*([\s\S]*?)(?=\n\n|\n[A-Za-z#]|$)/);
  if (actorSection) {
    const lines = actorSection[1].split('\n');
    for (const line of lines) {
      if (line.includes('Degree Centrality') || line.trim() === '' || line.startsWith('Entity')) {
        continue;
      }
      const parts = line.split(/\t|  +/);
      if (parts.length >= 2) {
        keyActors.push({
          name: parts[0].trim(),
          degree: parts[1].trim(),
          betweenness: parts[2] ? parts[2].trim() : '-'
        });
      }
    }
  }

  // Parse God Nodes (High Centrality)
  const godSection = md.match(/God Nodes \(High Centrality\)\s*([\s\S]*?)(?=\n\n|\n[A-Za-z#]|$)/);
  if (godSection) {
    const lines = godSection[1].split('\n');
    for (const line of lines) {
      if (line.trim() === '') continue;
      const match = line.match(/^\s*\*\s*(.+?)\s*\(Degree:\s*(\d+)\):\s*(.+?)\s*\|\s*(.+)$/i) || 
                    line.match(/^\s*(.+?)\s*\(Degree:\s*(\d+)\):\s*(.+?)\s*\|\s*(.+)$/i);
      if (match) {
        godNodes.push({
          name: match[1].trim().replace(/^\*\s*/, ''),
          degree: match[2].trim(),
          role: match[3].trim(),
          evidence: match[4].trim()
        });
      } else {
        const simpleMatch = line.match(/^\s*(.+?)\s*\(Degree:\s*(\d+)\):\s*(.+)$/i);
        if (simpleMatch) {
          godNodes.push({
            name: simpleMatch[1].trim().replace(/^\*\s*/, ''),
            degree: simpleMatch[2].trim(),
            role: simpleMatch[3].trim(),
            evidence: ''
          });
        }
      }
    }
  }

  // Parse Surprising Connections
  const connSection = md.match(/Surprising Connections \(Cross-Community\)\s*([\s\S]*?)(?=\n\n|\n[A-Za-z#]|$)/) || 
                      md.match(/Surprising Connections\s*([\s\S]*?)(?=\n\n|\n[A-Za-z#]|$)/);
  if (connSection) {
    const lines = connSection[1].split('\n');
    for (const line of lines) {
      if (line.trim() === '' || line.includes('Score') || line.includes('<->')) {
        const parts = line.split('<->');
        if (parts.length === 2) {
          const rightPart = parts[1].split('(');
          connections.push({
            source: parts[0].trim().replace(/^\*\s*/, ''),
            target: rightPart[0].trim(),
            details: rightPart[1] ? rightPart[1].replace(')', '').trim() : ''
          });
        }
      }
    }
  }

  return { stats, keyActors, godNodes, connections };
}

const communitySortValue = (communityId: string): number => {
  const match = communityId.match(/\d+/);
  return match ? parseInt(match[0], 10) : Number.MAX_SAFE_INTEGER;
};

const sortedCommunityEntries = (communities: CommunitySummariesResult | undefined): Array<[string, string]> => {
  if (!communities) return [];
  const summaries = communities.summaries || {};
  return Object.keys(summaries)
    .map((communityId): [string, string] => [communityId, summaries[communityId]])
    .filter((entry) => Boolean(entry[1] && entry[1].trim()))
    .sort(([a], [b]) => communitySortValue(a) - communitySortValue(b) || a.localeCompare(b));
};

const statusLabel = (status: string): string => {
  switch (status) {
    case 'present':
      return 'אותר';
    case 'missing':
      return 'חסר';
    case 'conditional_missing':
      return 'חסר מותנה';
    case 'not_applicable':
      return 'לא רלוונטי';
    default:
      return 'לא ידוע';
  }
};

const statusStyle = (status: string): React.CSSProperties => {
  if (status === 'present') {
    return { backgroundColor: '#d9f2e6', color: '#166534', borderColor: '#bbf7d0' };
  }
  if (status === 'missing') {
    return { backgroundColor: '#fde2e2', color: '#991b1b', borderColor: '#fecaca' };
  }
  if (status === 'conditional_missing') {
    return { backgroundColor: '#fff7db', color: '#8a6d3b', borderColor: '#fde68a' };
  }
  return { backgroundColor: '#e5eaf0', color: '#334e68', borderColor: '#cbd5e1' };
};

const evidenceStrengthLabel = (strength?: string): string => {
  if (strength === 'agent_verified') {
    return 'אותר ע"י סוכן חכם';
  }
  if (strength === 'strong') {
    return 'אינדיקציה חזקה';
  }
  if (strength === 'weak') {
    return 'אינדיקציה חלקית';
  }
  return '';
};

const evidenceStrengthStyle = (strength?: string): React.CSSProperties => {
  if (strength === 'agent_verified') {
    return { backgroundColor: '#e0f2fe', color: '#0369a1', borderColor: '#7dd3fc' };
  }
  if (strength === 'strong') {
    return { backgroundColor: '#ecfdf5', color: '#166534', borderColor: '#bbf7d0' };
  }
  if (strength === 'weak') {
    return { backgroundColor: '#fffbeb', color: '#92400e', borderColor: '#fde68a' };
  }
  return { backgroundColor: '#f1f5f9', color: '#475569', borderColor: '#cbd5e1' };
};

const groupedCompletenessItems = (items: ProtocolCompletenessItem[]): Record<string, ProtocolCompletenessItem[]> => {
  return items.reduce((acc, item) => {
    const key = item.pillar || 'general';
    acc[key] = acc[key] || [];
    acc[key].push(item);
    return acc;
  }, {} as Record<string, ProtocolCompletenessItem[]>);
};

export const ReportPage: React.FC<ReportPageProps> = ({ docId, reportService }) => {
  const [report, setReport] = React.useState<ReportResult | undefined>();
  const [communities, setCommunities] = React.useState<CommunitySummariesResult | undefined>();
  const [completeness, setCompleteness] = React.useState<ProtocolCompletenessResult | undefined>();
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | undefined>();
  const [verifyingAgent, setVerifyingAgent] = React.useState(false);
  const [verificationMessage, setVerificationMessage] = React.useState<string | undefined>();

  const loadReport = React.useCallback((forceRefresh = false): void => {
    if (!docId) return;
    setLoading(true);
    setError(undefined);
    Promise.all([
      optionalNonAuthenticationResult(reportService.getReport(docId, { forceRefresh })),
      optionalNonAuthenticationResult(reportService.getCommunitySummaries(docId, { forceRefresh })),
      optionalNonAuthenticationResult(reportService.getProtocolCompleteness(docId, { forceRefresh }))
    ])
      .then(([result, communityResult, completenessResult]) => {
        if (result) setReport(result);
        setCommunities(communityResult);
        setCompleteness(completenessResult);
        setLoading(false);
      })
      .catch((reportError) => setError(requestErrorText(reportError, 'אירעה שגיאה בטעינת הדו"ח.')))
      .then(() => setLoading(false));

  }, [docId, reportService]);

  const triggerAgentVerification = React.useCallback(async () => {
    if (!docId) return;
    setVerifyingAgent(true);
    setVerificationMessage('מפעיל אימות סוכן חכם ברקע...');
    try {
      const statusUrl = await reportService.verifyProtocolCompletenessWithAgent(docId);
      if (!statusUrl) {
        setVerificationMessage('אימות הסוכן הופעל.');
        loadReport(true);
        setVerifyingAgent(false);
        return;
      }

      const startTime = Date.now();
      const pollInterval = setInterval(async () => {
        try {
          if (Date.now() - startTime > 120000) {
            clearInterval(pollInterval);
            setVerifyingAgent(false);
            setVerificationMessage('אימות הסוכן מתמשך ברקע.');
            loadReport(true);
            return;
          }
          const status = await reportService.checkVerificationStatus(statusUrl);
          const state = (status.runtimeStatus || '').toLowerCase();
          if (state === 'completed' || state === 'failed' || state === 'terminated') {
            clearInterval(pollInterval);
            setVerifyingAgent(false);
            setVerificationMessage(state === 'completed' ? 'אימות הסוכן הושלם בהצלחה!' : 'אימות הסוכן הסתיים.');
            loadReport(true);
          } else {
            setVerificationMessage(`סוכן חכם סורק ומאמת חומר חקירה... (${status.runtimeStatus || 'בביצוע'})`);
          }
        } catch (statusError) {
          clearInterval(pollInterval);
          setVerifyingAgent(false);
          if (isEntraAuthenticationError(statusError)) {
            setError(requestErrorText(statusError, 'אירעה שגיאת אימות בבדיקת מצב הסוכן.'));
            setVerificationMessage(undefined);
            return;
          }
          loadReport(true);
        }
      }, 2500);
    } catch (err) {
      setVerifyingAgent(false);
      const message = requestErrorText(err, 'אירעה שגיאה בהפעלת אימות סוכן.');
      setVerificationMessage(message);
      if (isEntraAuthenticationError(err)) setError(message);
    }
  }, [docId, reportService, loadReport]);

  React.useEffect(() => {
    loadReport();
  }, [loadReport]);


  if (!docId) {
    return <div className={styles.emptyState}>יש להשלים עיבוד תיק לפני פתיחת דו"ח הניתוח.</div>;
  }

  const parsed = report ? parseReportMarkdown(report.markdown) : null;
  const communityEntries = sortedCommunityEntries(communities);
  const completenessGroups = groupedCompletenessItems(completeness?.items || []);
  const completenessSummary = completeness?.summary || {};
  const criticalMissing = completenessSummary.criticalMissing || 0;
  const criticalReviewRequired = completenessSummary.criticalReviewRequired || 0;
  const conditionalMissing = completenessSummary.conditionalMissing || 0;

  return (
    <section className={styles.panel}>
      <div className={styles.panelHeader}>
        <h2>דו"ח ניתוח חקירתי</h2>
        <div className={styles.headerActions}>
          {report?.cache?.fromCache && <span className={styles.cacheBadge}>נטען מהמטמון</span>}
          {report && <span>{sourceLabel(report.source)} / {new Date(report.updatedAt).toLocaleString('he-IL')}</span>}
          <DefaultButton text="רענן" iconProps={{ iconName: 'Refresh' }} onClick={() => loadReport(true)} disabled={loading} />
        </div>
      </div>
      <div className={styles.infoBanner} style={{ marginBottom: '16px' }}>
        💡 <strong>ממשק ניתוח חקירתי:</strong> מנגיש את תובנות גרף הידע לשימוש בהליכים משפטיים וחקירות נגדיות.
        {completeness && (criticalMissing > 0 || criticalReviewRequired > 0 || conditionalMissing > 0) && (
          <span style={{ display: 'block', marginTop: '6px', fontWeight: 600, color: criticalMissing > 0 ? '#991b1b' : '#854d0e' }}>
            בקרת שלמות תיק: {criticalMissing} חוסרים קריטיים, {criticalReviewRequired} בדיקות קריטיות נדרשות ו-{conditionalMissing} פריטים מותנים לא אותרו.
          </span>
        )}
      </div>
      {loading && <Spinner label={'טוען דו"ח'} />}
      {error && <div className={styles.errorBox}>{error}</div>}
      
      {report && parsed && (
        <>
          <div className={styles.kpiGrid}>
            <div className={styles.kpiCard}>
              <span className={styles.kpiLabel}>אחוז כיסוי תיק</span>
              <span className={styles.kpiValue} style={{ color: (completenessSummary.coveragePercent || 0) < 50 ? '#dc2626' : '#059669' }}>
                {completenessSummary.coveragePercent || 0}%
              </span>
            </div>
            <div className={styles.kpiCard}>
              <span className={styles.kpiLabel}>גורמי מפתח בגרף</span>
              <span className={styles.kpiValue}>{parsed.godNodes.length}</span>
            </div>
            <div className={styles.kpiCard}>
              <span className={styles.kpiLabel}>קשרים צולבים</span>
              <span className={styles.kpiValue}>{parsed.connections.length}</span>
            </div>
            <div className={styles.kpiCard}>
              <span className={styles.kpiLabel}>חוסרים קריטיים</span>
              <span className={styles.kpiValue} style={{ color: criticalMissing > 0 ? '#dc2626' : '#64748b' }}>
                {criticalMissing}
              </span>
            </div>
          </div>

          <Pivot aria-label="Investigation Report Tabs" style={{ marginTop: '12px' }}>
          <PivotItem headerText="גורמי מפתח וביסוס ראייתי">
            {parsed.godNodes.length === 0 ? (
              <div className={styles.emptyState} style={{ padding: '24px' }}>לא נמצאו גורמי מפתח מפורטים בדו"ח.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '20px', marginTop: '16px' }}>
                <div className={styles.actorCardGrid}>
                  {parsed.godNodes.map((node, idx) => (
                    <div key={idx} className={styles.actorCard}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                        <h3 style={{ margin: 0, color: '#0078d4', fontSize: '18px', fontWeight: '600' }}>
                          <MarkdownView markdown={node.name} variant="inline" />
                        </h3>
                        <span style={{
                          backgroundColor: '#fde7e9',
                          color: '#a80000',
                          padding: '2px 10px',
                          borderRadius: '12px',
                          fontSize: '11px',
                          fontWeight: '600'
                        }}>דרגת קשרים: {node.degree}</span>
                      </div>
                      <div style={{ margin: '0 0 12px 0', fontSize: '14px', color: '#323130', lineHeight: '1.5' }}>
                        <MarkdownView markdown={`**תפקיד ומשמעות:** ${node.role}`} />
                      </div>
                      {node.evidence && (
                        <div style={{
                          borderRight: '3px solid #0078d4',
                          paddingRight: '8px',
                          fontSize: '13px',
                          color: '#605e5c',
                          backgroundColor: '#f3f2f1',
                          padding: '8px',
                          borderRadius: '4px',
                          fontStyle: 'italic'
                        }}>
                          <MarkdownView markdown={`**ציטוט מפתח:** "${node.evidence}"`} />
                        </div>
                      )}
                    </div>
                  ))}
                </div>

                <div style={{ overflowX: 'auto', marginTop: '12px' }}>
                  <h4 style={{ margin: '0 0 12px', fontSize: '16px', color: '#162033' }}>טבלת מטריצת ביסוס ראייתי</h4>
                  <table className={styles.evidentiaryMatrix}>
                    <thead>
                      <tr>
                        <th>גורם מעורב</th>
                        <th>דרגת קשרים</th>
                        <th>חשדות עובדתיים ותפקיד</th>
                        <th>ביסוס ראייתי מקורי</th>
                      </tr>
                    </thead>
                    <tbody>
                      {parsed.godNodes.map((node, idx) => (
                        <tr key={idx}>
                          <td style={{ fontWeight: '600', color: '#0078d4' }}>
                            <MarkdownView markdown={node.name} variant="inline" />
                          </td>
                          <td>{node.degree}</td>
                          <td><MarkdownView markdown={node.role} /></td>
                          <td style={{ fontSize: '13px', color: '#605e5c', fontStyle: 'italic' }}>
                            <MarkdownView markdown={node.evidence ? `"${node.evidence}"` : 'ביסוס ישיר בגרף'} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </PivotItem>

          <PivotItem headerText="סתירות וקשרים צולבים">
            <div style={{ marginTop: '16px' }}>
              <div style={{
                backgroundColor: '#fff4ce',
                color: '#323130',
                padding: '12px 16px',
                borderRadius: '6px',
                marginBottom: '16px',
                borderRight: '4px solid #f3b13e',
                fontSize: '14px',
                lineHeight: '1.5'
              }}>
                💡 <strong>עוזר הכנה לחקירות:</strong> גורמים וקשרים אלו מגשרים בין קהילות עניין נפרדות בפרשה. מומלץ להתמקד בקשרים אלו במהלך חקירות נגדיות כדי לברר תיאום גרסאות או סתירות עובדתיות.
              </div>
              
              {parsed.connections.length === 0 ? (
                <div className={styles.emptyState} style={{ padding: '24px' }}>לא זוהו קשרים צולבים מיוחדים בדו"ח.</div>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '14px' }}>
                  {parsed.connections.map((conn, idx) => (
                    <div key={idx} style={{
                      border: '1px solid #e2e8f0',
                      borderRadius: '8px',
                      padding: '14px',
                      backgroundColor: '#f8fafc',
                      boxShadow: '0 1px 3px rgba(0,0,0,0.02)'
                    }}>
                      <div style={{ fontWeight: '600', color: '#1e293b', marginBottom: '6px', fontSize: '14px' }}>
                        <MarkdownView markdown={conn.source} variant="inline" /> <span style={{ color: '#0078d4', padding: '0 4px' }}>⇄</span> <MarkdownView markdown={conn.target} variant="inline" />
                      </div>
                      <div style={{ fontSize: '13px', color: '#475569', lineHeight: '1.4' }}>
                        <MarkdownView markdown={conn.details || 'קשר צולב מפתח בגרף'} />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </PivotItem>

          <PivotItem headerText="סיכומי קהילות">
            <div style={{ marginTop: '16px' }}>
              {communityEntries.length === 0 ? (
                <div className={styles.emptyState} style={{ padding: '24px' }}>לא נמצאו סיכומי קהילות עבור התיק.</div>
              ) : (
                <div className={styles.actorCardGrid}>
                  {communityEntries.map(([communityId, summary]) => {
                    const parentId = communities?.hierarchy?.[communityId];
                    return (
                      <div key={communityId} className={styles.actorCard}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', alignItems: 'center', marginBottom: '10px' }}>
                          <h3 style={{ margin: 0, color: '#0f766e', fontSize: '17px', fontWeight: 650 }}>
                            קהילה {communityId}
                          </h3>
                          {parentId && (
                            <span style={{ color: '#52606d', fontSize: '12px', whiteSpace: 'nowrap' }}>
                              תחת {parentId}
                            </span>
                          )}
                        </div>
                        <div style={{ margin: 0, color: '#1e293b', lineHeight: 1.6, fontSize: '14px' }}>
                          <MarkdownView markdown={summary} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </PivotItem>

          <PivotItem headerText="בקרת שלמות תיק">
            <div style={{ marginTop: '16px' }}>
              {!completeness ? (
                <div className={styles.emptyState} style={{ padding: '24px' }}>לא נמצא דו"ח בקרת שלמות עבור התיק.</div>
              ) : (
                <>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px', flexWrap: 'wrap', gap: '10px' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '10px', flex: '1 1 auto' }}>
                      <div className={styles.graphMetric}>
                        <span>כיסוי</span>
                        <strong>{completenessSummary.coveragePercent || 0}%</strong>
                      </div>
                      <div className={styles.graphMetric}>
                        <span>אותרו</span>
                        <strong>{completenessSummary.present || 0}</strong>
                      </div>
                      <div className={styles.graphMetric}>
                        <span>חסרים</span>
                        <strong>{completenessSummary.missing || 0}</strong>
                      </div>
                      <div className={styles.graphMetric}>
                        <span>בדיקות קריטיות</span>
                        <strong>{completenessSummary.criticalReviewRequired || 0}</strong>
                      </div>
                      <div className={styles.graphMetric}>
                        <span>מותנים</span>
                        <strong>{completenessSummary.conditionalMissing || 0}</strong>
                      </div>
                    </div>
                    <div>
                      <PrimaryButton
                        text={verifyingAgent ? 'מאמת חוסרים...' : 'הפעל אימות סוכן חכם'}
                        iconProps={{ iconName: 'Robot' }}
                        onClick={triggerAgentVerification}
                        disabled={verifyingAgent || loading}
                      />
                    </div>
                  </div>
                  {verificationMessage && (
                    <div className={styles.reportOpinion} style={{ marginBottom: '14px', backgroundColor: '#e0f2fe', borderColor: '#0284c7', color: '#0369a1' }}>
                      {verifyingAgent && <Spinner label={verificationMessage} labelPosition="right" style={{ display: 'inline-flex', marginRight: '8px' }} />}
                      {!verifyingAgent && verificationMessage}
                    </div>
                  )}
                  {completeness.disclaimer && (
                    <div className={styles.reportOpinion} style={{ marginBottom: '14px' }}>
                      {completeness.disclaimer}
                    </div>
                  )}
                  {Object.keys(completenessGroups).map((pillar) => (
                    <section key={pillar} style={{ marginTop: '18px' }}>
                      <h3 style={{ margin: '0 0 10px', fontSize: '17px', color: '#172033' }}>{pillar}</h3>
                      <div className={styles.actorCardGrid} style={{ marginTop: 0 }}>
                        {completenessGroups[pillar].map((item) => (
                          <div key={item.id} className={styles.actorCard}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', alignItems: 'flex-start', marginBottom: '10px' }}>
                              <h4 style={{ margin: 0, fontSize: '15px', color: '#172033' }}>{item.label}</h4>
                              <span style={{
                                display: 'inline-flex',
                                alignItems: 'center',
                                minHeight: '24px',
                                padding: '0 8px',
                                border: '1px solid',
                                borderRadius: '999px',
                                fontSize: '12px',
                                fontWeight: 650,
                                whiteSpace: 'nowrap',
                                ...statusStyle(item.status)
                              }}>
                                {statusLabel(item.status)}
                              </span>
                            </div>
                            {evidenceStrengthLabel(item.evidenceStrength) && (
                              <div style={{ marginBottom: '8px' }}>
                                <span style={{
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  minHeight: '22px',
                                  padding: '0 7px',
                                  border: '1px solid',
                                  borderRadius: '999px',
                                  fontSize: '11px',
                                  fontWeight: 650,
                                  ...evidenceStrengthStyle(item.evidenceStrength)
                                }}>
                                  {evidenceStrengthLabel(item.evidenceStrength)}
                                </span>
                              </div>
                            )}
                            <div style={{ color: '#52606d', fontSize: '13px', lineHeight: 1.45, marginBottom: '8px' }}>
                              {item.description}
                            </div>
                            <div style={{ color: '#334e68', fontSize: '13px', lineHeight: 1.45 }}>
                              {item.reason}
                            </div>
                            {item.applicabilityEvidence?.snippet && item.status !== 'present' && (
                              <div style={{ marginTop: '8px', color: '#475569', fontSize: '12px', lineHeight: 1.45 }}>
                                <strong>למה נבדק: </strong>{item.applicabilityEvidence.snippet}
                              </div>
                            )}
                            {item.reviewNote && (
                              <div style={{ marginTop: '8px', color: '#92400e', fontSize: '12px', lineHeight: 1.45 }}>
                                {item.reviewNote}
                              </div>
                            )}
                            {item.agentVerification && (
                              <div style={{
                                marginTop: '10px',
                                padding: '8px 10px',
                                borderRight: '3px solid #0284c7',
                                backgroundColor: '#f0f9ff',
                                color: '#0c4a6e',
                                fontSize: '12px',
                                lineHeight: 1.45,
                                borderRadius: '4px'
                              }}>
                                <strong>אימות סוכן חכם ({item.agentVerification.verifiedByAgent ? 'מאומת' : 'לא אותר'}): </strong>
                                {item.agentVerification.explanation}
                                {item.agentVerification.verbatimQuote && (
                                  <div style={{ marginTop: '4px', fontStyle: 'italic', color: '#0369a1' }}>
                                    "{item.agentVerification.verbatimQuote}" ({item.agentVerification.sourceLocation || 'חומר התיק'})
                                  </div>
                                )}
                              </div>
                            )}
                            {item.evidence?.[0] && (
                              <div style={{ marginTop: '10px', padding: '8px', borderRight: '3px solid #0f766e', backgroundColor: '#f0fdfa', color: '#134e4a', fontSize: '12px', lineHeight: 1.45 }}>
                                <strong>{item.evidence[0].signal}: </strong>{item.evidence[0].snippet}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </section>
                  ))}
                </>
              )}
            </div>
          </PivotItem>
        </Pivot>
        </>
      )}
    </section>
  );
};
