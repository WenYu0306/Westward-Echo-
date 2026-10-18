"""Glossary package — two-layer term lookup for the translation pipeline.

【中文·术语库包】
这个包是「西渡」翻译引擎的术语库层，负责在翻译时告诉 AI "这个词该怎么翻"。
它分两层：
  · exact_store  —— 精确匹配层：中文原词一模一样才命中，像查字典，
                     保证人名、地名等专有名词全书统一。数据存在 SQLite 里。
  · semantic_store—— 语义匹配层：靠"意思相近"检索，字面不同也能匹配上，
                     用于补充文化相关术语。数据存在 Chroma 向量库里。
两层配合使用：先精确查，查不到再语义查，确保既不遗漏也不乱匹配。
"""
