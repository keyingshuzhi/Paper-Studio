"""核心研究 Skill 共享的 JSON Schema。"""

from __future__ import annotations

from typing import Any, Dict

from .metadata import PAPER_SCHEMA


SUMMARY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": [
        "title", "problem", "method", "contribution", "limitation", "keywords",
    ],
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "problem": {"type": "string", "minLength": 1},
        "method": {"type": "string", "minLength": 1},
        "contribution": {"type": "string", "minLength": 1},
        "limitation": {"type": "string", "minLength": 1},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "_fallback": {"type": "boolean"},
    },
    "additionalProperties": True,
}


SUMMARY_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": ["string", "null"]},
        "abstract": {"type": ["string", "null"]},
        "text": {"type": "string"},
    },
    "additionalProperties": False,
}


SUMMARY_RECORD_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["ok", "summary", "error"],
    "properties": {
        "ok": {"type": "boolean"},
        "summary": {"anyOf": [SUMMARY_SCHEMA, {"type": "null"}]},
        "error": {"type": ["string", "null"]},
        "fallback": {"type": "boolean"},
    },
    "additionalProperties": True,
}


PAPER_PROFILE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["index", "title"],
    "properties": {
        "index": {"type": "integer", "minimum": 1},
        "title": {"type": "string", "minLength": 1},
        "year": {"type": ["integer", "null"]},
        "source": {"type": ["string", "null"]},
        "problem": {"type": "string"},
        "method": {"type": "string"},
        "contribution": {"type": "string"},
        "limitation": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": True,
}


ANALYSIS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["consensus", "conflicts", "evolution", "gaps", "summary"],
    "properties": {
        "consensus": {"type": "array", "items": {"type": "object"}},
        "conflicts": {"type": "array", "items": {"type": "object"}},
        "evolution": {"type": "array", "items": {"type": "object"}},
        "gaps": {"type": "array", "items": {"type": "object"}},
        "summary": {"type": "string"},
        "_fallback": {"type": "boolean"},
        "_error": {"type": "string"},
    },
    "additionalProperties": True,
}


CITATION_ANALYSIS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["top_cited", "intra_citations", "coverage", "errors"],
    "properties": {
        "top_cited": {"type": "array", "items": {"type": "object"}},
        "intra_citations": {"type": "array", "items": {"type": "object"}},
        "coverage": {"type": "number", "minimum": 0, "maximum": 1},
        "errors": {"type": "array"},
        "analyzed_papers": {"type": "integer", "minimum": 0},
        "total_papers": {"type": "integer", "minimum": 0},
        "recovered_papers": {"type": "integer", "minimum": 0},
        "error_stats": {"type": "object"},
        "_degraded": {"type": "boolean"},
    },
    "additionalProperties": True,
}


MEMORY_ENTRY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["query", "timestamp", "papers", "summaries", "analysis"],
    "properties": {
        "query": {"type": "string", "minLength": 1},
        "timestamp": {"type": "string"},
        "papers": {"type": "array", "items": PAPER_SCHEMA},
        "summaries": {"type": "array"},
        "analysis": {"type": ["object", "null"]},
    },
    "additionalProperties": True,
}


RESEARCH_PLAN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["query", "original_query"],
    "properties": {
        "query": {"type": "string", "minLength": 1},
        "original_query": {"type": "string", "minLength": 1},
        "max_results": {"type": "integer", "minimum": 1},
        "sources": {"type": ["array", "null"],
                    "items": {"type": "string"}},
        "download": {"type": "boolean"},
        "max_downloads": {"type": ["integer", "null"]},
        "report": {"type": "boolean"},
        "year_from": {"type": ["integer", "null"]},
        "extra": {"type": "object"},
    },
    "additionalProperties": True,
}


REPORT_KIND_SCHEMA: Dict[str, Any] = {
    "type": "string",
    "enum": ["single", "deep", "comparison"],
}
