FROM debian:13.4@sha256:e2d08da6f42ef4b09b165d55528a12727aeed8240dc9edf888e3ec07e10ef9da AS sqlite
# Match the upstream Dockerfile's WAL-reset fix and verified source archive.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates curl && \
    curl -fsSL https://sqlite.org/2026/sqlite-autoconf-3530400.tar.gz -o /tmp/sqlite.tar.gz && \
    echo '0e9483900e92cd5de8fd48d16bf9200145a61f7fd5be542a5ac81d8a9516eb9c  /tmp/sqlite.tar.gz' | sha256sum -c - && \
    tar -xzf /tmp/sqlite.tar.gz -C /tmp && \
    cd /tmp/sqlite-autoconf-3530400 && \
    CFLAGS='-O2 -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_COLUMN_METADATA' \
    ./configure --prefix=/opt/sqlite --disable-static && make -j2 && make install

FROM ghcr.io/astral-sh/uv:0.11.6-python3.13-trixie@sha256:b3c543b6c4f23a5f2df22866bd7857e5d304b67a564f4feab6ac22044dde719b AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && \
    rm -rf /var/lib/apt/lists/* && mkdir -p /opt/scout && \
    curl -fsSL https://registry.npmjs.org/agent-browser/-/agent-browser-0.26.0.tgz -o /tmp/agent-browser.tgz && \
    echo 'a5da927e3c1b152a7eaa7c256f6836ddef705ef78839f322d7dc693c0f716546f3100529e96e180598fa61b8fccf833b5a471b1830d873ea032070edf51dc40d  /tmp/agent-browser.tgz' | sha512sum -c - && \
    tar -xzf /tmp/agent-browser.tgz -C /opt/scout --strip-components=2 package/bin/agent-browser-linux-x64 && \
    mv /opt/scout/agent-browser-linux-x64 /usr/local/bin/agent-browser && \
    rm /tmp/agent-browser.tgz
COPY --from=sqlite /opt/sqlite/lib/libsqlite3.so.3.53.4 /usr/local/lib/
RUN ln -sf libsqlite3.so.3.53.4 /usr/local/lib/libsqlite3.so.0 && ldconfig && \
    python -c 'import sqlite3; assert sqlite3.sqlite_version_info >= (3, 51, 3)'
WORKDIR /opt/hermes
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-install-project --no-dev
COPY *.py ./
COPY agent/ agent/
COPY hermes_cli/ hermes_cli/
COPY tools/ tools/
COPY gateway/ gateway/
COPY cron/ cron/
COPY providers/ providers/
COPY plugins/ plugins/
COPY docker/signal-scout/config.yaml /opt/scout/config.yaml
RUN uv sync --frozen --no-dev && \
    chmod 0555 /usr/local/bin/agent-browser && \
    useradd --uid 10000 --create-home scout && \
    install -d -o scout -g scout -m 0700 /var/lib/scout /var/lib/scout/logs && \
    ln -s /opt/scout/config.yaml /var/lib/scout/config.yaml
ENV PATH="/opt/hermes/.venv/bin:$PATH" \
    HERMES_HOME=/var/lib/scout \
    PYTHONPATH=/opt/hermes \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10000:10000
WORKDIR /tmp
ENTRYPOINT ["python", "/opt/hermes/plugins/signal-scout/bootstrap.py"]
CMD ["status"]

FROM debian:13.4@sha256:e2d08da6f42ef4b09b165d55528a12727aeed8240dc9edf888e3ec07e10ef9da AS browser
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-yaml ca-certificates curl unzip libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2t64 libpango-1.0-0 libcairo2 && \
    rm -rf /var/lib/apt/lists/* && mkdir -p /opt/scout && \
    curl -fsSL https://storage.googleapis.com/chrome-for-testing-public/153.0.8010.36/linux64/chrome-linux64.zip -o /tmp/chrome.zip && \
    echo '167a098c4fdec156b58a9f678c90a84f9072d789f9c6e7b35496a6987b8b7ef8  /tmp/chrome.zip' | sha256sum -c - && \
    unzip -q /tmp/chrome.zip -d /opt/scout && rm /tmp/chrome.zip && \
    useradd --uid 10000 --no-create-home scout
COPY plugins/signal-scout/security/chromium.py plugins/signal-scout/security/control.py plugins/signal-scout/security/settings.py /opt/scout/security/
COPY docker/signal-scout/config.yaml /opt/scout/config.yaml
ENV PYTHONPATH=/opt/scout PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10000:10000
WORKDIR /tmp
ENTRYPOINT ["python3", "-m", "security.chromium"]

FROM debian:13.4@sha256:e2d08da6f42ef4b09b165d55528a12727aeed8240dc9edf888e3ec07e10ef9da AS network
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-yaml iptables util-linux ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --uid 10000 --no-create-home scout && \
    install -d -o scout -g scout -m 0700 /var/log/scout
COPY plugins/signal-scout/security/ /opt/scout/security/
COPY docker/signal-scout/config.yaml /opt/scout/config.yaml
ENV PYTHONPATH=/opt/scout PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --chmod=0555 docker/signal-scout/firewall.sh /usr/local/bin/scout-firewall
COPY --chmod=0555 docker/signal-scout/proxy.sh /usr/local/bin/scout-proxy
USER 10000:10000
CMD ["/usr/local/bin/scout-proxy"]
