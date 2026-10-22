# waynext Makefile

PREFIX ?= $(HOME)/.local
BINDIR ?= $(PREFIX)/bin

.PHONY: all install uninstall check deps lint test help

all: check
	@echo "Run 'make install' to install waynext"

help:
	@echo "waynext - Wayland GTK4 Google Calendar popup for waybar"
	@echo ""
	@echo "Targets:"
	@echo "  make install    Install to ~/.local/bin (default)"
	@echo "  make uninstall  Remove installation"
	@echo "  make check      Verify dependencies"
	@echo "  make deps       Install Python dependencies (pip)"
	@echo "  make lint       Run ruff linter"
	@echo "  make test       Run test suite"
	@echo ""
	@echo "Variables:"
	@echo "  PREFIX=~/.local  Installation prefix"
	@echo "  BINDIR=\$$PREFIX/bin  Binary directory"
	@echo ""
	@echo "After install, add to waybar config:"
	@echo '  "custom/waynext": {'
	@echo '    "exec": "waynext status",'
	@echo '    "return-type": "json",'
	@echo '    "interval": 120,'
	@echo '    "on-click": "waynext toggle"'
	@echo '  }'

check:
	@echo "Checking dependencies..."
	@command -v python3 >/dev/null || { echo "ERROR: python3 not found"; exit 1; }
	@python3 -c "import gi" 2>/dev/null || { echo "ERROR: PyGObject not found (install python-gobject)"; exit 1; }
	@python3 -c "import gi; gi.require_version('Gtk', '4.0')" 2>/dev/null || { echo "ERROR: GTK4 not found"; exit 1; }
	@python3 -c "import gi; gi.require_version('Adw', '1')" 2>/dev/null || { echo "ERROR: libadwaita not found"; exit 1; }
	@python3 -c "import gi; gi.require_version('Gtk4LayerShell', '1.0')" 2>/dev/null || { echo "ERROR: gtk4-layer-shell not found"; exit 1; }
	@python3 -c "import yaml" 2>/dev/null || { echo "ERROR: PyYAML not found (install python-yaml)"; exit 1; }
	@python3 -c "import googleapiclient" 2>/dev/null || { echo "ERROR: google-api-python-client not found"; exit 1; }
	@python3 -c "import google_auth_oauthlib" 2>/dev/null || { echo "ERROR: google-auth-oauthlib not found"; exit 1; }
	@echo "All dependencies OK"

deps:
	@echo "Installing Python dependencies via pip..."
	pip install --user -r requirements.txt
	@echo ""
	@echo "System packages also required (install via your package manager):"
	@echo "  - gtk4"
	@echo "  - libadwaita"
	@echo "  - gtk4-layer-shell"
	@echo "  - python-gobject"

lint:
	@command -v ruff >/dev/null || { echo "ERROR: ruff not found (pip install ruff)"; exit 1; }
	ruff check .
	ruff format --check .

test:
	@command -v pytest >/dev/null || { echo "ERROR: pytest not found (pip install pytest)"; exit 1; }
	pytest tests/ -v --tb=short

install: check
	@echo "Installing waynext to $(BINDIR)..."
	@mkdir -p $(BINDIR)
	@cp waynext.py $(BINDIR)/waynext.py
	@chmod +x $(BINDIR)/waynext.py
	@echo '#!/usr/bin/env bash' > $(BINDIR)/waynext
	@echo '# waynext launcher' >> $(BINDIR)/waynext
	@echo 'exec python3 "$(BINDIR)/waynext.py" "$$@"' >> $(BINDIR)/waynext
	@chmod +x $(BINDIR)/waynext
	@echo ""
	@echo "Installed successfully!"
	@echo ""
	@echo "First run: waynext toggle"
	@echo "(A browser window will open for Google Calendar OAuth consent)"

uninstall:
	@echo "Removing waynext from $(BINDIR)..."
	@rm -f $(BINDIR)/waynext
	@rm -f $(BINDIR)/waynext.py
	@rm -f /tmp/waynext-$$(id -u).sock
	@echo "Uninstalled"
