# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY tapo_cli ./tapo_cli

RUN uv build --wheel --out-dir /dist \
    && uv export --no-dev --no-emit-project --no-hashes \
        --output-file /dist/constraints.txt

FROM docker.io/library/python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/tapo

COPY --from=build /dist /tmp/dist
RUN pip install --no-cache-dir \
        --constraint /tmp/dist/constraints.txt \
        /tmp/dist/*.whl \
    && rm -rf /tmp/dist

RUN useradd --create-home --uid 1000 tapo \
    && install -d -o tapo -g tapo /home/tapo/.config/tapo-cli

USER tapo

# mount ~/.config/tapo-cli here to reuse a session (see README)
VOLUME ["/home/tapo/.config/tapo-cli"]

ENTRYPOINT ["tapo-cli"]
CMD ["--help"]
