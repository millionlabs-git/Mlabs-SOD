FROM python:3.12-slim

# System deps: git, gh CLI, Node.js 20
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI (required by claude-agent-sdk)
RUN npm install -g @anthropic-ai/claude-code

# Install Fly.io CLI (flyctl) for deployment
ENV FLYCTL_INSTALL="/usr/local/fly"
RUN curl -L https://fly.io/install.sh | sh
ENV PATH="/usr/local/fly/bin:$PATH"

# Install Playwright + Chromium for Visual Playwright
RUN npx playwright install --with-deps chromium

WORKDIR /app

# Clone everything-claude-code configs at build time
RUN git clone --depth 1 https://github.com/affaan-m/everything-claude-code.git /tmp/ecc && \
    mkdir -p claude-config && \
    cp -r /tmp/ecc/agents claude-config/ && \
    cp -r /tmp/ecc/skills claude-config/ && \
    cp -r /tmp/ecc/rules claude-config/ && \
    cp -r /tmp/ecc/hooks claude-config/ && \
    rm -rf /tmp/ecc

# Clone Visual Playwright
RUN git clone --depth 1 https://github.com/Finn-ML/visual-playwright.git /tmp/vp && \
    mkdir -p visual-playwright && \
    cp -r /tmp/vp/scripts visual-playwright/ && \
    cp -r /tmp/vp/SKILL.md visual-playwright/ 2>/dev/null || true && \
    rm -rf /tmp/vp && \
    cd visual-playwright && npm init -y && npm install playwright

# Copy project templates
COPY templates/ ./templates/

# Python deps
COPY worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY worker/src/ ./src/

# Create non-root user (Claude Code CLI refuses to run as root)
RUN useradd -m -s /bin/bash worker && \
    mkdir -p /workspace && \
    chown -R worker:worker /app /workspace

# Playwright headless config
ENV VP_HEADLESS=true
ENV VP_BROWSER=chromium
# Ensure Python output is sent straight to logs (no buffering)
ENV PYTHONUNBUFFERED=1

USER worker

# Git identity for commits
RUN git config --global user.email "prd-worker@millionlabs.ai" && \
    git config --global user.name "PRD Worker"

CMD ["python", "-m", "src.main"]
