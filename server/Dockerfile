# syntax=docker/dockerfile:1.7
# Memories Rust server image for linux/amd64 and linux/arm64.
# Build from the server/ folder: docker build -t memories-rust server

FROM rust:1.100.0-slim-trixie@sha256:e9d95b74296f7126a83ae3d041aeaba1ff4c478f13abc5cc034fcf7c131c3d4a AS build
WORKDIR /src
# Use the toolchain of the base image. rust-toolchain.toml names the same version.
ENV RUSTUP_TOOLCHAIN=1.100.0
COPY . .
ARG TARGETARCH
RUN --mount=type=cache,id=cargo-registry-${TARGETARCH},target=/usr/local/cargo/registry \
    --mount=type=cache,id=cargo-target-${TARGETARCH},target=/src/target \
    cargo build --release --locked --bin memories-server && \
    cp target/release/memories-server /memories-server

FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 AS runtime

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY --from=build /memories-server /usr/local/bin/memories-server
# The web UI files, from the build context "webui" (docker build --build-context webui=webui server).
COPY --from=webui . /app/webui
# The compose files of older installs run "python -c ..." for the health check.
RUN ln -s memories-server /usr/local/bin/python && \
    ln -s memories-server /usr/local/bin/python3

# Same user and UID (999) as the Python image, so that an existing /data volume stays writable.
RUN groupadd -r -g 999 memories && \
    useradd -r -u 999 -g 999 -m -s /bin/false memories && \
    mkdir -p /data/backups /data/model-cache && \
    chown -R memories:memories /data

ENV DATA_DIR=/data \
    UI_DIR=/app/webui \
    MODEL_CACHE_DIR=/data/model-cache

USER memories
WORKDIR /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD ["memories-server", "healthcheck"]

ENTRYPOINT ["memories-server"]
CMD ["serve"]
