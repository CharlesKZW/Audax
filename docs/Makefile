# Minimal wrapper around ``sphinx-build`` for local docs workflows.

SPHINXBUILD ?= python -m sphinx
SOURCEDIR   = .
BUILDDIR    = _build

.PHONY: help html clean

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)"

html:
	@$(SPHINXBUILD) -W -b html "$(SOURCEDIR)" "$(BUILDDIR)/html"

clean:
	rm -rf "$(BUILDDIR)"
