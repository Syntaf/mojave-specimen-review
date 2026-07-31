// Command mojave-specimen-review serves a shared plant shortlist for a Mojave
// front-yard planting. The whole site is baked into the binary; the only state
// that outlives a deploy is the SQLite database on the mounted volume.
package main

import (
	"context"
	"embed"
	"errors"
	"io/fs"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"
)

//go:embed web
var embedded embed.FS

type config struct {
	addr         string
	dbPath       string
	baseURL      string
	clientID     string
	clientSecret string
	sessionKey   string
	allowed      []string
}

func loadConfig() config {
	c := config{
		addr:         env("ADDR", ":8080"),
		dbPath:       env("DB_PATH", "data/mojave.db"),
		baseURL:      strings.TrimRight(env("BASE_URL", "http://localhost:8080"), "/"),
		clientID:     os.Getenv("GITHUB_CLIENT_ID"),
		clientSecret: os.Getenv("GITHUB_CLIENT_SECRET"),
		sessionKey:   os.Getenv("SESSION_KEY"),
	}
	for _, u := range strings.Split(os.Getenv("ALLOWED_USERS"), ",") {
		if u = strings.TrimSpace(u); u != "" {
			c.allowed = append(c.allowed, strings.ToLower(u))
		}
	}
	return c
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	cfg := loadConfig()

	store, err := openStore(cfg.dbPath)
	if err != nil {
		log.Fatalf("open database at %s: %v", cfg.dbPath, err)
	}
	defer store.Close()

	auth := newAuth(cfg)
	if !auth.enabled() {
		log.Print("GITHUB_CLIENT_ID/SECRET/SESSION_KEY not all set: running read-only, sign-in disabled")
	} else if len(cfg.allowed) == 0 {
		log.Print("ALLOWED_USERS is empty: any GitHub account will be able to stamp")
	}

	srv := &http.Server{
		Addr:              cfg.addr,
		Handler:           routes(store, auth),
		ReadHeaderTimeout: 10 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	// Shut down on SIGTERM so Kubernetes rollouts drain instead of cutting
	// connections, and so SQLite closes cleanly.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	go func() {
		log.Printf("listening on %s (db %s, base %s)", cfg.addr, cfg.dbPath, cfg.baseURL)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("serve: %v", err)
		}
	}()

	<-ctx.Done()
	log.Print("shutting down")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		log.Printf("graceful shutdown failed: %v", err)
	}
}

func routes(store *Store, auth *Auth) http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		if err := store.Ping(r.Context()); err != nil {
			http.Error(w, "database unavailable", http.StatusServiceUnavailable)
			return
		}
		w.Write([]byte("ok"))
	})

	api := &API{store: store, auth: auth}
	mux.HandleFunc("GET /api/state", api.getState)
	mux.HandleFunc("PUT /api/plants/{id}", api.putPlant)

	mux.HandleFunc("GET /auth/login", auth.login)
	mux.HandleFunc("GET /auth/callback", auth.callback)
	mux.HandleFunc("GET /auth/logout", auth.logout)

	site, err := fs.Sub(embedded, "web")
	if err != nil {
		log.Fatalf("embedded web/ missing: %v", err)
	}
	mux.Handle("GET /", cacheControl(http.FileServerFS(site)))

	return logRequests(mux)
}

// cacheControl lets fingerprinted assets sit in the browser cache forever while
// keeping index.html fresh, so a deploy is visible on the next reload.
func cacheControl(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasPrefix(r.URL.Path, "/images/"), strings.HasPrefix(r.URL.Path, "/fonts/"):
			w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
		default:
			w.Header().Set("Cache-Control", "no-cache")
		}
		next.ServeHTTP(w, r)
	})
}

func logRequests(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(rec, r)
		// Assets are the bulk of traffic and say nothing useful; skip them.
		if !strings.HasPrefix(r.URL.Path, "/images/") && !strings.HasPrefix(r.URL.Path, "/fonts/") {
			log.Printf("%s %s %d %s", r.Method, r.URL.Path, rec.status, time.Since(start).Round(time.Millisecond))
		}
	})
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (s *statusRecorder) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}
