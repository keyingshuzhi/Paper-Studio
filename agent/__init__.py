"""Paper Studio 学术研究 Agent — DSH 分层架构。

架构分层：
- agent.skills  : 原子能力层（Skills），每个技能只做一件事。
- agent.plugins : 流程编排层（Plugins），组合多个技能完成业务闭环。
- agent.core    : 控制与调度层（MCP），负责规划、决策、报告生成。
"""

__version__ = "0.1.0"
