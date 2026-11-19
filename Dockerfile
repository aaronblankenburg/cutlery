# Cutlery: the web app on top of the cutter image (ffmpeg, FilmCraft CLI, cutter, Python 3.12).
# Bump cutter: set CUTTER_IMAGE to the new tag and digest, rebuild, run the tests.
# To try unreleased cutter changes, build cutter's image and pass it:
#   docker build -t cutter:main ../cutter && docker build --build-arg CUTTER_IMAGE=cutter:main -t cutlery:dev .
ARG CLAUDE_CODE_VERSION=2.1.317
ARG CUTTER_IMAGE=ghcr.io/aaronblankenburg/cutter:0.8.0@sha256:65ea6d77195665e97892a1d16f77b66006b4e5de9499020cd18533433d72a262
# Claude Code at a fixed version; the npm package brings the native binary, Node is not needed at run time.
FROM node:22-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee AS claude
ARG CLAUDE_CODE_VERSION
RUN npm install -g --no-fund --no-audit "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && cp "$(readlink -f "$(command -v claude)")" /claude \
 && /claude --version

FROM ${CUTTER_IMAGE}

COPY pyproject.toml README.md LICENSE /opt/cutlery/
COPY src /opt/cutlery/src
RUN pip install /opt/cutlery \
 && cutlery --version
COPY --from=claude /claude /usr/local/bin/claude
COPY docker/entrypoint.sh /usr/local/bin/cutlery-entrypoint
COPY docker/job.sh /usr/local/bin/cutlery-job
RUN chmod 755 /usr/local/bin/cutlery-entrypoint /usr/local/bin/cutlery-job \
 && claude --version

ENV CUTLERY_DATA_DIR=/data CUTLERY_WORK_DIR=/work CUTLERY_MODELS_DIR=/models HF_HOME=/models \
    NUMBA_CACHE_DIR=/models/numba-cache TMPDIR=/work/tmp
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 CMD ["cutlery", "healthcheck"]
ENTRYPOINT ["cutlery-entrypoint"]
CMD ["serve", "--host", "0.0.0.0"]
