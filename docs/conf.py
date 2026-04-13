"""Sphinx configuration for the Audax documentation site."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

project = "Audax"
author = "Audax contributors"
copyright = f"{datetime.now():%Y}, {author}"
release = "development"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.duration",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
    "sphinx_design",
]

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

autosummary_generate = True
autoclass_content = "both"
autodoc_member_order = "bysource"
autodoc_preserve_defaults = True
autodoc_typehints = "description"

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

copybutton_prompt_text = r">>> |\.\.\. |\$ "
copybutton_prompt_is_regexp = True

html_theme = "pydata_sphinx_theme"
html_title = "Audax Documentation"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_show_sourcelink = False
html_last_updated_fmt = "%b %d, %Y"
html_context = {"default_mode": "light"}
html_sidebars = {
    "index": [],
    "**": ["search-field", "sidebar-nav-bs", "page-toc"],
}
html_theme_options = {
    "logo": {"text": "Audax"},
    "header_links_before_dropdown": 6,
    "navigation_with_keys": True,
    "show_nav_level": 2,
    "secondary_sidebar_items": ["page-toc"],
    "announcement": (
        "Mission-driven orchestration for Claude and Codex with locked specs, "
        "structured reviews, and real repository state checks."
    ),
}
