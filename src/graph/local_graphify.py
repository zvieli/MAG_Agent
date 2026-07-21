import networkx as nx
from typing import Dict, List, Any, Optional


def build_from_json(graph_data: Dict[str, Any]) -> nx.Graph:
    """Creates a NetworkX graph from a dictionary of nodes and edges."""
    G = nx.Graph()
    for node in graph_data.get("nodes", []):
        node_id = str(node["id"])
        G.add_node(node_id, **{k: v for k, v in node.items() if k != "id"})

    for edge in graph_data.get("edges", []) or graph_data.get("links", []):
        source = str(edge["source"])
        target = str(edge["target"])
        attrs = {k: v for k, v in edge.items() if k not in ("source", "target")}
        history = (
            attrs.get("metadata", {}).get("all_relations_history", [])
            if isinstance(attrs.get("metadata"), dict)
            else []
        )
        confidence = attrs.get("confidence_score")
        try:
            confidence_weight = float(confidence) if confidence is not None else 1.0
        except (TypeError, ValueError):
            confidence_weight = 1.0
        relation_weight = max(1.0, float(len(history) or attrs.get("weight") or 1.0))
        attrs["weight"] = max(0.1, confidence_weight) * relation_weight
        if G.has_edge(source, target):
            G[source][target]["weight"] = G[source][target].get("weight", 1.0) + attrs["weight"]
            existing_history = G[source][target].setdefault("merged_relations", [])
            existing_history.append(attrs)
        else:
            G.add_edge(source, target, **attrs)

    if "graph" in graph_data:
        G.graph.update(graph_data["graph"])
    return G


def cluster(G: nx.Graph) -> tuple[dict[str, list[str]], dict[str, str]]:
    """
    Detects communities using recursive greedy modularity partitioning
    to prevent 'God Communities' and resolve modularity resolution limit.
    
    Returns:
        communities_dict (dict[str, list[str]]): Mapping of community ID to list of member node IDs.
        hierarchy_dict (dict[str, str]): Mapping of child community ID to parent community ID.
    """
    from networkx.algorithms.community import greedy_modularity_communities

    communities_dict: dict[str, list[str]] = {}
    hierarchy_dict: dict[str, str] = {}
    if G.number_of_nodes() == 0:
        return communities_dict, hierarchy_dict

    def partition(subgraph_nodes: list[str], parent_id: str | None = None, depth: int = 0):
        sub_g = G.subgraph(subgraph_nodes)
        
        # Base case: Small enough or cannot split further
        if len(sub_g) <= 15:
            comm_id = f"c_{len(communities_dict)}"
            communities_dict[comm_id] = subgraph_nodes
            if parent_id:
                hierarchy_dict[comm_id] = parent_id
            return
            
        sub_comms = list(greedy_modularity_communities(sub_g, weight="weight"))
        if len(sub_comms) <= 1:
            comm_id = f"c_{len(communities_dict)}"
            communities_dict[comm_id] = subgraph_nodes
            if parent_id:
                hierarchy_dict[comm_id] = parent_id
            return
            
        # Hierarchy tracking: Establish a parent node at the current level
        current_parent_id = f"c_{len(communities_dict)}"
        communities_dict[current_parent_id] = subgraph_nodes
        if parent_id:
            hierarchy_dict[current_parent_id] = parent_id
            
        # Recurse on children
        for c in sub_comms:
            partition(list(c), parent_id=current_parent_id, depth=depth+1)

    raw_comms = list(greedy_modularity_communities(G, weight="weight"))
    for comm in raw_comms:
        partition(list(comm), parent_id=None, depth=0)

    return communities_dict, hierarchy_dict


def score_all(G: nx.Graph, communities: Dict[int, List[str]]) -> Dict[str, float]:
    """Calculates basic cohesion scores (density) for each community."""
    scores = {}
    for c_id, members in communities.items():
        if len(members) <= 1:
            scores[str(c_id)] = 1.0
            continue
        subgraph = G.subgraph(members)
        density = nx.density(subgraph)
        scores[str(c_id)] = density
    return scores


