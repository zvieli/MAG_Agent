import * as cytoscapeModule from 'cytoscape';
import { Core, EventObject } from 'cytoscape';
import * as React from 'react';
import styles from './MagApp.module.scss';
import { GraphEdge, GraphNode, GraphQueryResult } from '../domain/types';
import { relationLabel } from '../domain/localization';
import { displayName } from '../domain/displayNames';
import {
  communityColor,
  edgeColor,
  edgeLineStyle,
  edgeWidth
} from '../domain/graphVisuals';

type CytoscapeFactory = (options: cytoscapeModule.CytoscapeOptions) => Core;

const cytoscapeFactory = (
  (cytoscapeModule as unknown as { default?: CytoscapeFactory }).default ||
  (cytoscapeModule as unknown as CytoscapeFactory)
);

export interface GraphCanvasProps {
  graph: GraphQueryResult;
  onSelectNode: (node: GraphNode | undefined) => void;
  onSelectEdge: (edge: GraphEdge | undefined) => void;
  command?: GraphCanvasCommand;
}

export interface GraphCanvasCommand {
  type: 'fit' | 'layout' | 'clear';
  nonce: number;
}

export const GraphCanvas: React.FC<GraphCanvasProps> = ({ graph, onSelectNode, onSelectEdge, command }) => {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const cyRef = React.useRef<Core | null>(null);

  React.useEffect(() => {
    if (!containerRef.current) {
      return;
    }

    const elements = [
      ...graph.nodes.map((node) => ({
        data: {
          id: node.id,
          label: displayName(node.label, node.id),
          type: node.type,
          communityId: node.communityId || '0',
          communityColor: communityColor(node.communityId),
          nodeSize: Math.max(12, Math.min(18 + (node.degree || 0) * 2, 42)),
          haloSize: Math.max(20, Math.min(30 + (node.degree || 0) * 2, 56)),
          degree: node.degree || 0
        }
      })),
      ...graph.edges.map((edge) => ({
        data: {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          label: relationLabel(edge.relation),
          relation: edge.relation,
          color: edgeColor(edge.relation),
          width: edgeWidth(edge.confidence),
          lineStyle: edgeLineStyle(edge.confidence),
          confidence: edge.confidence
        }
      }))
    ];

    const cy = cytoscapeFactory({
      container: containerRef.current,
      elements,
      layout: {
        name: 'cose',
        animate: false,
        fit: true,
        padding: 54,
        nodeRepulsion: 11000,
        idealEdgeLength: 145,
        edgeElasticity: 70,
        gravity: 0.1,
        numIter: 1200
      },
      style: [
        {
          selector: 'node',
          style: {
            'background-color': 'data(communityColor)',
            'background-opacity': 0.92,
            'border-color': '#e2e8f0',
            'border-opacity': 0.22,
            'border-width': 1,
            shape: 'ellipse',
            color: '#e5edf8',
            content: '',
            'font-size': '11px',
            'font-weight': 700,
            'text-background-color': '#111827',
            'text-background-opacity': 0.88,
            'text-background-padding': '6px',
            'text-border-color': 'data(communityColor)',
            'text-border-opacity': 0.42,
            'text-border-width': 1,
            'text-margin-y': 10,
            'text-valign': 'bottom',
            'text-halign': 'center',
            'text-wrap': 'wrap',
            'text-max-width': '128px',
            width: 'data(nodeSize)',
            height: 'data(nodeSize)',
            'overlay-color': 'data(communityColor)',
            'overlay-opacity': 0,
            'overlay-padding': 6
          }
        },
        {
          selector: 'node[degree >= 6]',
          style: {
            width: 'data(haloSize)',
            height: 'data(haloSize)',
            'border-width': 2,
            'border-opacity': 0.36
          }
        },
        {
          selector: 'edge',
          style: {
            'curve-style': 'bezier',
            'line-color': 'data(color)',
            'line-opacity': 0.28,
            'target-arrow-color': 'data(color)',
            'target-arrow-shape': 'none',
            'line-style': 'data(lineStyle)' as cytoscapeModule.Css.PropertyValueEdge<cytoscapeModule.Css.LineStyle>,
            content: '',
            'font-size': '9px',
            color: '#dbeafe',
            'text-background-color': '#111827',
            'text-background-opacity': 0.9,
            'text-background-padding': '4px',
            width: 1
          }
        },
        {
          selector: 'node.focused, node:selected',
          style: {
            content: 'data(label)',
            'background-opacity': 1,
            'border-color': '#ffffff',
            'border-opacity': 0.9,
            'border-width': 3,
            'overlay-opacity': 0.18,
            'z-index': 999
          }
        },
        {
          selector: 'edge.focused, edge:selected',
          style: {
            content: 'data(label)',
            'line-opacity': 0.85,
            width: 'data(width)',
            'z-index': 900
          }
        },
        {
          selector: 'node:selected',
          style: {
            'border-color': '#f8fafc',
            'border-width': 4,
            'text-background-color': '#020617'
          }
        },
        {
          selector: 'edge:selected',
          style: {
            width: 4,
            'font-size': '10px',
            color: '#f8fafc'
          }
        },
        {
          selector: '.dimmed',
          style: {
            opacity: 0.12
          }
        },
        {
          selector: '.focused',
          style: {
            opacity: 1,
            'z-index': 999
          }
        }
      ]
    });

    cy.on('tap', 'node', (event: EventObject) => {
      const id = event.target.id();
      onSelectNode(graph.nodes.find((node) => node.id === id));
      onSelectEdge(undefined);
    });

    cy.on('tap', 'edge', (event: EventObject) => {
      const id = event.target.id();
      onSelectEdge(graph.edges.find((edge) => edge.id === id));
      onSelectNode(undefined);
    });

    cy.on('tap', (event: EventObject) => {
      if (event.target === cy) {
        onSelectNode(undefined);
        onSelectEdge(undefined);
        cy.elements().unselect();
      }
    });

    cy.on('mouseover', 'node, edge', (event: EventObject) => {
      const element = event.target;
      const neighborhood = element.isNode() ? element.closedNeighborhood() : element.connectedNodes().union(element);
      cy.elements().addClass('dimmed');
      neighborhood.addClass('focused');
      neighborhood.removeClass('dimmed');
    });

    cy.on('mouseout', 'node, edge', () => {
      cy.elements().removeClass('dimmed focused');
    });

    cyRef.current = cy;

    return () => {
      try {
        if (cy) {
          cy.removeAllListeners();
          if (typeof cy.stop === 'function') {
            cy.stop();
          }
          if (typeof cy.destroy === 'function' && !cy.destroyed()) {
            cy.destroy();
          }
        }
      } catch (e) {
        // Guard against unmount race condition
      }
      cyRef.current = null;
    };
  }, [graph, onSelectEdge, onSelectNode]);

  React.useEffect(() => {
    const cy = cyRef.current;
    if (!cy || cy.destroyed() || !command) return;

    if (command.type === 'fit') {
      cy.fit(undefined, 32);
    }

    if (command.type === 'layout') {
      cy.layout({
        name: 'cose',
        animate: false,
        fit: true,
        padding: 54,
        nodeRepulsion: 11500,
        idealEdgeLength: 145,
        edgeElasticity: 70,
        gravity: 0.1,
        numIter: 1200
      }).run();
    }

    if (command.type === 'clear') {
      cy.elements().unselect();
      cy.elements().removeClass('dimmed focused');
      onSelectNode(undefined);
      onSelectEdge(undefined);
    }
  }, [command, onSelectEdge, onSelectNode]);


  return <div ref={containerRef} className={styles.graphCanvas} role="img" aria-label="תצוגת גרף ידע" />;
};
