FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv-runtime
FROM --platform=$BUILDPLATFORM golang:1.27.3-alpine@sha256:b8c9b82affbbb82a020bbcf998dad2ba9516944ac7e883524642a481a9505e9c AS tunnel-builder
ARG TARGETARCH
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOFLAGS=-mod=mod GOOS=linux GOARCH=$TARGETARCH
WORKDIR /src/cloudflared
ADD --checksum=sha256:68d298c522aef45fe8412e0ccf8f21ff12a6ad768aa69cbc23e10eac688ac8c8 \
    https://github.com/cloudflare/cloudflared/archive/refs/tags/2026.10.3.tar.gz /tmp/cloudflared.tar.gz
RUN tar -xzf /tmp/cloudflared.tar.gz --strip-components=1 \
    && go get github.com/klauspost/compress@v1.18.7 github.com/go-jose/go-jose/v4@v4.1.6 \
    && go build -mod=mod -trimpath -buildvcs=false \
        -ldflags='-s -w -X main.Version=2026.10.3 -X main.BuildTime=2026-10-22' \
        -o /out/cloudflared ./cmd/cloudflared \
    && go version -m /out/cloudflared | grep -F 'github.com/klauspost/compress' | grep -F 'v1.18.7' \
    && go version -m /out/cloudflared | grep -F 'github.com/go-jose/go-jose/v4' | grep -F 'v4.1.6'

FROM docker.io/cloudflare/sandbox:0.12.14-python@sha256:226351e2bbb2ab1f97f921e3e5930d7eb908a8f79865aa04b9a41553b2260230 AS brotli-builder
USER root
RUN apt-get update && apt-get install -y --no-install-recommends gcc make cmake libc6-dev
ADD --checksum=sha256:98d35d11a78dc453aebb7d10c3748f3f73ff78c8267c0dc697b8631f67bf7e8f \
    https://github.com/google/brotli/archive/refs/tags/v1.2.1.tar.gz /tmp/brotli.tar.gz
COPY infra/brotli/build-package.sh /tmp/build-brotli.sh
RUN sh /tmp/build-brotli.sh /tmp/brotli.tar.gz

FROM docker.io/cloudflare/sandbox:0.12.14-python@sha256:226351e2bbb2ab1f97f921e3e5930d7eb908a8f79865aa04b9a41553b2260230

USER root
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends ffmpeg ghostscript poppler-utils \
    && rm -rf /var/lib/apt/lists/*
COPY --from=brotli-builder /out/libbrotli1_1.2.1-0bumpa1_amd64.deb /tmp/brotli.deb
RUN dpkg -i /tmp/brotli.deb && rm /tmp/brotli.deb \
    && python3 -c 'import ctypes; assert ctypes.CDLL("libbrotlidec.so.1").BrotliDecoderVersion() == 0x1002001'

COPY --from=uv-runtime /uv /usr/local/bin/uv
RUN uv --no-config python install --no-bin --install-dir /tmp/security-python 3.11.17 \
    && find /usr/local/python/lib/python3.11 -mindepth 1 -maxdepth 1 ! -name site-packages \
        -exec rm -rf '{}' + \
    && tar -C /tmp/security-python/cpython-3.11.17-linux-x86_64-gnu \
        --exclude='./lib/python3.11/site-packages' --exclude='./bin/pip*' \
        -cf /tmp/python-runtime.tar . \
    && tar -xf /tmp/python-runtime.tar -C /usr/local/python \
    && rm -rf /tmp/security-python /tmp/python-runtime.tar \
    && rm -f /usr/local/python/lib/python3.11/EXTERNALLY-MANAGED \
    && python3 -c 'import sys, IPython, numpy; assert sys.version_info[:3] == (3, 11, 17)'

RUN npm install --global npm@12.2.1 \
    && npm install --prefix /tmp/npm-security --omit=dev \
        --ignore-scripts --package-lock=false --no-audit --no-fund ip-address@10.7.4 undici@6.29.1 \
    && rm -rf /usr/local/lib/node_modules/npm/node_modules/ip-address \
    && cp -R /tmp/npm-security/node_modules/ip-address /usr/local/lib/node_modules/npm/node_modules/ \
    && rm -rf /usr/local/lib/node_modules/npm/node_modules/undici \
    && cp -R /tmp/npm-security/node_modules/undici /usr/local/lib/node_modules/npm/node_modules/ \
    && rm -rf /tmp/npm-security \
    && python3 -m pip install --no-cache-dir --upgrade \
        setuptools==84.2.1 wheel==0.48.0 urllib3==2.8.0 msgpack==1.2.3 \
    && rm -rf /usr/local/python/lib/python3.11/ensurepip \
    && npm --version | grep -Fx '12.2.1' \
    && node -e 'const {Address4}=require("/usr/local/lib/node_modules/npm/node_modules/ip-address"); new Address4("127.0.0.1")' \
    && python3 -m pip check \
    && python3 -c 'import msgpack; assert msgpack.__version__ == "1.2.3"'

COPY --from=tunnel-builder /out/cloudflared /usr/local/bin/cloudflared
RUN chmod 0755 /usr/local/bin/cloudflared \
    && cloudflared --version

# pip's current upstream bundle still includes vulnerable copies even when the
# global packages are fixed. Use its documented complete downstream wheel mode.
COPY infra/sandbox/pip-dependencies.in infra/sandbox/pip-dependencies.txt infra/sandbox/debundle-pip.py /tmp/
RUN python3 -m pip download --require-hashes --only-binary=:all: \
        -r /tmp/pip-dependencies.txt \
        --dest /usr/local/python/lib/python3.11/site-packages/pip/_vendor \
    && python3 /tmp/debundle-pip.py \
    && python3 -m pip --version \
    && python3 -m pip list --format=json >/dev/null \
    && python3 -m pip check \
    && python3 -m pip download --no-deps --only-binary=:all: --dest /tmp/pip-smoke six==1.17.0 \
    && python3 -m pip install --no-index --no-deps --target /tmp/pip-smoke-install /tmp/pip-smoke/*.whl \
    && PYTHONPATH=/tmp/pip-smoke-install python3 -c 'import six; assert six.__version__ == "1.17.0"' \
    && rm -rf /tmp/pip-smoke /tmp/pip-smoke-install /tmp/pip-dependencies.* /tmp/debundle-pip.py
