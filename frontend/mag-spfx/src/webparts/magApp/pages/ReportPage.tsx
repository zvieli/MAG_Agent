import { DefaultButton, Spinner, Pivot, PivotItem } from '@fluentui/react';
import * as React from 'react';
import { MarkdownView } from '../components/MarkdownView';
import styles from '../components/MagApp.module.scss';
import { CommunitySummariesResult, ReportResult } from '../domain/types';
import { IReportService } from '../services/IAppServices';
import { errorText, sourceLabel } from '../domain/localization';

export interface ReportPageProps {
  docId?: string;
  reportService: IReportService;
}

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

export const ReportPage: React.FC<ReportPageProps> = ({ docId, reportService }) => {
  const [report, setReport] = React.useState<ReportResult | undefined>();
  const [communities, setCommunities] = React.useState<CommunitySummariesResult | undefined>();
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | undefined>();

  const loadReport = React.useCallback((forceRefresh = false): void => {
    if (!docId) return;
    setLoading(true);
    setError(undefined);
    Promise.all([
      reportService.getReport(docId, { forceRefresh }),
      reportService.getCommunitySummaries(docId, { forceRefresh }).catch(() => undefined)
    ])
      .then(([result, communityResult]) => {
        setReport(result);
        setCommunities(communityResult);
        setLoading(false);
      })
      .catch((reportError) => setError(reportError instanceof Error ? errorText(reportError.message) : 'אירעה שגיאה בטעינת הדו"ח.'))
      .then(() => setLoading(false));
  }, [docId, reportService]);

  React.useEffect(() => {
    loadReport();
  }, [loadReport]);

  if (!docId) {
    return <div className={styles.emptyState}>יש להשלים עיבוד תיק לפני פתיחת דו"ח הניתוח.</div>;
  }

  const parsed = report ? parseReportMarkdown(report.markdown) : null;
  const communityEntries = sortedCommunityEntries(communities);

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
      <div className={styles.reportOpinion} style={{ marginBottom: '16px', fontSize: '14px', color: '#52606d' }}>
        ממשק אינטראקטיבי המנגיש את תובנות גרף הידע מתוך התיק לשימוש בהליכים משפטיים וחקירות נגדיות.
      </div>
      {loading && <Spinner label={'טוען דו"ח'} />}
      {error && <div className={styles.errorBox}>{error}</div>}
      
      {report && parsed && (
        <Pivot aria-label="Investigation Report Tabs" style={{ marginTop: '12px' }}>
          <PivotItem headerText="פרופיל גורמי מפתח">
            {parsed.godNodes.length === 0 ? (
              <div className={styles.emptyState} style={{ padding: '24px' }}>לא נמצאו גורמי מפתח מפורטים בדו"ח.</div>
            ) : (
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
            )}
          </PivotItem>

          <PivotItem headerText="מטריצת ביסוס ראייתי">
            <div style={{ overflowX: 'auto' }}>
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

          <PivotItem headerText='הדו"ח המלא'>
            <div style={{ marginTop: '16px' }}>
              <MarkdownView markdown={report.markdown} />
            </div>
          </PivotItem>
        </Pivot>
      )}
    </section>
  );
};
