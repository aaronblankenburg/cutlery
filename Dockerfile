# Cutlery: the web app on top of the cutter image (ffmpeg, FilmCraft CLI, cutter, Python 3.12).
# Bump cutter: set CUTTER_IMAGE to the new tag and digest, rebuild, run the tests.
# NOTE: the stand-in support (cutter branch cutlery-support) is not in a cutter release yet. Until it is, build the
# cutter image from that branch and pass it:  docker build -t cutter:cutlery-dev ../cutter &&
#   docker build --build-arg CUTTER_IMAGE=cutter:cutlery-dev -t cutlery:dev .
ARG CUTTER_IMAGE=ghcr.io/aaronblankenburg/cutter:0.2.1@sha256:83043b620c13856d3306f2998abea642f5c18c1a6e06f33a88b6c769ce985c2d
FROM ${CUTTER_IMAGE}

COPY pyproject.toml README.md LICENSE /opt/cutlery/
COPY src /opt/cutlery/src
RUN pip install /opt/cutlery \
 && cutlery --version
COPY docker/entrypoint.sh /usr/local/bin/cutlery-entrypoint
RUN chmod 755 /usr/local/bin/cutlery-entrypoint

ENV CUTLERY_DATA_DIR=/data CUTLERY_WORK_DIR=/work CUTLERY_MODELS_DIR=/models HF_HOME=/models \
    NUMBA_CACHE_DIR=/models/numba-cache TMPDIR=/work/tmp
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 CMD ["cutlery", "healthcheck"]
ENTRYPOINT ["cutlery-entrypoint"]
CMD ["serve", "--host", "0.0.0.0"]