def god_nodes(
    G: nx.Graph, threshold: Optional[int] = None, top_n: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Identifies high-degree nodes."""
    if threshold is None and top_n is None:
        # Heuristic: 2x the average degree or a fixed high number
        avg_degree = (
            sum(dict(G.degree()).values()) / G.number_of_nodes()
            if G.number_of_nodes() > 0
            else 0
        )
        threshold = max(20, int(avg_degree * 3))

    gods = []
    for node, degree in G.degree():
        if threshold is not None and degree >= threshold:
            gods.append(
                {
                    "id": node,
                    "label": G.nodes[node].get("label", node),
                    "degree": degree,
                    "description": G.nodes[node].get("forensic_summary")
                    or G.nodes[node].get("description", ""),
                }
            )
        elif threshold is None:
            gods.append(
                {
                    "id": node,
                    "label": G.nodes[node].get("label", node),
                    "degree": degree,
                    "description": G.nodes[node].get("forensic_summary")
                    or G.nodes[node].get("description", ""),
                }
            )

    sorted_gods = sorted(gods, key=lambda x: x["degree"], reverse=True)
    if top_n is not None:
        return sorted_gods[:top_n]
    return sorted_gods


def surprising_connections(
    G: nx.Graph, communities: Dict[int, List[str]]
) -> List[Dict[str, Any]]:
    """Finds edges that connect different communities."""
    node_to_community = {}
    for c_id, members in communities.items():
        for m in members:
            node_to_community[m] = c_id

    surprising = []
    for u, v in G.edges():
        c1 = node_to_community.get(u)
        c2 = node_to_community.get(v)
        if c1 is not None and c2 is not None and c1 != c2:
            surprising.append(
                {
                    "source": u,
                    "target": v,
                    "source_label": G.nodes[u].get("label", u),
                    "target_label": G.nodes[v].get("label", v),
                    "source_community": c1,
                    "target_community": c2,
                    "score": 1.0,  # Placeholder for actual surprise score
                }
            )
    return surprising


def calculate_centrality(G: nx.Graph) -> Dict[str, Dict[str, float]]:
    """Calculates multiple centrality metrics for investigative importance."""
    degree = nx.degree_centrality(G)
    try:
        betweenness = nx.betweenness_centrality(G)
    except Exception:
        betweenness = {}
    return {"degree": degree, "betweenness": betweenness}


def find_paths(G: nx.Graph, source: str, target: str) -> List[List[str]]:
    """Finds the shortest paths between two investigative entities."""
    try:
        return list(nx.all_shortest_paths(G, source, target))
    except Exception:
        return []


def generate(
    G,
    communities,
    cohesion,
    community_labels,
    god_nodes_list,
    surprising_list,
    stats,
    token_usage,
    source_file,
):
    """Generates an enhanced investigative report with graph analytics."""
    centrality = calculate_centrality(G)

    report = f"# Investigative Report: {source_file}\n\n"
    report += "## Graph Statistics\n"
    report += f"- **Total Entities (Nodes):** {G.number_of_nodes()}\n"
    report += f"- **Total Relations (Edges):** {G.number_of_edges()}\n"
    report += f"- **Identified Communities:** {len(communities)}\n\n"

    report += "## High Centrality Entities (Key Actors)\n"
    report += "| Entity | Degree Centrality | Betweenness (Brokerage) |\n"
    report += "| --- | --- | --- |\n"

    # Sort by betweenness if available, else degree
    sorted_nodes = sorted(
        G.nodes(), key=lambda n: centrality["betweenness"].get(n, 0), reverse=True
    )
    for node in sorted_nodes[:10]:
        label = G.nodes[node].get("label", node)
        deg = centrality["degree"].get(node, 0)
        bet = centrality["betweenness"].get(node, 0)
        report += f"| {label} | {deg:.3f} | {bet:.3f} |\n"

    report += "\n## Community Insights\n"
    for c_id, members in list(communities.items())[:5]:
        label = community_labels.get(str(c_id), f"Community {c_id}")
        density = cohesion.get(str(c_id), 0)
        report += f"### {label}\n"
        report += f"- **Density:** {density:.2f}\n"
        report += f"- **Top Members:** {', '.join([G.nodes[m].get('label', m) for m in members[:5]])}\n\n"

    report += "## Surprising Connections (Cross-Community)\n"
    for s in surprising_list[:10]:
        report += f"- **{s['source_label']}** <-> **{s['target_label']}** (Bridging {community_labels.get(str(s['source_community']), s['source_community'])} and {community_labels.get(str(s['target_community']), s['target_community'])})\n"

    return report


def to_html(G, communities, stream, community_labels=None):
    """Generates an interactive Cytoscape.js graph visualization."""
    import json

    nodes = []
    for n, data in G.nodes(data=True):
        nodes.append(
            {
                "data": {
                    "id": n,
                    "label": data.get("label", n),
                    "type": data.get("type", "Unknown"),
                    "description": data.get("description")
                    or data.get("forensic_summary")
                    or "",
                    "community": next(
                        (c for c, members in communities.items() if n in members), 0
                    ),
                }
            }
        )

    edges = []
    for u, v, data in G.edges(data=True):
        edges.append(
            {
                "data": {
                    "id": f"{u}-{v}-{data.get('relation', 'link')}",
                    "source": u,
                    "target": v,
                    "label": data.get("relation", ""),
                    "confidence": data.get("confidence_score", 0.0),
                }
            }
        )

    cy_data = {"nodes": nodes, "edges": edges}

    html_template = """
<!DOCTYPE html>
<html>
<head>
    <title>Investigative Graph Explorer</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.26.0/cytoscape.min.js"></script>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; display: flex; height: 100vh; overflow: hidden; }
        #cy { flex: 1; background: #f8f9fa; }
        #sidebar { width: 350px; border-left: 1px solid #ddd; padding: 20px; overflow-y: auto; background: white; box-shadow: -2px 0 5px rgba(0,0,0,0.05); }
        h2 { margin-top: 0; color: #333; font-size: 1.2rem; }
        .meta-item { margin-bottom: 15px; }
        .meta-label { font-weight: bold; font-size: 0.9rem; color: #666; display: block; margin-bottom: 4px; }
        .meta-value { font-size: 1rem; color: #333; line-height: 1.4; }
        .tag { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.8rem; background: #e9ecef; margin-right: 5px; }
        #empty-state { color: #999; font-style: italic; text-align: center; margin-top: 100px; }
    </style>
</head>
<body>
    <div id="cy"></div>
    <div id="sidebar">
        <div id="details">
            <div id="empty-state">Click a node or edge to see details</div>
        </div>
    </div>

    <script>
        const cy = cytoscape({
            container: document.getElementById('cy'),
            elements: __CY_DATA__,
            style: [
                {
                    selector: 'node',
                    style: {
                        'label': 'data(label)',
                        'background-color': '#007bff',
                        'color': '#333',
                        'text-valign': 'bottom',
                        'text-margin-y': 8,
                        'font-size': '12px',
                        'width': 30,
                        'height': 30
                    }
                },
                {
                    selector: 'edge',
                    style: {
                        'label': 'data(label)',
                        'width': 2,
                        'line-color': '#adb5bd',
                        'target-arrow-color': '#adb5bd',
                        'target-arrow-shape': 'triangle',
                        'curve-style': 'bezier',
                        'font-size': '10px',
                        'text-rotation': 'autorotate',
                        'text-margin-y': -10
                    }
                },
                {
                    selector: ':selected',
                    style: {
                        'background-color': '#ffc107',
                        'line-color': '#ffc107',
                        'target-arrow-color': '#ffc107'
                    }
                }
            ],
            layout: { name: 'cose', animate: false }
        });

        cy.on('tap', function(event) {
            const target = event.target;
            const details = document.getElementById('details');
            
            if (target === cy) {
                details.innerHTML = '<div id="empty-state">Click a node or edge to see details</div>';
                return;
            }

            const data = target.data();
            let html = '';
            
            if (target.isNode()) {
                html = `
                    <h2>${data.label}</h2>
                    <div class="meta-item">
                        <span class="meta-label">Type</span>
                        <span class="tag">${data.type}</span>
                        <span class="tag">Community ${data.community}</span>
                    </div>
                    <div class="meta-item">
                        <span class="meta-label">Description</span>
                        <div class="meta-value">${data.description || 'No description available'}</div>
                    </div>
                `;
            } else {
                html = `
                    <h2>Relation: ${data.label}</h2>
                    <div class="meta-item">
                        <span class="meta-label">From</span>
                        <div class="meta-value">${data.source}</div>
                    </div>
                    <div class="meta-item">
                        <span class="meta-label">To</span>
                        <div class="meta-value">${data.target}</div>
                    </div>
                    <div class="meta-item">
                        <span class="meta-label">Confidence</span>
                        <div class="meta-value">${(data.confidence * 100).toFixed(1)}%</div>
                    </div>
                `;
            }
            details.innerHTML = html;
        });
    </script>
</body>
</html>
    """.replace("__CY_DATA__", json.dumps(cy_data))

    if hasattr(stream, "write"):
        stream.write(html_template)
    else:
        from src.storage.factory import get_artifact_store

        store = get_artifact_store()
        store.write_bytes(stream, html_template.encode("utf-8"))
