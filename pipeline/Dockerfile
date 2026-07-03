FROM golang:1.24-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN go build -o pipeline ./cmd/pipeline/main.go

FROM alpine:3.20
RUN apk add --no-cache ca-certificates ffmpeg
WORKDIR /app
COPY --from=builder /app/pipeline .
ENTRYPOINT ["./pipeline"]
