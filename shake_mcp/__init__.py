"""Servidor MCP de solo lectura para consultar shake-pos desde un asistente.

`consultas` solo usa el ORM y no importa el SDK de MCP: así sus pruebas
corren con la suite de siempre aunque `mcp` no esté instalado. `server`
envuelve esas consultas en herramientas MCP por stdio.
"""
