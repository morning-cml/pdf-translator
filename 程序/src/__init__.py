"""PDF 科研论文翻译工具（英文 → 中文）。

模块划分：
    config      配置加载（API Key、模型等）
    glossary    术语库加载与匹配
    pdf_parser  PDF 解析（文本块 + 坐标 + 字号）
    translator  DeepSeek 翻译客户端（含离线 Mock）
    pdf_writer  译文回填 PDF（保留图片/排版）
    pipeline    串联整个翻译流水线
    gui         Tkinter 图形界面
"""

__version__ = "0.1.0"
