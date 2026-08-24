"""Plugins 层（流程编排层）对外导出。"""

from .acquisition_plugin import DataAcquisitionPipeline
from .base import BasePlugin
from .search_plugin import ComprehensiveSourceSearch

__all__ = [
    "BasePlugin",
    "ComprehensiveSourceSearch",
    "DataAcquisitionPipeline",
]
