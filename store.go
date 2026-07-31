package main

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	// Pure-Go SQLite: no cgo, so the binary cross-compiles and runs on a
	// distroless image. Do not swap this for mattn/go-sqlite3 without also
	// reworking the Dockerfile.
	_ "modernc.org/sqlite"
)

const (
	maxNoteLen = 2000
	// The client only ever sends the four-letter ids baked into index.html.
	// Anything else is a bug or someone poking at the API.
	plantIDPattern = `^[a-z]{4}$`
)

var plantID = regexp.MustCompile(plantIDPattern)

// Review is one plant's standing in the shortlist.
type Review struct {
	Verdict   string
	Note      string
	UpdatedBy string
	UpdatedAt time.Time
}

type Store struct{ db *sql.DB }

func openStore(path string) (*Store, error) {
	if dir := filepath.Dir(path); dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, fmt.Errorf("create %s: %w", dir, err)
		}
	}

	// WAL keeps readers from blocking the single writer, and busy_timeout
	// absorbs the latency spikes you get from a network block device.
	dsn := path + "?_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)&_pragma=foreign_keys(on)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	// SQLite takes one writer at a time; a small pool avoids pointless contention.
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)

	if _, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS reviews (
			plant_id   TEXT PRIMARY KEY,
			verdict    TEXT NOT NULL DEFAULT '',
			note       TEXT NOT NULL DEFAULT '',
			updated_by TEXT NOT NULL DEFAULT '',
			updated_at TEXT NOT NULL
		)`); err != nil {
		db.Close()
		return nil, fmt.Errorf("migrate: %w", err)
	}
	return &Store{db: db}, nil
}

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) Ping(ctx context.Context) error { return s.db.PingContext(ctx) }

// All returns every stamped plant. The shortlist is 42 rows at most, so there
// is no reason to paginate or filter server-side.
func (s *Store) All(ctx context.Context) (map[string]Review, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT plant_id, verdict, note, updated_by, updated_at FROM reviews`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := make(map[string]Review)
	for rows.Next() {
		var id, ts string
		var r Review
		if err := rows.Scan(&id, &r.Verdict, &r.Note, &r.UpdatedBy, &ts); err != nil {
			return nil, err
		}
		r.UpdatedAt, _ = time.Parse(time.RFC3339, ts)
		out[id] = r
	}
	return out, rows.Err()
}

// Put writes one plant's verdict and note, last-write-wins. Clearing both drops
// the row so the table only ever holds plants someone actually stamped.
func (s *Store) Put(ctx context.Context, id, verdict, note, by string) error {
	if !plantID.MatchString(id) {
		return fmt.Errorf("invalid plant id %q", id)
	}
	switch verdict {
	case "", "yes", "maybe", "no":
	default:
		return fmt.Errorf("invalid verdict %q", verdict)
	}
	note = strings.TrimSpace(note)
	if len(note) > maxNoteLen {
		note = note[:maxNoteLen]
	}

	if verdict == "" && note == "" {
		_, err := s.db.ExecContext(ctx, `DELETE FROM reviews WHERE plant_id = ?`, id)
		return err
	}

	_, err := s.db.ExecContext(ctx, `
		INSERT INTO reviews (plant_id, verdict, note, updated_by, updated_at)
		VALUES (?, ?, ?, ?, ?)
		ON CONFLICT(plant_id) DO UPDATE SET
			verdict    = excluded.verdict,
			note       = excluded.note,
			updated_by = excluded.updated_by,
			updated_at = excluded.updated_at`,
		id, verdict, note, by, time.Now().UTC().Format(time.RFC3339))
	return err
}
