import { DefaultButton, SearchBox, Spinner } from '@fluentui/react';
import * as React from 'react';
import { GraphCanvas } from '../components/GraphCanvas';
import styles from '../components/MagApp.module.scss';
import { GraphEdge, GraphNode, GraphQueryResult } from '../domain/types';
import { IGraphService } from '../services/IAppServices';
import { errorText, nodeTypeLabel, relationLabel, sourceLabel } from '../domain/localization';
import { communityColor, confidenceLabel, connectedNodeCount, graphCommunities } from '../domain/graphVisuals';
import { GraphCanvasCommand } from '../components/GraphCanvas';
import { displayName } from '../domain/displayNames';

export interface GraphPageProps {
  docId?: string;
  graphService: IGraphService;
}

type GraphViewMode = 'full' | 'filters' | 'details';

export const GraphPage: React.FC<GraphPageProps> = ({ docId, graphService }) => {
  const [graph, setGraph] = React.useState<GraphQueryResult | undefined>();
  const [search, setSearch] = React.useState('');
  const [selectedNode, setSelectedNode] = React.useState<GraphNode | undefined>();
  const [selectedEdge, setSelectedEdge] = React.useState<GraphEdge | undefined>();
  const [activeCommunity, setActiveCommunity] = React.useState<string | undefined>();
  const [activeType, setActiveType] = React.useState<string | undefined>();
  const [neighborsOnly, setNeighborsOnly] = React.useState(false);
  const [viewMode, setViewMode] = React.useState<GraphViewMode>('full');
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | undefined>();
  const [canvasCommand, setCanvasCommand] = React.useState<GraphCanvasCommand | undefined>();

  const loadGraph = React.useCallback(async (forceRefresh = false) => {
    if (!docId) return;
    setLoading(true);
    setError(undefined);
    try {
      setGraph(await graphService.getGraph({ docId, search, limit: 250, depth: 2 }, { forceRefresh }));
    } catch (loadError) {
      setError(loadError instanceof Error ? errorText(loadError.message) : 'אירעה שגיאה בטעינת גרף הידע.');
    } finally {
      setLoading(false);
    }
  }, [docId, graphService, search]);

  React.useEffect(() => {
    loadGraph().catch(() => undefined);
  }, [loadGraph]);

  const issueCanvasCommand = (type: GraphCanvasCommand['type']): void => {
    if (type === 'clear') {
      setSelectedNode(undefined);
      setSelectedEdge(undefined);
    }
    setCanvasCommand({ type, nonce: Date.now() });
  };

  const communities = React.useMemo(() => (graph ? graphCommunities(graph.nodes) : []), [graph]);
  const centralActors = React.useMemo(
    () => (graph ? [...graph.nodes].sort((left, right) => (right.degree || 0) - (left.degree || 0)).slice(0, 5) : []),
    [graph]
  );
  const nodeTypes = React.useMemo(() => (graph ? [...graph.metadata.nodeTypes].sort() : []), [graph]);
  const selectedRelationships = React.useMemo(
    () => (selectedNode && graph ? graph.edges.filter((edge) => edge.source === selectedNode.id || edge.target === selectedNode.id).slice(0, 6) : []),
    [graph, selectedNode]
  );
  const selectedConnectedCount = React.useMemo(
    () => connectedNodeCount(selectedNode, graph?.edges || []),
    [graph, selectedNode]
  );
  const filteredGraph = React.useMemo<GraphQueryResult | undefined>(() => {
    if (!graph) return undefined;

    let visibleNodeIds = new Set(
      graph.nodes
        .filter((node) => !activeCommunity || (node.communityId || '0') === activeCommunity)
        .filter((node) => !activeType || node.type === activeType)
        .map((node) => node.id)
    );

    if (neighborsOnly && selectedNode) {
      const neighborhood = new Set<string>([selectedNode.id]);
      for (const edge of graph.edges) {
        if (edge.source === selectedNode.id) neighborhood.add(edge.target);
        if (edge.target === selectedNode.id) neighborhood.add(edge.source);
      }
      visibleNodeIds = new Set(Array.from(visibleNodeIds).filter((nodeId) => neighborhood.has(nodeId)));
    }

    const nodes = graph.nodes.filter((node) => visibleNodeIds.has(node.id));
    const edges = graph.edges.filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target));

    return {
      ...graph,
      nodes,
      edges,
      metadata: {
        ...graph.metadata,
        nodeCount: nodes.length,
        edgeCount: edges.length,
        nodeTypes: Array.from(new Set(nodes.map((node) => node.type)))
      }
    };
  }, [activeCommunity, activeType, graph, neighborsOnly, selectedNode]);

  const selectCentralActor = (node: GraphNode): void => {
    setSelectedNode(node);
    setSelectedEdge(undefined);
    setNeighborsOnly(true);
    setViewMode('details');
  };

  const clearFilters = (): void => {
    setActiveCommunity(undefined);
    setActiveType(undefined);
    setNeighborsOnly(false);
    issueCanvasCommand('clear');
  };

  const endpointLabel = (id: string, explicitLabel?: string): string => {
    const node = graph?.nodes.find((candidate) => candidate.id === id);
    return displayName(explicitLabel || node?.label, id);
  };

  if (!docId) {
    return <div className={styles.emptyState}>יש להשלים עיבוד תיק לפני פתיחת גרף הידע.</div>;
  }

  return (
    <section className={styles.investigationBoard}>
      <div className={styles.investigationHero}>
        <div>
          <span>מרחב חקירה</span>
          <h2>גרף חקירה</h2>
          <p>מיפוי ישויות, קשרים וקהילות מתוך תיק המעובד בגרמלין.</p>
        </div>
        <div className={styles.headerActions}>
          {graph?.cache?.fromCache && <span className={styles.cacheBadge}>נטען מהמטמון</span>}
          <DefaultButton text="רענן נתונים" iconProps={{ iconName: 'Refresh' }} onClick={() => loadGraph(true)} disabled={loading} />
        </div>
      </div>
      {graph && (
        <div className={styles.graphSummaryGrid}>
          <div className={styles.graphMetric}>
            <span>ישויות</span>
            <strong>{graph.metadata.nodeCount}</strong>
          </div>
          <div className={styles.graphMetric}>
            <span>קשרים</span>
            <strong>{graph.metadata.edgeCount}</strong>
          </div>
          <div className={styles.graphMetric}>
            <span>קהילות</span>
            <strong>{communities.length}</strong>
          </div>
          <div className={styles.graphMetricWide}>
            <span>גורמים מרכזיים</span>
            <strong>{centralActors.slice(0, 3).map((actor) => displayName(actor.label, actor.id)).join(' · ') || 'לא זוהו'}</strong>
          </div>
        </div>
      )}
      <div className={styles.toolbar}>
        <SearchBox placeholder="סינון לפי ישות או קשר" value={search} onChange={(_, value) => setSearch(value || '')} onSearch={loadGraph} />
        {graph && <span>{graph.metadata.nodeCount} צמתים / {graph.metadata.edgeCount} קשרים / מקור: {sourceLabel(graph.source)}</span>}
      </div>
      {graph && (
        <div className={styles.graphControls}>
          <DefaultButton text="התאם למסך" iconProps={{ iconName: 'FitPage' }} onClick={() => issueCanvasCommand('fit')} />
          <DefaultButton text="פריסה מחדש" iconProps={{ iconName: 'Processing' }} onClick={() => issueCanvasCommand('layout')} />
          <DefaultButton text={neighborsOnly ? 'הצג את כל הגרף' : 'שכנים בלבד'} iconProps={{ iconName: 'Link' }} onClick={() => setNeighborsOnly((value) => !value)} disabled={!selectedNode} />
          <DefaultButton text="נקה תצוגה" iconProps={{ iconName: 'Clear' }} onClick={clearFilters} />
        </div>
      )}
      {loading && <Spinner label="טוען גרף ידע" />}
      {error && <div className={styles.errorBox}>{error}</div>}
      {graph && filteredGraph && (
        <div className={styles.graphFocusLayout}>
          <main className={styles.graphStage}>
            <div className={styles.graphStageHeader}>
              <div>
                <strong>{filteredGraph.metadata.nodeCount} ישויות מוצגות</strong>
                <span>{filteredGraph.metadata.edgeCount} קשרים בתצוגה הנוכחית</span>
              </div>
              <div className={styles.confidenceLegend}>
                <span className={styles.legendLineStrong}>גבוה</span>
                <span className={styles.legendLineMedium}>בינוני</span>
                <span className={styles.legendLineWeak}>נמוך</span>
              </div>
            </div>
            {filteredGraph.nodes.length > 0 ? (
              <GraphCanvas
                graph={filteredGraph}
                command={canvasCommand}
                onSelectNode={(node) => {
                  setSelectedNode(node);
                  if (node) {
                    setSelectedEdge(undefined);
                    setViewMode('details');
                  }
                }}
                onSelectEdge={(edge) => {
                  setSelectedEdge(edge);
                  if (edge) {
                    setSelectedNode(undefined);
                    setViewMode('details');
                  }
                }}
              />
            ) : (
              <div className={styles.emptyGraphState}>לא נמצאו ישויות להצגה במסננים הנוכחיים.</div>
            )}
          </main>
          <div className={styles.graphModeTabs}>
            <button className={viewMode === 'full' ? styles.graphModeActive : styles.graphModeButton} type="button" onClick={() => { setViewMode('full'); issueCanvasCommand('fit'); }}>
              גרף מלא
            </button>
            <button className={viewMode === 'filters' ? styles.graphModeActive : styles.graphModeButton} type="button" onClick={() => setViewMode('filters')}>
              סינון וחקירה
            </button>
            <button className={viewMode === 'details' ? styles.graphModeActive : styles.graphModeButton} type="button" onClick={() => setViewMode('details')}>
              פרטי רכיב
            </button>
          </div>
          {viewMode === 'filters' && (
            <aside className={styles.filterRailWide}>
            <section>
              <h3>קהילות</h3>
              <button className={!activeCommunity ? styles.filterChipActive : styles.filterChip} type="button" onClick={() => setActiveCommunity(undefined)}>
                כל הקהילות
              </button>
              {communities.map((communityId) => (
                <button
                  key={communityId}
                  className={activeCommunity === communityId ? styles.filterChipActive : styles.filterChip}
                  type="button"
                  onClick={() => setActiveCommunity(communityId)}
                >
                  <i style={{ background: communityColor(communityId) }} />
                  קהילה {communityId}
                </button>
              ))}
            </section>
            <section>
              <h3>סוגי ישויות</h3>
              <button className={!activeType ? styles.filterChipActive : styles.filterChip} type="button" onClick={() => setActiveType(undefined)}>
                כל הסוגים
              </button>
              {nodeTypes.map((type) => (
                <button
                  key={type}
                  className={activeType === type ? styles.filterChipActive : styles.filterChip}
                  type="button"
                  onClick={() => setActiveType(type)}
                >
                  {nodeTypeLabel(type)}
                </button>
              ))}
            </section>
            <section>
              <h3>גורמים מרכזיים</h3>
              <div className={styles.actorList}>
                {centralActors.map((actor) => (
                  <button key={actor.id} type="button" onClick={() => selectCentralActor(actor)}>
                    <strong>{displayName(actor.label, actor.id)}</strong>
                    <span>{nodeTypeLabel(actor.type)} · {actor.degree ?? 0} קשרים</span>
                    <small className={styles.ltrValue}>{actor.id}</small>
                  </button>
                ))}
              </div>
            </section>
            </aside>
          )}
          {viewMode === 'details' && (
          <aside className={styles.inspectorWide}>
            <div className={styles.inspectorHeader}>
              <span>ניתוח רכיב</span>
              <h3>פרטי חקירה</h3>
            </div>
            {!selectedNode && !selectedEdge && <p>בחר ישות או קשר כדי להציג פרטים, קשרים ישירים ומקור תומך.</p>}
            {selectedNode && (
              <>
                <strong>{displayName(selectedNode.label, selectedNode.id)}</strong>
                <span>{nodeTypeLabel(selectedNode.type)} · קהילה {selectedNode.communityId || '0'}</span>
                <p>{selectedNode.description || 'אין תיאור זמין.'}</p>
                <dl className={styles.inspectorFacts}>
                  <dt>דרגת מרכזיות</dt>
                  <dd>{selectedNode.degree ?? 0}</dd>
                  <dt>קשרים ישירים</dt>
                  <dd>{selectedConnectedCount}</dd>
                  {selectedNode.properties?.source_file && (
                    <>
                      <dt>קובץ מקור</dt>
                      <dd style={{ fontSize: '11px', wordBreak: 'break-all', direction: 'ltr', textAlign: 'left' }}>
                        {String(selectedNode.properties.source_file)}
                      </dd>
                    </>
                  )}
                  {selectedNode.properties?.source_location && (
                    <>
                      <dt>מיקום מקור</dt>
                      <dd style={{ fontSize: '11px' }}>{String(selectedNode.properties.source_location)}</dd>
                    </>
                  )}
                </dl>
                {(() => {
                  const aliasPreviewStr = selectedNode.properties?.alias_preview as string;
                  if (!aliasPreviewStr) return null;
                  try {
                    const aliases = JSON.parse(aliasPreviewStr);
                    if (!Array.isArray(aliases) || aliases.length === 0) return null;
                    return (
                      <div style={{ marginTop: '14px', padding: '10px', borderRadius: '6px', background: '#f8fafc', border: '1px solid #e2e8f0', direction: 'rtl', textAlign: 'right' }}>
                        <h4 style={{ margin: '0 0 6px 0', fontSize: '13px', color: '#0f766e', fontWeight: 650 }}>שמות נרדפים / ישויות שמוזגו ({selectedNode.properties?.alias_count || aliases.length})</h4>
                        <ul style={{ margin: 0, paddingRight: '16px', fontSize: '12px', color: '#334155', listStyleType: 'disc' }}>
                          {aliases.map((alias: any, idx: number) => (
                            <li key={idx} style={{ marginBottom: '4px' }}>
                              <strong>{alias.label}</strong> {alias.id !== alias.label && <span style={{ color: '#64748b', fontSize: '11px' }}>({alias.id})</span>}
                              {alias.source_location && <div style={{ fontSize: '11px', color: '#64748b', marginRight: '4px' }}>מיקום: {alias.source_location}</div>}
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  } catch (e) {
                    return null;
                  }
                })()}
                {selectedRelationships.length > 0 && (
                  <div className={styles.relationshipList}>
                    <h4>קשרים קרובים</h4>
                    {selectedRelationships.map((edge) => (
                      <button key={edge.id} type="button" onClick={() => { setSelectedEdge(edge); setSelectedNode(undefined); setNeighborsOnly(false); }}>
                        <strong>{relationLabel(edge.relation)}</strong>
                        <span>{endpointLabel(edge.source, edge.sourceLabel)} ← {endpointLabel(edge.target, edge.targetLabel)}</span>
                        {edge.description && <p style={{ margin: '4px 0 0', fontSize: '12px', color: '#52606d', fontStyle: 'italic', direction: 'rtl', textAlign: 'right' }}>{edge.description}</p>}
                      </button>
                    ))}
                  </div>
                )}
                <div style={{ marginTop: '12px', marginBottom: '8px' }}>
                  <DefaultButton
                    text={`שאל את הסוכן על ${displayName(selectedNode.label, selectedNode.id)}`}
                    iconProps={{ iconName: 'Chat' }}
                    onClick={() => {
                      if (docId) {
                        try {
                          const key = `mag:chat:${docId}`;
                          const existingRaw = window.sessionStorage.getItem(key);
                          const parsed = existingRaw ? JSON.parse(existingRaw) : { messages: [], draft: '' };
                          parsed.draft = `סקור את הראיות והפעילות הקשורות ל-${displayName(selectedNode.label, selectedNode.id)}`;
                          window.sessionStorage.setItem(key, JSON.stringify(parsed));
                        } catch {}
                      }
                    }}
                  />
                </div>
                <small>מזהה: <span className={styles.ltrValue}>{selectedNode.id}</span></small>
              </>
            )}
            {selectedEdge && (
              <>
                <strong>{relationLabel(selectedEdge.relation)}</strong>
                <span>{endpointLabel(selectedEdge.source, selectedEdge.sourceLabel)} ← {endpointLabel(selectedEdge.target, selectedEdge.targetLabel)}</span>
                {selectedEdge.description && <p>{selectedEdge.description}</p>}
                <p>רמת ביטחון: {confidenceLabel(selectedEdge.confidence)} <span className={styles.ltrValue}>{selectedEdge.confidence ?? ''}</span></p>
                <dl className={styles.inspectorFacts}>
                  <dt>ישות מקור</dt>
                  <dd>{endpointLabel(selectedEdge.source, selectedEdge.sourceLabel)}</dd>
                  <dt>ישות יעד</dt>
                  <dd>{endpointLabel(selectedEdge.target, selectedEdge.targetLabel)}</dd>
                  {selectedEdge.properties?.source_files_count !== undefined && (
                    <>
                      <dt>סך קבצי מקור</dt>
                      <dd>{String(selectedEdge.properties.source_files_count)}</dd>
                    </>
                  )}
                </dl>
                {(() => {
                  const historyStr = selectedEdge.properties?.relation_history_preview as string;
                  if (!historyStr) return null;
                  try {
                    const histories = JSON.parse(historyStr);
                    if (!Array.isArray(histories) || histories.length === 0) return null;
                    return (
                      <div style={{ marginTop: '14px', padding: '10px', borderRadius: '6px', background: '#f8fafc', border: '1px solid #e2e8f0', direction: 'rtl', textAlign: 'right' }}>
                        <h4 style={{ margin: '0 0 6px 0', fontSize: '13px', color: '#0f766e', fontWeight: 650 }}>תיעוד ומופעי קשר ({selectedEdge.properties?.relation_history_count || histories.length})</h4>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                          {histories.map((hist: any, idx: number) => (
                            <div key={idx} style={{ fontSize: '12px', color: '#334155', borderBottom: idx < histories.length - 1 ? '1px dashed #e2e8f0' : 'none', paddingBottom: idx < histories.length - 1 ? '6px' : '0' }}>
                              <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: 600 }}>
                                <span>סוג קשר: {relationLabel(hist.relation || selectedEdge.relation)}</span>
                                {(hist.quality !== undefined || selectedEdge.confidence !== undefined) && (
                                  <span style={{ color: '#0f766e' }}>איכות: {hist.quality ?? selectedEdge.confidence}</span>
                                )}
                              </div>
                              {hist.description && <p style={{ margin: '4px 0', fontStyle: 'italic', fontSize: '11px', color: '#475569' }}>{hist.description}</p>}
                              <div style={{ fontSize: '10px', color: '#64748b', display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: '4px' }}>
                                <span style={{ direction: 'ltr', textAlign: 'left', wordBreak: 'break-all' }}>
                                  קובץ: {hist.source_file || (selectedEdge.properties?.pk as string) || (selectedEdge.properties?.source_file as string) || 'לא ידוע'}
                                </span>
                                {(hist.source_location || selectedEdge.properties?.source_location) && (
                                  <span>מיקום: {hist.source_location || selectedEdge.properties?.source_location}</span>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  } catch (e) {
                    return null;
                  }
                })()}
                <small>{selectedEdge.sourceLocation || 'אין מיקום מקור תומך'}</small>
              </>
            )}
          </aside>
          )}
        </div>
      )}
    </section>
  );
};
