# Build the Go agent worker.
FROM golang:1.22-bookworm AS build
WORKDIR /src
COPY services/agent/go.mod ./
RUN go mod download
COPY services/agent/ ./
RUN CGO_ENABLED=0 go build -o /out/agent .

# Runtime image needs git for shallow clones.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/agent /usr/local/bin/agent
ENV AGENT_PORT=8081
EXPOSE 8081
ENTRYPOINT ["/usr/local/bin/agent"]
