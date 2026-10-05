# AICorellator
AI Infrastructure Attack Surface Correlator

A modular pipeline that correlates AI infrastructure scan data into exploitability chains. It ingests output from discovery tools (NeuralScan, agent-bom, mcp-scan) and LLM behavioral scanners (garak), normalizes everything into a unified graph of nodes (LLM endpoints, MCP servers, tools, credentials, agents) and edges (access, delegation, exposure), then uses a voting ensemble of heterogeneous local LLMs to identify multi-hop attack chains that no single scanner can see.

Built for both red and blue teams: fast profile for quick reconnaissance, deep profile for thorough audit with prioritized findings.
