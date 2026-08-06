#!/usr/bin/env python3
import sys
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.query_agent.nodes import get_investigative_graph

def generate_visualization():
    print("🔄 Compiling LangGraph StateGraph...")
    app = get_investigative_graph()
    
    print("✍️ Generating Mermaid syntax...")
    try:
        mermaid_code = app.get_graph().draw_mermaid()
        
        # 1. Clean up HTML tags in start/end node labels
        mermaid_code = mermaid_code.replace("<p>__start__</p>", "__start__")
        mermaid_code = mermaid_code.replace("<p>__end__</p>", "__end__")
        
        # 2. Enforce high-contrast dark text inside filled nodes (fixes dark mode white-on-white text)
        mermaid_code = mermaid_code.replace(
            "classDef default fill:#f2f0ff,line-height:1.2",
            "classDef default fill:#e0e7ff,line-height:1.2,color:#0f172a,stroke:#312e81,stroke-width:1px"
        )
        
        # 3. Give start and end nodes solid, high-contrast lavender background fills
        mermaid_code = mermaid_code.replace(
            "classDef first fill-opacity:0",
            "classDef first fill:#c7d2fe,color:#0f172a,stroke:#312e81,stroke-width:1px"
        )
        mermaid_code = mermaid_code.replace(
            "classDef last fill:#bfb6fc",
            "classDef last fill:#a5b4fc,color:#0f172a,stroke:#312e81,stroke-width:1px"
        )
        
        # 4. Inject linkStyle to make all edges (connection lines) thick and bright indigo
        mermaid_code += "\n\tlinkStyle default stroke:#818cf8,stroke-width:2px;"
        
        md_content = f"""# LangGraph State Machine Diagram

```mermaid
{mermaid_code}
```
"""
        
        # Save to local workspace root as graph.md
        local_path = ROOT / "graph.md"
        local_path.write_text(md_content, encoding="utf-8")
        print(f"✅ Local markdown visualization saved to: {local_path}")
        
        # Save to visuals directory
        artifact_dir = ROOT / "visuals"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "agent_graph.md"
        artifact_path.write_text(md_content, encoding="utf-8")
        print(f"✅ Brain artifact markdown saved to: {artifact_path}")
            
    except Exception as e:
        print(f"❌ Failed to generate graph visualization: {e}")
        sys.exit(1)

if __name__ == "__main__":
    generate_visualization()
