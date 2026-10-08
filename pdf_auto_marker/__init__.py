"""PDF 内の表を自動マーキングするローカルツール。"""

from .config import Config, ConfigError, load_config, parse_color
from .marker import MarkResult, inspect_pdf, mark_document, mark_pdf

__all__ = [
    "Config", "ConfigError", "MarkResult",
    "inspect_pdf", "load_config", "mark_document", "mark_pdf", "parse_color",
]
__version__ = "0.1.0"
