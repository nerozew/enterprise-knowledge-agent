FROM golang:1.24.9-alpine3.22 AS builder

ARG MINIO_REF=RELEASE.2025-10-15T17-29-55Z
RUN apk add --no-cache git
RUN git clone --filter=blob:none --branch "${MINIO_REF}" --depth 1 https://github.com/minio/minio.git /src
WORKDIR /src
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/minio

FROM alpine:3.22.2
RUN apk add --no-cache ca-certificates wget \
    && addgroup -S minio \
    && adduser -S -G minio minio \
    && mkdir -p /data \
    && chown minio:minio /data
COPY --from=builder /out/minio /usr/local/bin/minio
USER minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
