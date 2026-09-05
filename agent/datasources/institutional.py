"""机构数据库 / 企业知识库 通用 connector(OAI-PMH 协议优先)。

很多学术机构库、企业知识库都会暴露 OAI-PMH 端点或简单的 REST API。
本类提供一个「endpoint + 自定义 header」模板;用户填写后即可用。
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import DataSource, DataSourceError, DataSourceItem, FetchResult, register


@register
class InstitutionalConnector(DataSource):
    """通用 OAI-PMH / 简单 REST 端点。

    配置:
      - ``endpoint``: 必填,机构库 API 基址
      - ``api_key``: 选填(若机构库需要 Bearer Key)
      - ``protocol``: ``"oai"``(默认, OAI-PMH 2.0) 或 ``"rest"``
      - ``set_spec``: OAI-PMH 专用,可限定集合
    """

    id = "institutional"
    name = "机构/企业知识库(通用)"
    blurb = "通过 OAI-PMH 或简单 REST 端点接入任意机构/企业库,无需专门适配。"
    auth_kind = "api_key"
    config_keys = ["endpoint"]

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self.endpoint = (self.config.get("endpoint") or "").strip()
        self.api_key = self.config.get("api_key") or ""
        self.protocol = (self.config.get("protocol") or "oai").lower()
        self.set_spec = (self.config.get("set_spec") or "").strip()
        self.timeout = float(self.config.get("timeout") or 12)

    def _request(self, params: Dict[str, Any]) -> Any:
        if not self.endpoint:
            self.require_config("endpoint")
        url = self.endpoint
        if self.protocol == "oai":
            merged = {"verb": "ListRecords", "metadataPrefix": "oai_dc"}
            merged.update({k: v for k, v in params.items() if v})
            url = f"{url}?{urlencode(merged)}"
        else:
            url = f"{url}?{urlencode({k: v for k, v in params.items() if v})}"
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if self.protocol == "oai":
                    # 让上层解析 XML
                    return raw.decode("utf-8", errors="ignore")
                return json.loads(raw.decode("utf-8"))
        except HTTPError as err:
            raise DataSourceError(
                f"机构库 API {err.code}: {err.reason}") from err
        except URLError as err:
            raise DataSourceError(f"机构库连接失败: {err.reason}") from err

    def list_targets(self) -> List[Dict[str, Any]]:
        if not self.endpoint or self.protocol != "oai":
            return []
        # OAI ListSets
        try:
            data = self._request({"verb": "ListSets"})
        except DataSourceError:
            return []
        # 极简 XML 文本解析 — 提 <setSpec><setName>
        out = []
        import re
        for m in re.finditer(r"<set>(.*?)</set>", data, re.DOTALL):
            block = m.group(1)
            spec = re.search(r"<setSpec>([^<]+)</setSpec>", block)
            name = re.search(r"<setName>([^<]+)</setName>", block)
            if spec:
                out.append({
                    "id": f"set:{spec.group(1).strip()}",
                    "title": (name.group(1).strip() if name
                              else spec.group(1).strip()),
                    "kind": "set",
                })
        return out[:20]

    def search(self, query: str, limit: int = 10) -> List[DataSourceItem]:
        if not query:
            return []
        try:
            data = self._request({
                "verb": "ListRecords",
                "from": "", "until": "",
                "set": self.set_spec,
                "metadataPrefix": "oai_dc",
            } if self.protocol == "oai" else
                {"q": query, "limit": max(1, min(50, limit))})
        except DataSourceError:
            return []
        if self.protocol == "oai":
            return self._parse_oai_records(data, limit)
        # REST 协议:期待 {"items":[{id,title,url,abstract,...}]}
        rows = (data or {}).get("items") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []
        out: List[DataSourceItem] = []
        for row in rows[:limit]:
            out.append(DataSourceItem(
                id=f"inst:{row.get('id','')}",
                title=row.get("title") or "(无标题)",
                source="institutional",
                url=row.get("url", ""),
                snippet=row.get("abstract") or row.get("summary") or "",
                authors=row.get("authors") or [],
                year=row.get("year"),
                extra={"raw": {k: v for k, v in row.items()
                               if k not in {"title", "url", "abstract",
                                            "summary", "authors", "year"}}},
            ))
        return out

    def fetch(self, target: str) -> FetchResult:
        if not target.startswith("inst:"):
            raise DataSourceError("机构库 target 必须以 'inst:' 开头")
        ident = target.split(":", 1)[1]
        if self.protocol == "oai":
            data = self._request({"verb": "GetRecord",
                                  "identifier": ident,
                                  "metadataPrefix": "oai_dc"})
            return FetchResult(target=target, body=str(data)[:4000],
                               format="xml", meta={"source": "oai"})
        try:
            data = self._request({"id": ident})
        except DataSourceError as err:
            return FetchResult(target=target, body=str(err),
                               format="text", meta={"error": True})
        return FetchResult(target=target,
                           body=json.dumps(data, ensure_ascii=False, indent=2),
                           format="json", meta={"source": "rest"})

    @staticmethod
    def _parse_oai_records(xml_text: str, limit: int) -> List[DataSourceItem]:
        import re
        out: List[DataSourceItem] = []
        pattern = re.compile(r"<record>(.*?)</record>", re.DOTALL)
        for m in pattern.finditer(xml_text):
            block = m.group(1)
            # <header> 或 <header status="deleted"> 都匹配
            header = re.search(r"<header\b[^>]*>(.*?)</header>",
                               block, re.DOTALL)
            if header and 'status="deleted"' in header.group(0):
                continue
            ident = (re.search(r"<identifier>([^<]+)</identifier>", header.group(1))
                     if header else None)
            meta_match = re.search(r"<metadata>(.*?)</metadata>",
                                    block, re.DOTALL)
            meta_text = meta_match.group(1) if meta_match else block
            title = re.search(r"<dc:title[^>]*>([^<]+)</dc:title>", meta_text)
            desc = re.search(r"<dc:description[^>]*>([^<]+)</dc:description>",
                             meta_text)
            date = re.search(r"<dc:date[^>]*>([^<]+)</dc:date>", meta_text)
            creators = re.findall(r"<dc:creator[^>]*>([^<]+)</dc:creator>",
                                   meta_text)
            out.append(DataSourceItem(
                id=f"inst:{ident.group(1).strip() if ident else f'row{len(out)}'}",
                title=title.group(1).strip() if title else "(无标题)",
                source="institutional",
                snippet=desc.group(1)[:280] if desc else "",
                authors=creators,
                year=date.group(1)[:4] if date else None,
                extra={"protocol": "oai"},
            ))
            if len(out) >= limit:
                break
        return out

    def health(self) -> Dict[str, Any]:
        started = time.monotonic()
        if not self.endpoint:
            return {"ok": False, "latency_ms": 0, "error": "缺少 endpoint"}
        try:
            self._request({"verb": "Identify"} if self.protocol == "oai"
                          else {})
            return {"ok": True, "latency_ms": round(
                (time.monotonic() - started) * 1000, 1),
                "info": f"protocol={self.protocol}"}
        except DataSourceError as err:
            return {"ok": False, "latency_ms": round(
                (time.monotonic() - started) * 1000, 1), "error": str(err)}
