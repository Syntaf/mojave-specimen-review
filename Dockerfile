# The site (HTML, images, fonts) is embedded in the binary, so the runtime image
# needs nothing but the binary itself. modernc.org/sqlite is pure Go, which is
# what lets CGO stay off and the final stage be distroless/static.
FROM golang:1.26-alpine AS build
WORKDIR /src

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/server .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/server /server

ENV ADDR=:8080 \
    DB_PATH=/data/mojave.db
EXPOSE 8080
USER nonroot:nonroot
ENTRYPOINT ["/server"]
